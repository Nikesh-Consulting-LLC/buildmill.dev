"""US-31.2: the lease is the manager's number, and a dead agent is noticed.

Endpoint + SQL-shape level with a fake connection: the live-DB behaviour
(claim surviving past 15 minutes under a 60-minute config) is exercised at
UAT; here we pin the SQL that produces it, the config validation bounds,
the bundle's lease, and the distinct staleness event note.
"""

import uuid

import pytest

from app import db

WORKER_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, script=None):
        self.calls = []
        self.script = script or (lambda q, p: FakeCursor())

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self.script(query, params)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ------------------------------------------------------------- claim lease


def test_claim_sql_reads_configured_lease(monkeypatch):
    """claim_run's expiry must come from runner_config.max_run_minutes when
    set, falling back to the type default — in ONE statement, so a lost race
    can't split the two."""
    conn = FakeConn(lambda q, p: FakeCursor(None))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.claim_run(object(), RUN_ID, {"id": WORKER_ID, "org_id": ORG_ID, "type": "autonomous"})
    q, params = next(
        (q, p) for q, p in conn.calls if "update public.runs" in q
    )
    assert "rc.max_run_minutes || ' minutes'" in q
    assert "coalesce" in q
    # US-39.2: the autonomous default is 120 minutes (15 was far too short for
    # real coding work), and the claim now also carries the total ceiling.
    assert "120 minutes" in params  # the fallback still rides along
    assert "1440 minutes" in params  # US-39.2's bound on per-story x N
    # The lease is multiplied by the work the run carries.
    assert "run_work_units" in q


def test_extend_claim_honors_configured_lease(monkeypatch):
    """The first heartbeat must not shrink a configured 60-minute claim
    back to the type default."""
    conn = FakeConn(lambda q, p: FakeCursor({"id": RUN_ID, "claim_expires_at": None}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.extend_claim(object(), RUN_ID, WORKER_ID)
    q, _ = next((q, p) for q, p in conn.calls if "update public.runs" in q)
    assert "rc.max_run_minutes" in q and "coalesce" in q


def test_extend_claim_falls_back_to_the_same_120_minutes_claim_run_uses(monkeypatch):
    """US-57.16: the fallback used to be a SQL literal — '15 minutes' for an
    autonomous worker — independent of, and silently shorter than, claim_run's
    120-minute default. A run's first heartbeat was quietly shrinking its
    120-minute claim down to 15. Both must now agree."""

    def script(q, p):
        if "select type from public.workers" in q:
            return FakeCursor({"type": "autonomous"})
        return FakeCursor({"id": RUN_ID, "claim_expires_at": None})

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.extend_claim(object(), RUN_ID, WORKER_ID)
    _, params = next((q, p) for q, p in conn.calls if "update public.runs" in q)
    assert "120 minutes" in params


def test_extend_claim_still_gives_a_human_worker_a_day(monkeypatch):
    def script(q, p):
        if "select type from public.workers" in q:
            return FakeCursor({"type": "human"})
        return FakeCursor({"id": RUN_ID, "claim_expires_at": None})

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.extend_claim(object(), RUN_ID, WORKER_ID)
    _, params = next((q, p) for q, p in conn.calls if "update public.runs" in q)
    assert "24 hours" in params


def test_worker_lease_seconds_prefers_config(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor({"max_run_minutes": 60}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    assert db.worker_lease_seconds(object(), WORKER_ID, "autonomous") == 3600


def test_worker_lease_seconds_defaults_by_type(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor(None))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    assert db.worker_lease_seconds(object(), WORKER_ID, "autonomous") == 7200
    assert db.worker_lease_seconds(object(), WORKER_ID, "human") == 86400


# ---------------------------------------------------- staleness sweep


def test_stale_sweep_targets_only_configured_runners_and_says_so(monkeypatch):
    """The sweep joins runner_config (external MCP workers keep lease-only
    reclaim) and its event note must NOT read as a lease expiry — the
    manager needs to tell a slow agent from a dead one."""
    stale_row = {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": str(uuid.uuid4()),
        "kind": "plan",
        # US-59.4: no captured session id on this run — the CASE in the
        # real SQL would land 'queued', not 'paused'; the fake cursor
        # doesn't evaluate SQL, so the row states the same outcome directly.
        "status": "queued",
        "worker_name": "pod-001-1",
        "silent_seconds": 663,
        "worker_id": str(uuid.uuid4()),
    }

    def script(q, p):
        if "with stale as" in q:
            return FakeCursor(rows=[stale_row])
        return FakeCursor()

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    n = db.requeue_stale_heartbeats(object())
    assert n == 1
    sweep_q = conn.calls[0][0]
    assert "join public.runner_config rc" in sweep_q
    assert "pushed_head_sha is null" in sweep_q
    event_q, event_p = next(
        (q, p) for q, p in conn.calls if "issue_events" in q
    )
    # US-59.4: the event type is now a parameter too (dynamic: 'run-paused'
    # vs 'claim-expired'), so payload moved from index 2 to index 3.
    assert event_p[2] == "claim-expired"
    payload = event_p[3]
    assert "agent stopped reporting" in payload
    assert "lease expired" not in payload
    assert '"silent_seconds": 663' in payload


def test_expired_sweep_also_runs_stale_sweep(monkeypatch):
    called = []
    monkeypatch.setattr(
        db, "requeue_stale_heartbeats", lambda s: called.append(True) or 0
    )
    conn = FakeConn(lambda q, p: FakeCursor(rows=[]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.requeue_expired_claims(object())
    assert called == [True]


# ------------------------------------------------------- runner-side math


def test_timeout_from_lease_is_strictly_below_the_lease():
    import sys
    from pathlib import Path

    sys.path.insert(
        0, str(Path(__file__).resolve().parents[2] / "runner")
    )
    from supervisor.workloop import timeout_from_lease

    for lease in (60, 120, 900, 3600, 86400):
        t = timeout_from_lease(lease)
        assert t is not None and t < lease, (lease, t)
    assert timeout_from_lease(3600) == 3240  # 90%, headroom not binding
    assert timeout_from_lease(None) is None  # older server: caller keeps 1200
    assert timeout_from_lease("nonsense") is None
