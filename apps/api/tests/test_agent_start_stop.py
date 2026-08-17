"""us-116.5: Start means start.

Three buttons were called start and none of them started: the roster's ▶ was
membership Reactivate (its ⏸ revoked the token), Enable flipped a flag and
never touched a dead service, Restart was host-authorized so a pool tenant got
a 404 the runner page swallowed. `POST /agents/{principal}/start` and `/stop`
are the agent's own, authorized on the SLOT's org, and Start restarts the
service when the agent is not live.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import agent_provision, db
from app.auth import AuthUser, verify_token
from app.config import get_settings
from app.main import app
from app.routers import agents

PRINCIPAL = str(uuid.uuid4())
WORKER = str(uuid.uuid4())
SLOT = str(uuid.uuid4())
HOST = str(uuid.uuid4())
TENANT_ORG = str(uuid.uuid4())
PLATFORM_ORG = str(uuid.uuid4())


@pytest.fixture
def client():
    app.dependency_overrides[verify_token] = lambda: AuthUser(
        id="actor-1", email="me@example.com", token="jwt"
    )
    app.dependency_overrides[get_settings] = lambda: object()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def wired(monkeypatch):
    """A tenant's agent on a platform-owned pool: the slot's org is the tenant,
    the host's org is the platform. Every write is captured."""
    state = {
        "slot": {
            "id": SLOT, "org_id": TENANT_ORG, "worker_id": WORKER, "principal_id": PRINCIPAL,
            "agent_server_id": HOST, "host_org_id": PLATFORM_ORG, "slot_index": 9,
            "desired_state": "paused", "service_state": "active", "name": "Programmer",
        },
        "worker": {"id": WORKER, "org_id": TENANT_ORG, "name": "Programmer", "status": "active"},
        "live": True,
        "rpc": [],
        "paused": [],
        "slot_updates": [],
        "pushed": [],
        "jobs": [],
        "launched": [],
        "busy": None,
        "job_active": False,
    }

    async def fake_rpc(settings, token, fn, params):
        state["rpc"].append((fn, params))
        return True

    async def fake_push(settings, worker_id):
        state["pushed"].append(worker_id)

    def fake_create_job(settings, **kw):
        if state["job_active"]:
            raise agent_provision.JobActive("This machine already has a job running.")
        state["jobs"].append(kw)
        return {"id": "job-1", **kw}

    monkeypatch.setattr(agents, "rpc", fake_rpc)
    monkeypatch.setattr(agent_provision, "slot_for_principal", lambda s, p: state["slot"])
    monkeypatch.setattr(db, "get_worker", lambda s, w: state["worker"])
    monkeypatch.setattr(agent_provision, "set_paused",
                        lambda s, w, paused: state["paused"].append((w, paused)))
    monkeypatch.setattr(agent_provision, "update_slot",
                        lambda s, sid, fields: state["slot_updates"].append((sid, fields)))
    monkeypatch.setattr(agent_provision, "push_config", fake_push)
    monkeypatch.setattr(db, "worker_is_live", lambda s, w: state["live"])
    monkeypatch.setattr(agent_provision, "create_job", fake_create_job)
    monkeypatch.setattr(agent_provision, "launch", lambda s, ctx: state["launched"].append(ctx))
    monkeypatch.setattr(agent_provision, "worker_is_busy", lambda s, w: state["busy"])
    return state


def test_start_on_a_live_agent_enables_and_does_not_restart(client, wired):
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": True, "restarted": None, "live": True, "service_state": "active"}
    assert wired["paused"] == [(WORKER, False)]
    assert wired["slot_updates"] == [(SLOT, {"desired_state": "enabled"})]
    assert wired["pushed"] == [WORKER]
    assert wired["jobs"] == [] and wired["launched"] == []


def test_start_is_authorized_on_the_slots_org_not_the_hosts(client, wired):
    """AC3: a tenant whose agent sits on a platform pool. Today's Restart asked
    manage_org on the HOST's org, which the tenant is never a member of."""
    client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    fn, params = wired["rpc"][0]
    assert fn == "has_org_capability"
    assert params == {"p_org": TENANT_ORG, "p_capability": "manage_org"}


def test_start_on_an_agent_that_is_not_live_queues_the_restart_job(client, wired):
    wired["live"] = False
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.status_code == 200, r.text
    assert r.json()["restarted"] == "job-1"
    # enabled FIRST, so the restart job's restore-previous-state leaves it enabled
    assert wired["paused"] == [(WORKER, False)]
    assert wired["slot_updates"][0] == (SLOT, {"desired_state": "enabled"})
    job = wired["jobs"][0]
    assert job["kind"] == "restart" and job["slot_id"] == SLOT
    # the job row belongs to the host's org — that is where the job list lives
    assert job["org_id"] == PLATFORM_ORG and job["agent_server_id"] == HOST
    assert job["by_email"] == "me@example.com"
    assert wired["launched"] == [
        {"job_id": "job-1", "agent_server_id": HOST, "kind": "restart", "slot_id": SLOT}
    ]


def test_start_restarts_when_the_probe_found_the_unit_dead_even_if_live(client, wired):
    wired["slot"] = {**wired["slot"], "service_state": "failed"}
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.json()["restarted"] == "job-1"


def test_a_host_already_running_a_job_is_a_sentence_and_the_agent_stays_enabled(client, wired):
    wired["live"] = False
    wired["job_active"] = True
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.status_code == 409
    assert "already has a job running" in r.json()["detail"]
    assert "enabled" in r.json()["detail"]
    assert wired["paused"] == [(WORKER, False)]


def test_a_revoked_token_is_refused_naming_the_repair(client, wired):
    wired["worker"] = {**wired["worker"], "status": "revoked"}
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.status_code == 409
    assert "Re-issue" in r.json()["detail"]
    assert wired["paused"] == []


def test_an_agent_installed_by_hand_has_no_start(client, wired, monkeypatch):
    monkeypatch.setattr(agent_provision, "slot_for_principal", lambda s, p: None)
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/start")
    assert r.status_code == 409
    assert "installed outside Build Mill" in r.json()["detail"]


def test_stop_is_pause_and_names_the_run_it_lets_finish(client, wired):
    wired["busy"] = {"id": "run-1", "title": "Login and auth"}
    r = client.post(f"/api/v1/agents/{PRINCIPAL}/stop")
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False, "finishing": "Login and auth"}
    assert wired["paused"] == [(WORKER, True)]
    assert wired["slot_updates"] == [(SLOT, {"desired_state": "paused"})]
    assert wired["pushed"] == [WORKER]
    assert wired["jobs"] == [], "stop never touches the service"


def test_a_non_owner_cannot_start_or_stop(client, wired, monkeypatch):
    async def deny(settings, token, fn, params):
        return False

    monkeypatch.setattr(agents, "rpc", deny)
    assert client.post(f"/api/v1/agents/{PRINCIPAL}/start").status_code == 403
    assert client.post(f"/api/v1/agents/{PRINCIPAL}/stop").status_code == 403
    assert wired["paused"] == []
