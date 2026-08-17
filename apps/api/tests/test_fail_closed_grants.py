"""US-31.3: an agent only works on projects it is assigned to.

The capability gate was fail-open — zero grant rows meant unrestricted, at
the pool listing, the claim gate, and the git-proxy clone gate. These tests
pin the inversion: all three gates route through the ONE shared predicate
(public.worker_has_grant), zero grants means nothing is offered and nothing
clones, and a refusal names which grant is missing.
"""

import uuid

import pytest

from app import db

WORKER_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())
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


# ------------------------------------------------ the one shared predicate


def test_all_three_gates_use_worker_has_grant(monkeypatch):
    """The pre-inversion design had three copies of the rule; the clone gate
    (the one nobody was thinking about) is how a zero-grant agent could read
    every repository in the org. One function, three callers."""
    # claim gate
    conn = _conn(
        monkeypatch,
        lambda q, p: FakeCursor(_gate_row(allowed=False, on_project=False)),
    )
    db.worker_run_refusal(object(), WORKER_ID, RUN_ID)
    assert "public.worker_has_grant" in conn.calls[0][0]

    # clone gate
    conn = _conn(monkeypatch, lambda q, p: FakeCursor({"allowed": False}))
    db.worker_allowed_for_project(object(), WORKER_ID, PROJECT_ID)
    assert "public.worker_has_grant" in conn.calls[0][0]

    # pool listing
    conn = _conn(monkeypatch, lambda q, p: FakeCursor(rows=[]))
    db.list_worker_pool(
        object(),
        {"id": WORKER_ID, "org_id": str(uuid.uuid4()), "type": "autonomous"},
    )
    pool_q = next(q for q, _ in conn.calls if "queue_rank" in q)
    assert "public.worker_has_grant" in pool_q
    # The fail-open escape hatch is gone: what made the gate permissive was
    # `not exists (... worker_capabilities where worker_id = ...)` — "this
    # worker has no grants, so allow everything". Assert that exact shape is
    # absent, rather than the bare words "not exists" (us-31.5 legitimately
    # adds one for the blocked-item check).
    assert "worker_capabilities" not in pool_q


def _gate_row(**over):
    """The claim gate's row shape — the combined predicate (us-55.1) plus
    attempt state (us-31.5, checked first) and the kind checkboxes (us-53.4,
    named separately so the refusal can tell the two halves apart)."""
    row = {
        "allowed": True,
        "kind": "code",
        "issue_id": str(uuid.uuid4()),
        "exhausted": False,
        "blocked": False,
        "kind_checked": True,
    }
    row.update(over)
    return row


def test_refusal_names_the_missing_access():
    reason = _refusal_for(_gate_row(allowed=False))
    assert "does not have access to that project" in reason
    assert "Team page" in reason


def test_refusal_names_the_unchecked_kind():
    reason = _refusal_for(_gate_row(allowed=False, kind="plan", kind_checked=False))
    assert "'plan'" in reason and "unchecked" in reason


def test_allowed_run_answers_none():
    assert _refusal_for(_gate_row()) is None


def test_unknown_run_answers_none_for_the_404_downstream():
    assert _refusal_for(None) is None
    # non-uuid ids never reach the DB
    assert db.worker_run_refusal(object(), WORKER_ID, "not-a-uuid") is None


def _refusal_for(row):
    import unittest.mock as mock

    conn = FakeConn(lambda q, p: FakeCursor(row))
    with mock.patch.object(db, "_connect", lambda s: conn):
        return db.worker_run_refusal(object(), WORKER_ID, RUN_ID)


def test_boolean_face_matches_refusal(monkeypatch):
    monkeypatch.setattr(db, "worker_run_refusal", lambda s, w, r: None)
    assert db.worker_allowed_for_run(object(), WORKER_ID, RUN_ID) is True
    monkeypatch.setattr(db, "worker_run_refusal", lambda s, w, r: "nope")
    assert db.worker_allowed_for_run(object(), WORKER_ID, RUN_ID) is False


# ------------------------------------------------ idle-reason truthfulness


def test_zero_grants_reads_as_no_grants_not_idle(monkeypatch):
    """US-27.9's `no-grants` idle reason is finally literally true: with the
    gate fail-closed, an agent with zero grants must never be told 'it
    should pick one up'."""
    offerable = {
        "id": RUN_ID,
        "kind": "code",
        "project_id": PROJECT_ID,
        "paused_at": None,
        "hold_reason": None,
        "label": "Some story",
    }

    def script(q, p):
        if "from public.workers where id" in q:
            return FakeCursor(
                {"id": WORKER_ID, "org_id": str(uuid.uuid4()), "status": "active"}
            )
        if "status = 'queued'" in q:
            return FakeCursor(rows=[offerable])
        if "from public.worker_capabilities" in q:
            return FakeCursor(rows=[])  # zero grants
        # anything else the readout consults (current run, paused, sessions)
        return FakeCursor(None, rows=[])

    _conn(monkeypatch, script)
    # us-116.2: configuration is checked ahead of the queue tier; this agent
    # has a model, so the reason it cannot work is its grants.
    from app import model_resolution as mr

    monkeypatch.setattr(
        mr, "resolve_session",
        lambda inputs: mr.SessionModel(model="grok-4.5", kind="code", resolved=None, tried=["code"]),
    )
    out = db.worker_idle_reason(object(), WORKER_ID)
    assert out["reason"] == "no-grants"
    assert "no project access at all" in out["detail"]
