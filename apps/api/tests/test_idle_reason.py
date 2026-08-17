"""US-27.9: an agent that cannot work says so.

On 2026-07-26 both agents on pod-001 showed connected, their services showed
active, the host card was green — and neither had been able to claim anything
for fourteen minutes. Their worker tokens had been revoked. The socket
handshake had already succeeded so the sockets stayed up and kept heartbeating;
only the HTTP pool poll was rejected, silently, every three seconds.

The manager's own reading of the app was "my agents say waiting for work".
Every surface agreed with that and every surface was wrong.
"""

from __future__ import annotations

from app import db


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class IdleConn:
    """Answers each of `worker_idle_reason`'s queries by shape."""

    def __init__(self, *, worker, held=None, paused=None, queued=(), grants=()):
        self.worker = worker
        self.held = held
        self.paused = paused
        self.queued = list(queued)
        self.grants = list(grants)

    def execute(self, query, params=None):
        q = " ".join(query.split())
        if "from public.workers where id" in q:
            return FakeCursor(self.worker)
        if "from public.runs where worker_id" in q:
            return FakeCursor(self.held)
        if "from public.runner_config where worker_id" in q:
            return FakeCursor(self.paused)
        if "run_hold_reason(r.id) as hold_reason" in q:
            return FakeCursor(rows=self.queued)
        if "from public.worker_capabilities" in q:
            return FakeCursor(rows=self.grants)
        return FakeCursor()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


WORKER_ID = "11111111-1111-4111-8111-111111111111"
ACTIVE = {"id": WORKER_ID, "org_id": "org-1", "status": "active"}


def _reason(monkeypatch, conn, model="grok-4.5"):
    """`model` is what the resolver answers for this agent — us-116.2 asks it
    ahead of the queue tier, so every queue-tier case here runs with a model
    that resolves. `None` means nothing resolves."""
    from app import model_resolution as mr

    monkeypatch.setattr(db, "_connect", lambda settings: conn)

    def fake_resolve(inputs):
        tried = mr.session_kind_order(inputs.config)
        if model:
            return mr.SessionModel(model=model, kind=tried[0] if tried else None,
                                   resolved=None, tried=tried[:1])
        return mr.SessionModel(model=None, kind=None, resolved=None, tried=tried)

    monkeypatch.setattr(mr, "resolve_session", fake_resolve)
    return db.worker_idle_reason(object(), WORKER_ID)


def _item(**over):
    row = {
        "id": "run-1",
        "kind": "code",
        "project_id": "proj-1",
        "paused_at": None,
        "hold_reason": None,
        "label": "Login and auth",
    }
    row.update(over)
    return row


def test_a_revoked_token_is_the_reason_not_waiting_for_work(monkeypatch):
    """The 2026-07-26 case. Presence is not permission."""
    conn = IdleConn(worker={**ACTIVE, "status": "revoked"})
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "revoked"
    assert "rejected" in out["detail"]


def test_a_paused_agent_says_paused(monkeypatch):
    conn = IdleConn(worker=ACTIVE, paused={"paused": True}, queued=[_item()])
    assert _reason(monkeypatch, conn)["reason"] == "paused"


def test_an_agent_holding_a_run_is_working(monkeypatch):
    conn = IdleConn(worker=ACTIVE, held={"id": "run-9"})
    assert _reason(monkeypatch, conn)["reason"] == "working"


def test_access_that_matches_nothing_queued_is_named(monkeypatch):
    # US-55.1: access to some project, but nothing queued there.
    conn = IdleConn(
        worker=ACTIVE,
        queued=[_item()],
        grants=[{"project_id": "other-proj"}],
    )
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "no-grants"
    assert "can access" in out["detail"]


def test_access_that_matches_leaves_the_agent_simply_idle(monkeypatch):
    conn = IdleConn(
        worker=ACTIVE,
        queued=[_item()],
        grants=[{"project_id": "proj-1"}],
    )
    assert _reason(monkeypatch, conn)["reason"] == "idle"


def test_an_accessless_agent_reads_as_no_grants_not_idle(monkeypatch):
    """US-31.3/55.1: an agent with zero access rows can claim NOTHING, so
    telling it "it should pick one up" was the same class of lie this readout
    exists to end."""
    conn = IdleConn(worker=ACTIVE, queued=[_item()], grants=[])
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "no-grants"
    assert "no project access at all" in out["detail"]


def test_an_empty_queue_is_the_healthy_idle(monkeypatch):
    conn = IdleConn(worker=ACTIVE, queued=[])
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "idle"
    assert out["detail"] == "nothing is queued"


def test_a_fully_held_queue_names_the_nearest_items_reason(monkeypatch):
    """"No claimable work" has to distinguish an empty queue from a queue
    whose items are all held — otherwise it is the same lie again."""
    conn = IdleConn(
        worker=ACTIVE,
        queued=[
            _item(hold_reason="waiting on sibling stories to be approved"),
            _item(id="run-2", paused_at="2026-07-26T00:00:00Z"),
        ],
    )
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "queue-held"
    assert "waiting on sibling stories" in out["detail"]
    assert "Login and auth" in out["detail"]


def test_a_paused_queue_item_is_not_offered_either(monkeypatch):
    conn = IdleConn(
        worker=ACTIVE, queued=[_item(paused_at="2026-07-26T00:00:00Z")]
    )
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "queue-held"
    assert "paused by the manager" in out["detail"]


def test_an_unknown_worker_answers_unknown(monkeypatch):
    conn = IdleConn(worker=None)
    assert _reason(monkeypatch, conn)["reason"] == "unknown"


# ---------------------------------------------------------------------------
# us-116.2: configuration is reported before queue state.
# ---------------------------------------------------------------------------


def test_every_role_unchecked_reads_no_roles(monkeypatch):
    conn = IdleConn(
        worker=ACTIVE, paused={"paused": False, "enabled_kinds": []}, queued=[_item()],
        grants=[{"project_id": "proj-1"}],
    )
    out = _reason(monkeypatch, conn)
    assert out["reason"] == "no-roles"
    assert "What this agent does" in out["detail"]


def test_null_enabled_kinds_is_not_no_roles(monkeypatch):
    """us-53.4: a never-saved agent is unrestricted, not benched."""
    conn = IdleConn(
        worker=ACTIVE, paused={"paused": False, "enabled_kinds": None}, queued=[_item()],
        grants=[{"project_id": "proj-1"}],
    )
    assert _reason(monkeypatch, conn)["reason"] == "idle"


def test_no_model_for_any_claimed_role_reads_no_model_with_the_roles_named(monkeypatch):
    conn = IdleConn(
        worker=ACTIVE,
        paused={"paused": False, "enabled_kinds": ["prd", "plan"], "model_overrides": {}},
        queued=[_item()],
        grants=[{"project_id": "proj-1"}],
    )
    out = _reason(monkeypatch, conn, model=None)
    assert out["reason"] == "no-model"
    assert "(Planning)" in out["detail"]
    assert "Model per role" in out["detail"]


def test_a_model_for_some_roles_is_enough(monkeypatch):
    """AC4: some-but-not-all is NOT flagged — the agent can work."""
    conn = IdleConn(
        worker=ACTIVE,
        paused={"paused": False, "enabled_kinds": ["prd", "code"]},
        queued=[_item()],
        grants=[{"project_id": "proj-1"}],
    )
    # the fake resolver answers a model for the first claimed kind only — that
    # is exactly what "some roles" looks like to resolve_session
    assert _reason(monkeypatch, conn, model="grok-4.5")["reason"] == "idle"


def test_precedence_revoked_beats_no_model_and_paused_beats_no_roles(monkeypatch):
    revoked = IdleConn(worker={**ACTIVE, "status": "revoked"},
                       paused={"paused": False, "enabled_kinds": []})
    assert _reason(monkeypatch, revoked, model=None)["reason"] == "revoked"
    paused = IdleConn(worker=ACTIVE, paused={"paused": True, "enabled_kinds": []})
    assert _reason(monkeypatch, paused, model=None)["reason"] == "paused"
    working = IdleConn(worker=ACTIVE, held={"id": "run-9"},
                       paused={"paused": False, "enabled_kinds": []})
    assert _reason(monkeypatch, working, model=None)["reason"] == "working"


def test_no_roles_beats_no_model_and_both_beat_the_queue_tier(monkeypatch):
    conn = IdleConn(worker=ACTIVE, paused={"paused": False, "enabled_kinds": []},
                    queued=[], grants=[])
    assert _reason(monkeypatch, conn, model=None)["reason"] == "no-roles"
    conn2 = IdleConn(worker=ACTIVE, paused={"paused": False, "enabled_kinds": ["code"]},
                     queued=[], grants=[])
    # an empty queue used to read "idle — nothing is queued"; a stuck agent is
    # not idle
    assert _reason(monkeypatch, conn2, model=None)["reason"] == "no-model"
