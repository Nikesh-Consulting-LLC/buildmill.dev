"""US-87.6: one Postgres connection pool for the whole API process.

Before this, every database helper called ``psycopg.connect()`` directly —
214 call sites in ``db.py`` alone, plus its own copy of ``_connect`` in
``deploy.py``, ``notify.py``, ``agent_provision.py`` and
``workspace_prep.py``. Each opened a connection, ran one statement,
committed and closed. A request handler calling three helpers paid three
connection handshakes to Supabase before doing any work, and the database
showed it: ``SELECT * FROM pgbouncer.get_auth($1)`` at 143,388 calls over
six weeks on prod, a statement that exists only because connections keep
being established.

Three things here are load-bearing and easy to get wrong:

**Prepared statements are disabled.** psycopg3 automatically PREPAREs a
query after it has been executed a few times on the same connection.
Short-lived connections never reached that threshold, so it never mattered.
Pooled connections are long-lived and absolutely do — and this API reaches
Postgres through Supabase's pooler (that ``pgbouncer.get_auth`` count is
the proof). In transaction pooling mode a prepared statement belongs to a
server connection the next transaction may not get, which surfaces as
"prepared statement ... already exists" on random requests under load.
``prepare_threshold=None`` turns the feature off. It costs a little
per-query planning time and removes the whole class of failure; do not
"optimize" it back on without knowing which pooling mode the deployment's
DATABASE_URL points at.

**Session state cannot leak between requests.** ``reset`` below runs on
every return to the pool: rollback, then ``DISCARD ALL`` to drop temp
tables, prepared statements, cursors and any ``SET`` a handler left behind.
A pooled connection carrying one request's ``search_path`` or ``role`` into
another's is the defect pooling most easily introduces (us-87.6 AC3), and
it would be a security bug, not a performance one.

**The pool is sized against a shared budget.** Supabase counts connections
across everything that dials it — this API, the web app's server
components, and any direct session. ``max_size`` defaults deliberately
low and is configurable, because exhausting the database's connections is a
worse outage than the churn this replaces.
"""

from __future__ import annotations

import logging
import threading

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import Settings

logger = logging.getLogger(__name__)

# One pool per distinct DATABASE_URL. Keyed rather than global so a test's
# fake settings can never hand back a connection to the real database.
_POOLS: dict[str, ConnectionPool] = {}
_LOCK = threading.Lock()


def _reset(conn: Connection) -> None:
    """Scrub a connection on its way back to the pool.

    psycopg_pool already rolls back an open transaction; ``DISCARD ALL``
    is what guarantees nothing else survives — see the module docstring.
    """
    try:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DISCARD ALL")
        conn.commit()
    except Exception:  # noqa: BLE001 — a connection that cannot be reset is
        # closed by the pool rather than handed to the next caller.
        logger.debug("pooled connection reset failed; discarding", exc_info=True)
        raise


def pool_for(settings: Settings) -> ConnectionPool:
    """The process-wide pool for ``settings.database_url``, opened on first use."""
    url = settings.database_url
    pool = _POOLS.get(url)
    if pool is not None:
        return pool
    with _LOCK:
        pool = _POOLS.get(url)
        if pool is None:
            pool = ConnectionPool(
                conninfo=url,
                min_size=settings.db_pool_min_size,
                max_size=settings.db_pool_max_size,
                # How long a caller waits for a free slot before giving up.
                # Bounded so a saturated pool answers with an error rather
                # than hanging a request forever.
                timeout=settings.db_pool_timeout_s,
                # Recycle connections periodically: a pooler or a network
                # middlebox will drop a long-idle TCP session, and a
                # connection that dies in a caller's hands is a 500 the
                # caller did nothing to deserve.
                max_lifetime=settings.db_pool_max_lifetime_s,
                max_idle=settings.db_pool_max_idle_s,
                reset=_reset,
                # Every connection keeps exactly what `_connect` used to set
                # by hand, so the 214 call sites behave identically.
                kwargs={
                    "row_factory": dict_row,
                    "connect_timeout": 5,
                    "options": "-c statement_timeout=15000",
                    "prepare_threshold": None,
                },
                name="factory-api",
                open=False,
                # Do not block process start on the database being reachable;
                # the first caller waits (up to `timeout`) instead.
                check=ConnectionPool.check_connection,
            )
            pool.open()
            _POOLS[url] = pool
    return pool


def close_all() -> None:
    """Close every pool. Called from the API's lifespan shutdown."""
    with _LOCK:
        for url, pool in list(_POOLS.items()):
            try:
                pool.close()
            except Exception:  # noqa: BLE001 — shutdown must not raise
                logger.debug("pool close failed", exc_info=True)
            _POOLS.pop(url, None)


def stats() -> dict[str, dict[str, int]]:
    """Per-pool counters, for diagnosing saturation. Keyed by a redacted
    label — never the URL, which carries the password."""
    out: dict[str, dict[str, int]] = {}
    for i, pool in enumerate(_POOLS.values()):
        out[f"pool-{i}"] = dict(pool.get_stats())
    return out
