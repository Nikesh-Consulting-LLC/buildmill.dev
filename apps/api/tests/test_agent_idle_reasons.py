"""US-35.1: why every agent in an org is idle, from one computation.

`GET /agent-servers/{id}/slots/idle-reasons` (US-27.9) answers this per machine,
which cannot reach an agent that is not on a managed machine at all — and Team
and the dashboard both need to ask about those. The risk this route creates is a
second implementation drifting from the first, so what these tests pin is that it
delegates to the same `db.worker_idle_reason` and that its org scoping is real.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import AuthUser, verify_token
from app.config import get_settings
from app.main import app
from app.routers import agents

ORG_ID = str(uuid.uuid4())
OTHER_ORG_ID = str(uuid.uuid4())
WORKER_A = str(uuid.uuid4())
WORKER_B = str(uuid.uuid4())
PRINCIPAL_A = str(uuid.uuid4())


@pytest.fixture
def client():
    app.dependency_overrides[verify_token] = lambda: AuthUser(
        id="actor-1", email="me@example.com", token="jwt"
    )
    app.dependency_overrides[get_settings] = lambda: object()
    # NOT `with TestClient(app)`: the context manager runs the app's lifespan,
    # whose exit shuts down the MCP session manager process-wide and fails every
    # test_factory_mcp.py test that runs afterwards. Routing is all this needs.
    yield TestClient(app)
    app.dependency_overrides.clear()


def _rows(monkeypatch, rows):
    """Stand in for the RLS-scoped worker read, recording what was asked for."""
    seen: dict[str, object] = {}

    async def fake_get(settings, token, table, params):
        seen["table"] = table
        seen["params"] = params
        seen["token"] = token
        return rows

    monkeypatch.setattr(agents, "postgrest_get", fake_get)
    return seen


def _reasons(monkeypatch, mapping):
    monkeypatch.setattr(
        db,
        "worker_idle_reason",
        lambda settings, worker_id: mapping[worker_id],
    )


@pytest.fixture(autouse=True)
def _live(monkeypatch):
    """us-116.4: the route answers `db.agent_status`, which asks presence
    first. Every agent here is live unless a test says otherwise, so the
    idle-reason cases below still exercise the reason tier."""
    monkeypatch.setattr(db, "worker_is_live", lambda settings, worker_id: True)
    monkeypatch.setattr(db, "worker_last_seen", lambda settings, worker_id: None)


def test_returns_a_reason_per_worker(client, monkeypatch):
    _rows(
        monkeypatch,
        [
            {"id": WORKER_A, "principal_id": PRINCIPAL_A},
            {"id": WORKER_B, "principal_id": None},
        ],
    )
    _reasons(
        monkeypatch,
        {
            WORKER_A: {"reason": "working", "detail": "holding a run"},
            WORKER_B: {"reason": "idle", "detail": "nothing is queued"},
        },
    )

    r = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}")

    assert r.status_code == 200
    body = r.json()
    assert body["reasons"][WORKER_A]["reason"] == "working"
    assert body["reasons"][WORKER_B]["reason"] == "idle"


def test_keys_by_principal_as_well_as_worker(client, monkeypatch):
    """Team addresses an agent by principal, the dashboard by worker. Both keys
    come off one computation rather than each caller rebuilding the mapping."""
    _rows(monkeypatch, [{"id": WORKER_A, "principal_id": PRINCIPAL_A}])
    _reasons(
        monkeypatch,
        {WORKER_A: {"reason": "no-grants", "detail": "assigned to no project"}},
    )

    body = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}").json()

    assert body["by_principal"][PRINCIPAL_A]["reason"] == "no-grants"
    assert body["reasons"][WORKER_A] == body["by_principal"][PRINCIPAL_A]


def test_a_worker_without_a_principal_is_not_in_the_principal_map(
    client, monkeypatch
):
    _rows(monkeypatch, [{"id": WORKER_B, "principal_id": None}])
    _reasons(monkeypatch, {WORKER_B: {"reason": "idle", "detail": "nothing"}})

    body = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}").json()

    assert body["by_principal"] == {}
    assert WORKER_B in body["reasons"]


def test_org_scoping_is_the_query_and_the_caller_s_own_token(client, monkeypatch):
    """The isolation is RLS, so it only holds if the read is filtered by the
    requested org AND made with the user's token — a service-role read here
    would return every org's agents to anyone."""
    seen = _rows(monkeypatch, [])
    _reasons(monkeypatch, {})

    client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}")

    assert seen["table"] == "workers"
    assert seen["params"]["org_id"] == f"eq.{ORG_ID}"
    assert seen["token"] == "jwt"


def test_an_org_the_caller_cannot_see_yields_nothing(client, monkeypatch):
    """RLS returns no worker rows to a non-member, so the endpoint has nothing
    to compute — it must not fall back to anything wider."""
    _rows(monkeypatch, [])
    called: list[str] = []
    monkeypatch.setattr(
        db,
        "worker_idle_reason",
        lambda settings, worker_id: called.append(worker_id) or {"reason": "idle"},
    )

    body = client.get(f"/api/v1/agents/idle-reasons?org={OTHER_ORG_ID}").json()

    assert body == {"reasons": {}, "by_principal": {}}
    assert called == []


def test_org_is_required(client, monkeypatch):
    """Without an org there is no scope, and defaulting to one would be a guess
    at which org the caller meant."""
    _rows(monkeypatch, [])
    _reasons(monkeypatch, {})

    assert client.get("/api/v1/agents/idle-reasons").status_code == 422


def test_reasons_come_from_the_shared_computation(client, monkeypatch):
    """The whole point of the route: it delegates to the same function the
    host-scoped route calls, so the two can never disagree."""
    _rows(monkeypatch, [{"id": WORKER_A, "principal_id": PRINCIPAL_A}])
    seen: list[str] = []

    def spy(settings, worker_id):
        seen.append(worker_id)
        return {"reason": "paused", "detail": "paused by an admin"}

    monkeypatch.setattr(db, "worker_idle_reason", spy)

    body = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}").json()

    assert seen == [WORKER_A]
    assert body["reasons"][WORKER_A]["reason"] == "paused"


# ---------------------------------------------------------------------------
# us-116.4: one status — presence in front, the manager's words.
# ---------------------------------------------------------------------------


def test_the_answer_carries_state_with_paused_read_as_stopped_and_idle_as_ready(
    client, monkeypatch
):
    _rows(monkeypatch, [{"id": WORKER_A, "principal_id": PRINCIPAL_A},
                        {"id": WORKER_B, "principal_id": None}])
    _reasons(monkeypatch, {
        WORKER_A: {"reason": "paused", "detail": "paused by an admin"},
        WORKER_B: {"reason": "idle", "detail": "nothing is queued"},
    })
    body = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}").json()
    assert body["reasons"][WORKER_A]["state"] == "stopped"
    assert body["reasons"][WORKER_A]["reason"] == "paused"
    assert body["reasons"][WORKER_B]["state"] == "ready"


def test_an_agent_with_no_live_heartbeat_is_offline_whatever_its_reason(client, monkeypatch):
    """Presence beats everything: an offline agent's idle reason is not even
    computed — nothing below offline is actionable while it is not there."""
    _rows(monkeypatch, [{"id": WORKER_A, "principal_id": PRINCIPAL_A}])
    monkeypatch.setattr(db, "worker_is_live", lambda settings, worker_id: False)
    monkeypatch.setattr(db, "worker_last_seen", lambda settings, worker_id: "2026-08-17T11:48:58+00:00")
    called: list[str] = []
    monkeypatch.setattr(db, "worker_idle_reason",
                        lambda settings, worker_id: called.append(worker_id) or {"reason": "revoked"})
    body = client.get(f"/api/v1/agents/idle-reasons?org={ORG_ID}").json()
    assert body["reasons"][WORKER_A]["state"] == "offline"
    assert body["reasons"][WORKER_A]["last_seen_at"] == "2026-08-17T11:48:58+00:00"
    assert called == []
