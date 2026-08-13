"""US-31.5: an agent stops retrying an item it cannot finish.

The central design point these tests defend: attempts come from an
append-only log, NOT from `runs`. requeue_expired_claims mutates the run row
back to `queued` and nulls worker_id, so the four-lap loop of 2026-07-26 left
zero failed runs behind — a counter over `runs` would have counted zero.
"""

import uuid

import pytest

from app import db

WORKER_ID = str(uuid.uuid4())
OTHER_WORKER = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, script):
        self.calls = []
        self.script = script

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self.script(query, params)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _conn(monkeypatch, script):
    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


def _attempt_inserts(conn):
    return [
        (q, p) for q, p in conn.calls if "insert into public.run_attempts" in q
    ]


# ------------------------------------------- the two invisible requeue paths


def test_lease_expiry_records_an_attempt_with_the_worker_id(monkeypatch):
    """THE case this story exists for: the run row goes back to queued and
    loses its worker, so the attempt must be logged separately."""
    expired = {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": ISSUE_ID,
        "kind": "plan",
        "worker_name": "pod-001-1",
        "worker_id": WORKER_ID,
        "held_minutes": 15,
    }

    def script(q, p):
        if "with expired as" in q:
            return FakeCursor(rows=[expired])
        return FakeCursor(rows=[])

    conn = _conn(monkeypatch, script)
    monkeypatch.setattr(db, "requeue_stale_heartbeats", lambda s: 0)
    db.requeue_expired_claims(object())
    inserts = _attempt_inserts(conn)
    assert len(inserts) == 1
    params = inserts[0][1]
    assert params[1] == ISSUE_ID
    assert params[3] == WORKER_ID  # by id, never by name
    assert params[5] == "lease-expired"


def test_stale_heartbeat_records_an_attempt(monkeypatch):
    stale = {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": ISSUE_ID,
        "kind": "code",
        # US-59.4: no captured session id — lands 'queued' (a plain reclaim),
        # same as this test's original intent.
        "status": "queued",
        "worker_name": "pod-001-2",
        "worker_id": WORKER_ID,
        "silent_seconds": 663,
    }

    def script(q, p):
        if "with stale as" in q:
            return FakeCursor(rows=[stale])
        return FakeCursor(rows=[])

    conn = _conn(monkeypatch, script)
    db.requeue_stale_heartbeats(object())
    inserts = _attempt_inserts(conn)
    assert len(inserts) == 1
    assert inserts[0][1][5] == "heartbeat-stale"


def test_failed_run_records_an_attempt(monkeypatch):
    def script(q, p):
        if "update public.runs" in q and "returning" in q:
            return FakeCursor(
                {
                    "id": RUN_ID,
                    "org_id": ORG_ID,
                    "issue_id": ISSUE_ID,
                    "kind": "code",
                    "input_context": {},
                    "worker_id": WORKER_ID,
                }
            )
        return FakeCursor(None, rows=[])

    conn = _conn(monkeypatch, script)
    db.complete_run(
        object(), RUN_ID, "failed", "out", None, None, None, "it died"
    )
    inserts = _attempt_inserts(conn)
    assert len(inserts) == 1
    assert inserts[0][1][3] == WORKER_ID
    assert inserts[0][1][5] == "failed"


def test_succeeded_run_records_no_attempt(monkeypatch):
    def script(q, p):
        if "update public.runs" in q and "returning" in q:
            return FakeCursor(
                {
                    "id": RUN_ID,
                    "org_id": ORG_ID,
                    "issue_id": ISSUE_ID,
                    "kind": "prd",
                    "input_context": {},
                    "worker_id": WORKER_ID,
                }
            )
        # The success path consults the artifact version counter, which
        # always returns a row in reality.
        return FakeCursor({"v": 1}, rows=[])

    conn = _conn(monkeypatch, script)
    db.complete_run(object(), RUN_ID, "succeeded", "out", None, None, None, None, prd="# P")
    assert _attempt_inserts(conn) == []


def test_cancelled_never_consumes_an_attempt(monkeypatch):
    """A cancelled run is the manager withdrawing work — counting it would
    punish an agent for a human's change of mind, and cancelling repeatedly
    could block an item."""
    def script(q, p):
        if "update public.runs" in q and "returning" in q:
            return FakeCursor(
                {
                    "id": RUN_ID,
                    "org_id": ORG_ID,
                    "issue_id": ISSUE_ID,
                    "kind": "code",
                    "input_context": {},
                    "worker_id": WORKER_ID,
                }
            )
        return FakeCursor(None, rows=[])

    conn = _conn(monkeypatch, script)
    # 'cancelled' is not 'failed', so the recorder must not fire.
    db.complete_run(object(), RUN_ID, "cancelled", None, None, None, None, None)
    assert _attempt_inserts(conn) == []


def test_attempt_recorder_skips_issueless_runs():
    conn = FakeConn(lambda q, p: FakeCursor())
    db.record_run_attempt(conn, ORG_ID, None, RUN_ID, WORKER_ID, "deploy", "failed")
    assert conn.calls == []  # deploy/release runs are not item-capped


# ------------------------------------------------------ the two gates


def test_pool_excludes_exhausted_and_blocked_items(monkeypatch):
    conn = _conn(monkeypatch, lambda q, p: FakeCursor(rows=[]))
    db.list_worker_pool(
        object(), {"id": WORKER_ID, "org_id": ORG_ID, "type": "autonomous"}
    )
    pool_q = next(q for q, _ in conn.calls if "queue_rank" in q)
    assert "worker_exhausted_on_issue" in pool_q
    assert "attempts_blocked_at is not null" in pool_q


def test_claim_refusal_prefers_attempt_reasons_over_grants(monkeypatch):
    """An exhausted agent WITH a valid grant is the case the manager hit —
    the refusal must say "you have tried enough", not "you lack a grant"."""
    _conn(
        monkeypatch,
        lambda q, p: FakeCursor(
            {
                "allowed": True,
                "on_project": True,
                "kind": "code",
                "issue_id": ISSUE_ID,
                "exhausted": True,
                "blocked": False,
                "kind_checked": True,
            }
        ),
    )
    reason = db.worker_run_refusal(object(), WORKER_ID, RUN_ID)
    assert reason is not None and "attempt limit" in reason

    _conn(
        monkeypatch,
        lambda q, p: FakeCursor(
            {
                "allowed": True,
                "on_project": True,
                "kind": "code",
                "issue_id": ISSUE_ID,
                "exhausted": False,
                "blocked": True,
                "kind_checked": True,
            }
        ),
    )
    reason = db.worker_run_refusal(object(), WORKER_ID, RUN_ID)
    assert reason is not None and "exhausted its attempts" in reason


def test_healthy_run_still_claimable(monkeypatch):
    _conn(
        monkeypatch,
        lambda q, p: FakeCursor(
            {
                "allowed": True,
                "on_project": True,
                "kind": "code",
                "issue_id": ISSUE_ID,
                "exhausted": False,
                "blocked": False,
                "kind_checked": True,
            }
        ),
    )
    assert db.worker_run_refusal(object(), WORKER_ID, RUN_ID) is None


# ------------------------------------------------------ manager release


def test_release_clears_block_and_history(monkeypatch):
    def script(q, p):
        if "update public.issues set attempts_blocked_at = null" in q:
            return FakeCursor({"id": ISSUE_ID, "org_id": ORG_ID})
        if "delete from public.run_attempts" in q:
            return FakeCursor(rows=[{"id": 1}, {"id": 2}, {"id": 3}])
        return FakeCursor()

    conn = _conn(monkeypatch, script)
    assert db.release_attempt_block(object(), ISSUE_ID, actor="me@example.com") is True
    ev = next((q, p) for q, p in conn.calls if "attempts-released" in q)
    assert '"cleared": 3' in ev[1][2]


def test_release_on_unblocked_item_is_a_conflict(monkeypatch):
    _conn(monkeypatch, lambda q, p: FakeCursor(None))
    assert db.release_attempt_block(object(), ISSUE_ID) is False
