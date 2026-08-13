"""Tenant pool placement (US-57.3).

SSH and the job engine are mocked, matching test_agent_servers.py's own
convention — what these cover is authorization (the caller's OWN org
capability, not the pool's), the worker/pool existence checks, and the
pool's hard capacity refusal.
"""

from __future__ import annotations

import pytest

from app import agent_provision

WORKER_ORG = "654d7ff1-ab30-4812-a1ff-c9588d91ad50"
PLATFORM_ORG = "42ed41e9-1253-4fd0-a57b-0404611c0429"
POOL_ID = "55555555-5555-4555-8555-555555555555"
WORKER_ID = "66666666-6666-4666-8666-666666666666"


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


POOL_ROW = {
    "id": POOL_ID,
    "org_id": PLATFORM_ORG,
    "status": "ready",
    "shared": True,
    "pool_name": "Alpha",
    "capacity": 4,
}

WORKER_ROW = {"id": WORKER_ID, "org_id": WORKER_ORG, "status": "active"}


@pytest.fixture
def pool(monkeypatch):
    state = {"jobs": [], "launched": None}

    async def fake_postgrest_get(settings, token, table, params):
        if table == "workers":
            return [dict(WORKER_ROW)]
        if table == "agent_slots":
            return []  # not already bound elsewhere
        return []

    async def fake_admin_get(settings, table, params):
        if table == "agent_servers":
            return [dict(POOL_ROW)]
        if table == "agent_slots":
            return []  # no slots placed yet -> full capacity free
        return []

    async def fake_rpc(settings, token, fn, args):
        assert fn == "has_org_capability"
        return args["p_capability"] == "develop"

    def fake_create_job(settings, **kwargs):
        state["jobs"].append(kwargs)
        return {"id": "job-1", **kwargs}

    def fake_launch(settings, ctx):
        state["launched"] = ctx

    monkeypatch.setattr("app.routers.agent_pools.postgrest_get", fake_postgrest_get)
    monkeypatch.setattr("app.routers.agent_pools.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.agent_pools.rpc", fake_rpc)
    monkeypatch.setattr(agent_provision, "create_job", fake_create_job)
    monkeypatch.setattr(agent_provision, "launch", fake_launch)
    return state


def test_requires_auth(client):
    resp = client.post(f"/api/v1/agent-pools/{POOL_ID}/place", json={"worker_id": WORKER_ID})
    assert resp.status_code == 401


def test_happy_path_places_the_worker_on_the_pool(client, make_token, pool):
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pool_name"] == "Alpha"
    assert pool["jobs"][0]["kind"] == "add_slot"
    assert pool["jobs"][0]["org_id"] == PLATFORM_ORG  # the job stays platform-owned
    assert pool["launched"]["adopt_worker_id"] == WORKER_ID


def test_a_caller_without_develop_on_the_workers_org_is_403(client, make_token, pool, monkeypatch):
    async def no_capability(settings, token, fn, args):
        return False

    monkeypatch.setattr("app.routers.agent_pools.rpc", no_capability)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert not pool["jobs"]


def test_an_unknown_worker_is_404(client, make_token, pool, monkeypatch):
    async def no_worker(settings, token, table, params):
        return [] if table == "workers" else []

    monkeypatch.setattr("app.routers.agent_pools.postgrest_get", no_worker)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404
    assert not pool["jobs"]


def test_a_worker_already_on_a_slot_is_refused(client, make_token, pool, monkeypatch):
    async def bound(settings, token, table, params):
        if table == "workers":
            return [dict(WORKER_ROW)]
        if table == "agent_slots":
            return [{"id": "other-slot"}]
        return []

    monkeypatch.setattr("app.routers.agent_pools.postgrest_get", bound)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert not pool["jobs"]


def test_an_unshared_or_missing_pool_is_404(client, make_token, pool, monkeypatch):
    async def not_a_pool(settings, table, params):
        if table == "agent_servers":
            return [{**POOL_ROW, "shared": False}]
        return []

    monkeypatch.setattr("app.routers.agent_pools.admin_get", not_a_pool)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404
    assert not pool["jobs"]


def test_a_pool_not_ready_refuses(client, make_token, pool, monkeypatch):
    async def not_ready(settings, table, params):
        if table == "agent_servers":
            return [{**POOL_ROW, "status": "provisioning"}]
        return []

    monkeypatch.setattr("app.routers.agent_pools.admin_get", not_ready)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert not pool["jobs"]


def test_a_full_pool_refuses_and_names_itself(client, make_token, pool, monkeypatch):
    async def full(settings, table, params):
        if table == "agent_servers":
            return [dict(POOL_ROW)]
        if table == "agent_slots":
            return [{"id": f"slot-{i}"} for i in range(4)]  # capacity 4, all taken
        return []

    monkeypatch.setattr("app.routers.agent_pools.admin_get", full)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert "Alpha" in resp.json()["detail"]
    assert not pool["jobs"]


def test_a_second_job_on_the_pool_is_queued_instead_of_refused(client, make_token, pool, monkeypatch):
    """US-57.3 follow-on, 2026-07-31: the host's one-job-per-host lock being
    held is not the tenant's problem to retry by hand — the request is
    acknowledged and `pool_placement_sweep` places it once the lock frees."""

    def busy(settings, **kwargs):
        raise agent_provision.JobActive("This machine already has a job running.")

    enqueued = {}

    def fake_enqueue(settings, **kwargs):
        enqueued.update(kwargs)

    monkeypatch.setattr(agent_provision, "create_job", busy)
    monkeypatch.setattr(agent_provision, "upsert_pool_placement_request", fake_enqueue)
    resp = client.post(
        f"/api/v1/agent-pools/{POOL_ID}/place",
        json={"worker_id": WORKER_ID},
        headers=_auth(make_token),
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["queued"] is True
    assert body["job_id"] is None
    assert enqueued["org_id"] == PLATFORM_ORG
    assert enqueued["pool_id"] == POOL_ID
    assert enqueued["worker_id"] == WORKER_ID


# --- slot state, authorized by the slot's own org, not the host's ---------

SLOT_ID = "77777777-7777-4777-8777-777777777777"
SLOT_ROW = {
    "id": SLOT_ID,
    "org_id": WORKER_ORG,
    "worker_id": WORKER_ID,
    "status": "active",
    "desired_state": "paused",
}


@pytest.fixture
def slot(monkeypatch):
    state = {"paused": [], "pushed": [], "patched": []}

    async def fake_get(settings, token, table, params):
        return [dict(SLOT_ROW)] if table == "agent_slots" else []

    async def fake_patch(settings, token, table, params, body):
        state["patched"].append((table, params, body))
        return [{**SLOT_ROW, **body}]

    async def fake_rpc(settings, token, fn, args):
        assert fn == "has_org_capability"
        assert args["p_org"] == WORKER_ORG  # the SLOT's org, never the host's
        return args["p_capability"] == "manage_org"

    async def fake_push(settings, worker_id):
        state["pushed"].append(worker_id)
        return True

    monkeypatch.setattr("app.routers.agent_pools.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.agent_pools.postgrest_patch", fake_patch)
    monkeypatch.setattr("app.routers.agent_pools.rpc", fake_rpc)
    monkeypatch.setattr(
        agent_provision, "set_paused",
        lambda settings, worker_id, paused: state["paused"].append((worker_id, paused)),
    )
    monkeypatch.setattr("app.routers.agent_pools.runner_socket.push_config_update", fake_push)
    return state


def test_enabling_a_pool_slot_authorizes_on_its_own_org(client, make_token, slot):
    resp = client.patch(
        f"/api/v1/agent-pools/slots/{SLOT_ID}",
        json={"desired_state": "enabled"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert slot["paused"] == [(WORKER_ID, False)]
    assert slot["pushed"] == [WORKER_ID]
    assert slot["patched"][0][2] == {"desired_state": "enabled"}


def test_a_caller_without_manage_org_on_the_slots_org_is_403(client, make_token, slot, monkeypatch):
    async def no_capability(settings, token, fn, args):
        return False

    monkeypatch.setattr("app.routers.agent_pools.rpc", no_capability)
    resp = client.patch(
        f"/api/v1/agent-pools/slots/{SLOT_ID}",
        json={"desired_state": "enabled"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert not slot["paused"]


def test_an_invisible_slot_is_404(client, make_token, monkeypatch):
    async def hidden(settings, token, table, params):
        return []  # RLS hides another org's slot

    monkeypatch.setattr("app.routers.agent_pools.postgrest_get", hidden)
    resp = client.patch(
        f"/api/v1/agent-pools/slots/{SLOT_ID}",
        json={"desired_state": "enabled"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404
