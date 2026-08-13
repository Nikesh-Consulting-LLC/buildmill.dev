"""US-32.2: an agent can be given a name.

The generated `pod-001-1` is copied into three columns at provision time —
`principals.display_name`, `workers.name`, `agent_slots.name` — so a rename that
writes one of them leaves the other two lying, which reads to a manager as a
caching bug. These tests pin the fan-out, and pin that the infrastructure
identity (service name, slot index, workspace, worker/principal ids) is not part
of it: a systemd unit named after a display name is one rename away from an
orphaned service.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import db
from app.auth import AuthUser, verify_token
from app.config import get_settings
from app.main import app
from app.routers import agents

PRINCIPAL_ID = str(uuid.uuid4())
WORKER_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
SLOT_ID = str(uuid.uuid4())


class FakeCursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [self._row] if self._row else []


class FakeConn:
    def __init__(self, script):
        self.calls: list[tuple[str, tuple | None]] = []
        self.script = script
        self.committed = False

    def execute(self, query, params=None):
        self.calls.append((query, params))
        return self.script(query, params)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sql_matching(self, *needles: str) -> list[tuple[str, tuple | None]]:
        return [
            (q, p)
            for q, p in self.calls
            if all(n in " ".join(q.split()) for n in needles)
        ]


def _conn(monkeypatch, script):
    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


# --------------------------------------------------------------- the fan-out


def test_rename_writes_all_three_name_columns(monkeypatch):
    def script(q, p):
        if "select display_name" in q:
            return FakeCursor({"display_name": "pod-001-1"})
        return FakeCursor(None)

    conn = _conn(monkeypatch, script)
    result = db.rename_agent(
        object(),
        PRINCIPAL_ID,
        "frontend",
        actor_id="actor-1",
        actor_email="me@example.com",
        org_id=ORG_ID,
    )

    assert result == {"name": "frontend", "from": "pod-001-1"}
    assert conn.sql_matching("update public.principals", "display_name")
    assert conn.sql_matching("update public.workers", "set name")
    assert conn.sql_matching("update public.agent_slots", "set name")
    # every one of them gets the SAME new name
    for query, params in conn.calls:
        if query.strip().startswith("update"):
            assert params[0] == "frontend"
    assert conn.committed


def test_rename_leaves_infrastructure_identity_alone(monkeypatch):
    """The columns a systemd unit and a workspace path are built from must not
    appear in any statement the rename issues."""
    conn = _conn(
        monkeypatch,
        lambda q, p: FakeCursor({"display_name": "pod-001-1"})
        if "select display_name" in q
        else FakeCursor(None),
    )
    db.rename_agent(
        object(),
        PRINCIPAL_ID,
        "frontend",
        actor_id=None,
        actor_email="",
        org_id=ORG_ID,
    )
    written = " ".join(q for q, _ in conn.calls if q.strip().startswith("update"))
    for immutable in ("service_name", "slot_index", "workspace_path", "worker_id"):
        assert immutable not in written


def test_rename_only_touches_the_live_slot(monkeypatch):
    """A removed slot keeps the name it ran under — past runs name the agent
    that did them (US-26.9)."""
    conn = _conn(
        monkeypatch,
        lambda q, p: FakeCursor({"display_name": "pod-001-1"})
        if "select display_name" in q
        else FakeCursor(None),
    )
    db.rename_agent(
        object(),
        PRINCIPAL_ID,
        "frontend",
        actor_id=None,
        actor_email="",
        org_id=ORG_ID,
    )
    slot_writes = conn.sql_matching("update public.agent_slots")
    assert slot_writes
    assert "status = 'active'" in " ".join(q for q, _ in slot_writes)


def test_rename_records_an_event_with_both_values_and_the_actor(monkeypatch):
    conn = _conn(
        monkeypatch,
        lambda q, p: FakeCursor({"display_name": "pod-001-1"})
        if "select display_name" in q
        else FakeCursor(None),
    )
    db.rename_agent(
        object(),
        PRINCIPAL_ID,
        "frontend",
        actor_id="actor-1",
        actor_email="me@example.com",
        org_id=ORG_ID,
    )
    events = conn.sql_matching("insert into public.agent_events")
    assert len(events) == 1
    _query, params = events[0]
    org, principal, payload, actor_id, actor_email = params
    assert org == ORG_ID
    assert principal == PRINCIPAL_ID
    assert json.loads(payload) == {"from": "pod-001-1", "to": "frontend"}
    assert actor_id == "actor-1"
    assert actor_email == "me@example.com"


def test_agent_identity_reads_the_live_slot_and_autonomous_worker(monkeypatch):
    conn = _conn(
        monkeypatch,
        lambda q, p: FakeCursor(
            {
                "principal_id": PRINCIPAL_ID,
                "kind": "agent",
                "display_name": "pod-001-1",
                "worker_id": WORKER_ID,
                "org_id": ORG_ID,
                "slot_index": 1,
                "service_name": "buildmill-agent@1",
            }
        ),
    )
    row = db.agent_identity(object(), PRINCIPAL_ID)
    assert row["service_name"] == "buildmill-agent@1"
    query = " ".join(conn.calls[0][0].split())
    assert "w.type = 'autonomous'" in query
    assert "s.status = 'active'" in query


def test_agent_identity_refuses_a_non_uuid():
    assert db.agent_identity(object(), "../../etc/passwd") is None


# ------------------------------------------------------------- the endpoint


@pytest.fixture()
def client(monkeypatch):
    app.dependency_overrides[verify_token] = lambda: AuthUser(
        id="actor-1", email="me@example.com", token="jwt"
    )
    app.dependency_overrides[get_settings] = lambda: object()

    async def allow(org_id, user, settings):
        return None

    monkeypatch.setattr(agents, "_require_manage_work", allow)
    # NOT `with TestClient(app)`: the context manager runs the app's lifespan,
    # and its exit shuts down the MCP session manager for the whole process —
    # which made every test in test_factory_mcp.py that ran after this file
    # fail with "task group is not initialized". These tests need routing, not
    # startup.
    yield TestClient(app)
    app.dependency_overrides.clear()


def _identity(**over):
    row = {
        "principal_id": PRINCIPAL_ID,
        "kind": "agent",
        "display_name": "pod-001-1",
        "worker_id": WORKER_ID,
        "org_id": ORG_ID,
        "worker_name": "pod-001-1",
        "slot_id": SLOT_ID,
        "slot_name": "pod-001-1",
        "slot_index": 1,
        "service_name": "buildmill-agent@1",
    }
    row.update(over)
    return row


def test_endpoint_trims_and_reports_the_unchanged_identity(client, monkeypatch):
    monkeypatch.setattr(db, "agent_identity", lambda s, pid: _identity())
    seen = {}

    def fake_rename(settings, principal_id, name, **kw):
        seen["name"] = name
        seen.update(kw)
        return {"name": name, "from": "pod-001-1"}

    monkeypatch.setattr(db, "rename_agent", fake_rename)

    res = client.patch(
        f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": "  frontend  "}
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert seen["name"] == "frontend"
    assert body["name"] == "frontend"
    assert body["previous_name"] == "pod-001-1"
    assert body["service_name"] == "buildmill-agent@1"
    assert body["slot_index"] == 1
    assert body["worker_id"] == WORKER_ID


@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_endpoint_refuses_an_empty_name(client, monkeypatch, name):
    monkeypatch.setattr(db, "agent_identity", lambda s, pid: _identity())
    called = {"n": 0}
    monkeypatch.setattr(
        db,
        "rename_agent",
        lambda *a, **k: called.update(n=called["n"] + 1) or {},
    )
    res = client.patch(f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": name})
    assert res.status_code == 422
    assert called["n"] == 0


def test_endpoint_refuses_an_overlong_name(client, monkeypatch):
    monkeypatch.setattr(db, "agent_identity", lambda s, pid: _identity())
    res = client.patch(
        f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": "x" * 81}
    )
    assert res.status_code == 422


def test_endpoint_refuses_renaming_a_human(client, monkeypatch):
    monkeypatch.setattr(
        db, "agent_identity", lambda s, pid: _identity(kind="human")
    )
    res = client.patch(f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": "Bob"})
    assert res.status_code == 422
    assert "person" in res.json()["detail"]


def test_endpoint_404s_an_unknown_principal(client, monkeypatch):
    monkeypatch.setattr(db, "agent_identity", lambda s, pid: None)
    res = client.patch(f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": "x"})
    assert res.status_code == 404


def test_endpoint_is_capability_gated(monkeypatch):
    """The gate is the same one the runner config PATCH uses — a page whose
    fields need two different permissions is a page that half-works."""
    app.dependency_overrides[verify_token] = lambda: AuthUser(
        id="actor-1", email="me@example.com", token="jwt"
    )
    app.dependency_overrides[get_settings] = lambda: object()
    monkeypatch.setattr(db, "agent_identity", lambda s, pid: _identity())
    monkeypatch.setattr(
        db, "rename_agent", lambda *a, **k: pytest.fail("must not write")
    )

    async def deny(settings, token, fn, params):
        return False

    monkeypatch.setattr(agents, "rpc", deny)
    res = TestClient(app).patch(
        f"/api/v1/agents/{PRINCIPAL_ID}/name", json={"name": "frontend"}
    )
    app.dependency_overrides.clear()
    assert res.status_code == 403
