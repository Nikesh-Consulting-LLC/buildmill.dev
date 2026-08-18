"""Phase 59: a run pauses instead of failing, and resumes instead of
restarting — the core db.py mechanics (us-59.3/59.4/59.7).

Endpoint-level coverage lives in test_worker_pool.py and test_factory_mcp.py;
this file pins the db-layer decisions those endpoints depend on: the
resume-attempt cap, the atomic worker-scoped resume-claim, and the
abandon-is-scoped-to-parked-statuses guard.
"""

from __future__ import annotations

import json

from app import db


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class ScriptConn:
    """Routes each `execute` to a caller-supplied `script(query, params)` that
    returns a FakeCursor. Records every call for assertions."""

    def __init__(self, script):
        self.script = script
        self.calls: list[tuple[str, tuple]] = []
        self.committed = False

    def execute(self, query, params=None):
        q = " ".join(query.split())
        self.calls.append((q, params or ()))
        return self.script(q, params or ())

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _install(monkeypatch, conn):
    monkeypatch.setattr(db, "_connect", lambda settings: conn)


RUN_ID = "11111111-1111-4111-8111-111111111111"
ORG_ID = "org-1"
ISSUE_ID = "issue-1"
WORKER_ID = "22222222-2222-4222-8222-222222222222"


def _running_row(**over):
    row = {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": ISSUE_ID,
        "kind": "code",
        "worker_id": WORKER_ID,
        "resume_attempts": 0,
    }
    row.update(over)
    return row


def test_pause_run_lands_paused_and_releases_the_claim(monkeypatch):
    """US-59.3: under the cap, a turn-limit hit becomes `paused` — the claim
    releases (checked via the update's SQL text) but `worker_id` is never
    nulled, since only that worker can resume it (worker affinity)."""
    monkeypatch.setattr(
        db, "get_runner_config", lambda s, wid: {"autonomy_policy": {}}
    )

    def script(q, p):
        if q.startswith("select id, org_id, issue_id, kind, worker_id, resume_attempts"):
            return FakeCursor(_running_row())
        if q.startswith("update public.runs"):
            # us-119.1 AC2: the update returns the row it landed on.
            return FakeCursor({"id": RUN_ID})
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    accepted, landed, attempts, cap = db.pause_run(
        object(),
        RUN_ID,
        reason="turn_limit",
        claude_session_id="sess-1",
        stdout="log",
        error="hit its turn ceiling",
        worker_name="pod-001-1",
    )

    assert (accepted, landed, attempts, cap) == (True, "paused", 1, db.DEFAULT_MAX_RESUME_ATTEMPTS)
    update_q, update_p = next(
        (q, p) for q, p in conn.calls if q.startswith("update public.runs")
    )
    assert "status = 'paused'" in update_q
    assert "claimed_at = null, claim_expires_at = null, last_heartbeat_at = null" in update_q
    # worker_id is never in the SET list — it stays exactly what it was.
    assert "worker_id = null" not in update_q
    # us-119.1 AC2: the write is guarded like every other run transition —
    # the read above and this update are two statements, and a cancel or a
    # completed submit can land between them.
    assert "where id = %s and status = 'running'" in update_q
    assert update_p == ("turn_limit", "log", "sess-1", RUN_ID)


def test_pause_run_that_loses_the_race_lands_nothing(monkeypatch):
    """us-119.1 AC2: the run was `running` when read and is not by the time
    the update runs (a manager cancelled it, a submit completed it). The
    guarded update matches no row; `pause_run` reports the loss and writes no
    event — instead of overwriting a terminal status with `paused`."""
    monkeypatch.setattr(
        db, "get_runner_config", lambda s, wid: {"autonomy_policy": {}}
    )

    def script(q, p):
        if q.startswith("select id, org_id, issue_id, kind, worker_id, resume_attempts"):
            return FakeCursor(_running_row())
        return FakeCursor()  # the update returns no row: someone got there first

    conn = ScriptConn(script)
    conn.rolled_back = False
    conn.rollback = lambda: setattr(conn, "rolled_back", True)
    _install(monkeypatch, conn)

    accepted, landed, attempts, cap = db.pause_run(
        object(),
        RUN_ID,
        reason="turn_limit",
        claude_session_id="sess-1",
        stdout="log",
        error="hit its turn ceiling",
    )

    assert (accepted, landed) == (False, "")
    assert (attempts, cap) == (0, db.DEFAULT_MAX_RESUME_ATTEMPTS)
    assert conn.rolled_back
    assert not any(q.startswith("insert into public.issue_events") for q, _ in conn.calls)
    assert not conn.committed


def test_pause_run_exhausted_falls_through_without_calling_complete_run(monkeypatch):
    """US-59.3: past the cap, `pause_run` reports "exhausted" and touches
    nothing — landing it `failed` is the caller's job (worker.py's ordinary
    failure path), so `complete_run` must not be called twice."""
    monkeypatch.setattr(
        db,
        "get_runner_config",
        lambda s, wid: {"autonomy_policy": {"max_resume_attempts": 2}},
    )
    complete_run_called = []
    monkeypatch.setattr(
        db, "complete_run", lambda *a, **k: complete_run_called.append(1)
    )

    def script(q, p):
        if q.startswith("select id, org_id, issue_id, kind, worker_id, resume_attempts"):
            return FakeCursor(_running_row(resume_attempts=2))
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    accepted, landed, attempts, cap = db.pause_run(
        object(),
        RUN_ID,
        reason="turn_limit",
        claude_session_id="sess-1",
        stdout="log",
        error="hit its turn ceiling",
        worker_name="pod-001-1",
    )

    assert (accepted, landed, attempts, cap) == (False, "exhausted", 2, 2)
    assert complete_run_called == []
    # No 'update public.runs ... paused' — only the read happened.
    assert not any(
        q.startswith("update public.runs") for q, _ in conn.calls
    )


def test_resume_claim_is_scoped_to_the_owning_worker(monkeypatch):
    """US-59.3/59.4/59.9: the resume-claim UPDATE's WHERE clause is the
    single-flight gate — worker_id must be bound, and the status set must be
    exactly paused/awaiting_input, never 'queued' (that would just be a
    second `claim_run`)."""

    def script(q, p):
        if q.startswith("update public.runs") and "resume_claim" not in q:
            return FakeCursor(
                {
                    "id": RUN_ID,
                    "org_id": ORG_ID,
                    "issue_id": ISSUE_ID,
                    "kind": "code",
                    "claim_expires_at": "later",
                    "claude_session_id": "sess-1",
                    "resume_reason": "turn_limit",
                }
            )
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    worker = {"id": WORKER_ID, "org_id": ORG_ID, "type": "autonomous", "name": "pod-001-1"}
    run = db.resume_claim(object(), RUN_ID, worker)

    assert run is not None
    assert run["claude_session_id"] == "sess-1"
    update_q, update_p = conn.calls[0]
    assert "status in ('paused', 'awaiting_input')" in update_q
    assert "worker_id = %s" in update_q
    assert WORKER_ID in update_p
    # An event lands so the run's history says it resumed, not just claimed.
    event_q, _ = conn.calls[1]
    assert "run-resumed" in event_q


def test_abandon_run_is_scoped_to_parked_statuses(monkeypatch):
    """US-59.7: abandon's WHERE clause only matches paused/awaiting_input —
    a run that has already started resuming (back to 'running') is not
    touched, which is what stops abandon racing a resume mid-flight."""

    def script(q, p):
        if q.startswith("update public.runs"):
            return FakeCursor({"id": RUN_ID, "issue_id": ISSUE_ID, "kind": "code"})
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    ok = db.abandon_run(
        object(),
        RUN_ID,
        ORG_ID,
        reason="stale — not coming back",
        member={"id": "user-1", "name": "kaushlesh"},
    )

    assert ok is True
    update_q, update_p = conn.calls[0]
    assert "status in ('paused', 'awaiting_input')" in update_q
    assert "status = 'abandoned'" in update_q
    assert "worker_id = null" in update_q
    assert update_p[0] == "stale — not coming back"


def test_abandon_run_racing_a_resume_reports_false(monkeypatch):
    """The same guard from the other side: once resumed, status is
    'running' again, so the UPDATE's WHERE matches nothing and the caller
    (the API endpoint) sees False — never a silent no-op success."""

    def script(q, p):
        if q.startswith("update public.runs"):
            return FakeCursor(None)  # no row matched
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    ok = db.abandon_run(object(), RUN_ID, ORG_ID, reason="too late")
    assert ok is False


def test_has_pending_clarification_checks_unanswered_only(monkeypatch):
    seen = {}

    def script(q, p):
        if "public.clarifications" in q:
            seen["query"] = q
            seen["params"] = p
            return FakeCursor(row={"exists": 1})
        return FakeCursor()

    conn = ScriptConn(script)
    _install(monkeypatch, conn)

    run = {"issue_id": ISSUE_ID, "org_id": ORG_ID}
    assert db.has_pending_clarification(object(), run) is True
    assert "answer is null and selected_options is null" in seen["query"]
    assert seen["params"] == (ISSUE_ID, ORG_ID)


def test_has_pending_clarification_false_with_no_issue(monkeypatch):
    # A project-scoped run (no issue_id) can never have a clarification.
    assert db.has_pending_clarification(object(), {"issue_id": None}) is False
