"""US-87.6 / US-87.7: the connection pool's contract, and the throttled
worker heartbeat.

These are unit tests over `db.py` with `_connect` faked — no database, no
network. What they pin is behavior that is invisible until it is wrong in
production: a heartbeat that stops being written at all, or a pooled
connection that carries one request's session state into another's.
"""

import time

import pytest

from app import db


class FakeCursor:
    def __init__(self, sink):
        self.sink = sink

    def execute(self, sql, params=None):
        self.sink.append((" ".join(sql.split()), params))
        return self

    def executemany(self, sql, seq):
        self.sink.append((" ".join(sql.split()), list(seq)))
        return self

    def fetchone(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    """Records every statement, so a test can assert what was (not) written."""

    def __init__(self, sink, row=None, fail=False):
        self.sink = sink
        self._row = row
        self._fail = fail
        self.commits = 0

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        # Only the presence UPDATE fails; the auth SELECT must still work, or
        # the test would be proving the wrong thing.
        if self._fail and flat.lower().startswith("update"):
            raise RuntimeError("write failed")
        self.sink.append((flat, params))
        return _Result(self._row)

    def cursor(self):
        return FakeCursor(self.sink)

    def rollback(self):
        self.sink.append(("ROLLBACK", None))

    def commit(self):
        self.commits += 1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


@pytest.fixture()
def fake_db(monkeypatch):
    """Replace `_connect` and clear the module-level throttle between tests —
    it is process-global by design, which would otherwise leak across tests."""
    sink: list = []
    state: dict = {"row": None, "fail": False}

    def _connect(_settings):
        return FakeConn(sink, row=state["row"], fail=state["fail"])

    monkeypatch.setattr(db, "_connect", _connect)
    db._last_seen_written.clear()
    with db._LOG_BUFFER_LOCK:
        db._LOG_BUFFER.clear()
    yield sink, state
    db._last_seen_written.clear()
    with db._LOG_BUFFER_LOCK:
        db._LOG_BUFFER.clear()


# ---------------------------------------------------------------------------
# US-87.7 — presence is throttled, and authentication does not write
# ---------------------------------------------------------------------------


def test_worker_auth_reads_and_does_not_update(fake_db, settings_override):
    sink, state = fake_db
    state["row"] = {"id": "w1", "org_id": "o1"}

    db.get_worker_by_token(settings_override, "tok")

    statements = [s for s, _ in sink]
    assert any(s.lower().startswith("select") for s in statements), statements
    # The auth lookup itself must never be an UPDATE — that was the 940,000
    # writes this story removes.
    assert not any(
        "update public.workers set last_seen_at" in s.lower()
        and "where token_hash" in s.lower()
        for s in statements
    ), statements


def test_presence_is_written_once_per_interval(fake_db, settings_override):
    sink, state = fake_db
    state["row"] = {"id": "w1", "org_id": "o1"}

    for _ in range(5):
        db.get_worker_by_token(settings_override, "tok")

    writes = [s for s, _ in sink if "set last_seen_at" in s.lower()]
    assert len(writes) == 1, f"expected one presence write, got {len(writes)}"


def test_presence_is_written_again_after_the_interval(
    fake_db, settings_override, monkeypatch
):
    sink, state = fake_db
    state["row"] = {"id": "w1", "org_id": "o1"}

    db.get_worker_by_token(settings_override, "tok")
    # Move the clock past the interval rather than sleeping through it.
    real = time.monotonic
    monkeypatch.setattr(
        db.time, "monotonic", lambda: real() + db.LAST_SEEN_INTERVAL_S + 1
    )
    db.get_worker_by_token(settings_override, "tok")

    writes = [s for s, _ in sink if "set last_seen_at" in s.lower()]
    assert len(writes) == 2, f"expected a second presence write, got {len(writes)}"


def test_two_workers_do_not_share_a_throttle(fake_db, settings_override):
    sink, state = fake_db
    state["row"] = {"id": "w1"}
    db.get_worker_by_token(settings_override, "tok-a")
    state["row"] = {"id": "w2"}
    db.get_worker_by_token(settings_override, "tok-b")

    writes = [p for s, p in sink if "set last_seen_at" in s.lower()]
    assert writes == [("w1",), ("w2",)], writes


def test_a_failed_presence_write_retries_rather_than_waiting_out_the_interval(
    fake_db, settings_override
):
    """AC4: a lost or failed mark must never leave a stale-forever timestamp."""
    sink, state = fake_db
    state["row"] = {"id": "w1"}
    state["fail"] = True

    db.get_worker_by_token(settings_override, "tok")  # write raises, swallowed
    assert "w1" not in db._last_seen_written

    state["fail"] = False
    db.get_worker_by_token(settings_override, "tok")
    writes = [s for s, _ in sink if "set last_seen_at" in s.lower()]
    assert len(writes) == 1, "the retry after a failed write did not land"


def test_presence_failure_never_fails_authentication(fake_db, settings_override):
    sink, state = fake_db
    state["row"] = {"id": "w1", "org_id": "o1"}
    state["fail"] = True
    # Must return the worker, not raise: a presence write is not an auth step.
    assert db.get_worker_by_token(settings_override, "tok") is not None


def test_an_unknown_token_records_no_presence(fake_db, settings_override):
    sink, state = fake_db
    state["row"] = None
    assert db.get_worker_by_token(settings_override, "nope") is None
    assert not [s for s, _ in sink if "set last_seen_at" in s.lower()]


# ---------------------------------------------------------------------------
# US-87.6 — the request log batches instead of opening a connection per row
# ---------------------------------------------------------------------------


def test_request_log_buffers_until_the_batch_is_full(fake_db, settings_override):
    sink, _ = fake_db
    for i in range(db._LOG_BATCH_SIZE - 1):
        db.record_api_request(settings_override, f"/r{i}", "GET", 200, 5, 1)
    assert sink == [], "a buffered request row must not touch the database"

    db.record_api_request(settings_override, "/last", "GET", 200, 5, 1)
    inserts = [s for s, _ in sink if "insert into public.api_request_log" in s]
    assert len(inserts) == 1, "the full batch should be one insert, not many"
    _, rows = next(
        (s, p) for s, p in sink if "insert into public.api_request_log" in s
    )
    assert len(rows) == db._LOG_BATCH_SIZE


def test_flush_drains_a_partial_batch(fake_db, settings_override):
    sink, _ = fake_db
    db.record_api_request(settings_override, "/one", "GET", 200, 5, 1)
    assert sink == []
    db.flush_api_request_log(settings_override)
    inserts = [p for s, p in sink if "insert into public.api_request_log" in s]
    assert len(inserts) == 1 and len(inserts[0]) == 1


def test_flushing_an_empty_buffer_touches_nothing(fake_db, settings_override):
    sink, _ = fake_db
    db.flush_api_request_log(settings_override)
    assert sink == []


# ---------------------------------------------------------------------------
# US-87.6 AC3 — a pooled connection is scrubbed before reuse
# ---------------------------------------------------------------------------


def test_pool_reset_discards_session_state():
    """The reset hook is what stops one request's `search_path`, `role`, temp
    tables or prepared statements reaching the next caller. If this stops
    running, the leak is silent and cross-request."""
    from app import pool as pool_module

    sink: list = []
    conn = FakeConn(sink)
    pool_module._reset(conn)

    statements = [s.upper() for s, _ in sink]
    assert "DISCARD ALL" in statements, statements


def test_pool_kwargs_disable_prepared_statements(monkeypatch, settings_override):
    """psycopg3 auto-PREPAREs a query after a few executions on the same
    connection. Short-lived connections never got there; pooled ones do — and
    behind a transaction-mode pooler a prepared statement belongs to a server
    connection the next transaction may not get. Turning it off is what keeps
    that failure from appearing at random under load."""
    from app import pool as pool_module

    captured: dict = {}

    class FakePool:
        # pool.py reads `ConnectionPool.check_connection` off the class.
        check_connection = staticmethod(lambda conn: None)

        def __init__(self, **kwargs):
            captured.update(kwargs)

        def open(self):
            pass

    monkeypatch.setattr(pool_module, "ConnectionPool", FakePool)
    pool_module._POOLS.clear()
    try:
        pool_module.pool_for(settings_override)
        assert captured["kwargs"]["prepare_threshold"] is None
        # And the guarantees the old `_connect` set by hand survive.
        assert captured["kwargs"]["options"] == "-c statement_timeout=15000"
        assert captured["kwargs"]["connect_timeout"] == 5
        assert captured["reset"] is pool_module._reset
    finally:
        pool_module._POOLS.clear()
