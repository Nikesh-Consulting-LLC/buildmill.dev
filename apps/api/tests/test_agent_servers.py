"""Agent server registry and fleet actions (Phase 26, US-26.1–26.9).

SSH and the job engine are mocked — what these cover is authorization, the
manage_org gate, cross-org isolation, the refusals (bad module, uninstalled
module in a template, busy agent, second job on one host), and the advisory
capacity guard.
"""

from __future__ import annotations

import pytest

from app import agent_provision

ORG = "654d7ff1-ab30-4812-a1ff-c9588d91ad50"
HOST_ID = "11111111-1111-4111-8111-111111111111"
SLOT_ID = "22222222-2222-4222-8222-222222222222"
SERVER_ID = "33333333-3333-4333-8333-333333333333"
WORKER_ID = "44444444-4444-4444-8444-444444444444"


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


HOST_ROW = {
    "id": HOST_ID,
    "org_id": ORG,
    "server_id": SERVER_ID,
    "status": "ready",
    "workdir": "/opt/buildmill",
    "modules": ["claude"],
    "slot_template": {},
    "cpu_count": 4,
    "disk_free_gb": 40.0,
    "auto_repair_enabled": True,
    "servers": {
        "id": SERVER_ID,
        "org_id": ORG,
        "host": "10.0.0.5",
        "port": 22,
        "username": "ops",
        "auth_method": "password",
        "host_key_fingerprint": "SHA256:abc",
    },
}

SLOT_ROW = {
    "id": SLOT_ID,
    "org_id": ORG,
    "agent_server_id": HOST_ID,
    "slot_index": 1,
    "name": "bravo-1",
    "worker_id": WORKER_ID,
    "status": "active",
    "desired_state": "paused",
}


@pytest.fixture
def fleet(monkeypatch):
    """A visible, ready host with one paused slot, and a job engine that
    records rather than SSHes anywhere."""
    state = {"jobs": [], "paused": [], "pushed": [], "patched": [], "posted": [], "busy": None}

    async def fake_get(settings, token, table, params):
        if table == "agent_servers":
            if params.get("server_id"):
                return []
            return [dict(HOST_ROW)]
        if table == "agent_slots":
            if params.get("worker_id"):
                return []          # not already bound elsewhere
            if params.get("id"):
                return [dict(SLOT_ROW)]
            return [dict(SLOT_ROW)]  # one live slot on the host
        if table == "workers":
            return [{"id": WORKER_ID, "name": "bravo-1", "org_id": ORG, "status": "active"}]
        return []

    async def fake_patch(settings, token, table, params, body):
        state["patched"].append((table, params, body))
        return [{**HOST_ROW, **body}]

    async def fake_post(settings, token, table, body):
        state["posted"].append((table, body))
        return [{**HOST_ROW, **body}]

    async def fake_rpc(settings, token, fn, args):
        assert fn == "has_org_capability"
        return args["p_capability"] == "manage_org"

    def fake_create_job(settings, **kwargs):
        state["jobs"].append(kwargs)
        return {"id": "job-1", **kwargs}

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.agent_servers.postgrest_patch", fake_patch)
    monkeypatch.setattr("app.routers.agent_servers.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.agent_servers.rpc", fake_rpc)
    monkeypatch.setattr(agent_provision, "create_job", fake_create_job)
    monkeypatch.setattr(agent_provision, "launch", lambda settings, ctx: None)
    monkeypatch.setattr(
        agent_provision, "worker_is_busy", lambda settings, worker_id: state["busy"]
    )
    monkeypatch.setattr(
        agent_provision,
        "set_paused",
        lambda settings, worker_id, paused: state["paused"].append((worker_id, paused)),
    )

    async def fake_push(settings, worker_id):
        state["pushed"].append(worker_id)
        return True

    monkeypatch.setattr("app.routers.agent_servers.runner_socket.push_config_update", fake_push)
    return state


# --- auth ------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("POST", "/api/v1/agent-servers"),
        ("PATCH", f"/api/v1/agent-servers/{HOST_ID}"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/provision"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/slots"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/update"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/teardown"),
        ("DELETE", f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}"),
    ],
)
def test_requires_auth(client, method, path):
    resp = client.request(method, path, json={})
    assert resp.status_code == 401


# --- cross-org isolation ---------------------------------------------------


def test_a_host_in_another_org_is_a_404(client, make_token, monkeypatch):
    async def hidden(settings, token, table, params):
        return []  # RLS hides other orgs' rows

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", hidden)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/provision", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 404


# --- the manage_org gate ---------------------------------------------------


def test_without_manage_org_every_action_is_403(client, make_token, fleet, monkeypatch):
    async def no_capability(settings, token, fn, args):
        return False

    monkeypatch.setattr("app.routers.agent_servers.rpc", no_capability)
    for method, path in (
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/provision"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/update"),
        ("POST", f"/api/v1/agent-servers/{HOST_ID}/teardown"),
        ("PATCH", f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}"),
    ):
        resp = client.request(
            method, path, json={"desired_state": "enabled"}, headers=_auth(make_token)
        )
        assert resp.status_code == 403, path


# --- registration validation (US-26.1 / US-26.6) ---------------------------


def _create_body(**over):
    return {"server_id": SERVER_ID, "workdir": "/opt/buildmill", **over}


@pytest.fixture
def registerable(monkeypatch, fleet, settings_override):
    async def fake_server(settings, token, server_id):
        return dict(HOST_ROW["servers"])

    async def ok_preflight(settings, user, server, workdir):
        return [{"check": "ssh", "ok": True, "detail": "Connected"}]

    monkeypatch.setattr(
        "app.routers.agent_servers.servers_router.get_server_for_user", fake_server
    )
    monkeypatch.setattr("app.routers.agent_servers._run_preflight", ok_preflight)
    # US-27.13: registration is refused outright while the factory's own
    # address is the default loopback, so a test that registers has to
    # configure one — the same thing the deployed .env has to do.
    monkeypatch.setattr(
        settings_override, "api_base_url", "https://api.buildmill.dev"
    )
    return fleet


def test_register_writes_the_row_after_preflight(client, make_token, registerable):
    resp = client.post("/api/v1/agent-servers", json=_create_body(), headers=_auth(make_token))
    assert resp.status_code == 200, resp.text
    assert registerable["posted"], "no row written"
    table, body = registerable["posted"][0]
    assert table == "agent_servers"
    assert body["status"] == "new"  # registered, nothing installed yet


def test_register_is_refused_while_the_factory_address_is_loopback(
    client, make_token, registerable, monkeypatch, settings_override
):
    """US-27.13: the first agent server provisioned cleanly and produced two
    agents told to dial http://localhost:8000 — the API's own default. No SSH
    session is opened to discover that; nothing can be reached on a remote
    machine's own loopback."""
    monkeypatch.setattr(settings_override, "api_base_url", "http://localhost:8000")

    async def never(settings, user, server, workdir):
        raise AssertionError("preflight must not run against an unusable address")

    monkeypatch.setattr("app.routers.agent_servers._run_preflight", never)
    resp = client.post(
        "/api/v1/agent-servers", json=_create_body(), headers=_auth(make_token)
    )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["checks"][0]["check"] == "factory-reachable"
    assert "API_BASE_URL" in detail["checks"][0]["detail"]
    assert "apps/api/.env" in detail["checks"][0]["detail"]


def test_register_refuses_a_machine_that_fails_preflight(
    client, make_token, registerable, monkeypatch
):
    async def bad_preflight(settings, user, server, workdir):
        return [
            {"check": "ssh", "ok": True, "detail": "Connected"},
            {"check": "os", "ok": False, "detail": "not a Debian-family machine (ID=alpine)"},
        ]

    monkeypatch.setattr("app.routers.agent_servers._run_preflight", bad_preflight)
    resp = client.post("/api/v1/agent-servers", json=_create_body(), headers=_auth(make_token))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "alpine" in str(detail)          # the reason is named, not generic
    assert not registerable["posted"]       # and nothing was written


def test_register_refuses_an_unknown_module(client, make_token, registerable):
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(modules=["claude", "codex"]),
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "codex" in resp.json()["detail"]


def test_register_refuses_a_relative_working_folder(client, make_token, registerable):
    resp = client.post(
        "/api/v1/agent-servers", json=_create_body(workdir="buildmill"), headers=_auth(make_token)
    )
    assert resp.status_code == 422


def test_template_cannot_enable_a_module_the_host_does_not_install(
    client, make_token, registerable
):
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(modules=["claude"], slot_template={"enabled_modules": ["opencode"]}),
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "opencode" in resp.json()["detail"]


def test_template_rejects_an_unknown_capability(client, make_token, registerable):
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(
            slot_template={"capabilities": [{"project_id": "p1", "capabilities": ["deploy", "sing"]}]}
        ),
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "sing" in resp.json()["detail"]


def test_registering_the_same_server_twice_is_refused(
    client, make_token, registerable, monkeypatch
):
    async def already(settings, token, table, params):
        if table == "agent_servers" and params.get("server_id"):
            return [{"id": HOST_ID, "status": "ready"}]
        return [dict(HOST_ROW)]

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", already)
    resp = client.post("/api/v1/agent-servers", json=_create_body(), headers=_auth(make_token))
    assert resp.status_code == 409


# --- jobs (US-26.2 / US-26.8) ---------------------------------------------


def test_provision_starts_one_job(client, make_token, fleet):
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/provision", json={"slots": 2}, headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert fleet["jobs"][0]["kind"] == "provision"


def test_a_second_job_on_one_host_is_refused(client, make_token, fleet, monkeypatch):
    def busy(settings, **kwargs):
        raise agent_provision.JobActive("This machine already has a job running.")

    monkeypatch.setattr(agent_provision, "create_job", busy)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/update", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_update_is_refused_before_the_machine_is_provisioned(
    client, make_token, fleet, monkeypatch
):
    async def unprovisioned(settings, token, table, params):
        return [{**HOST_ROW, "status": "new"}] if table == "agent_servers" else []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", unprovisioned)
    resp = client.post(f"/api/v1/agent-servers/{HOST_ID}/update", json={}, headers=_auth(make_token))
    assert resp.status_code == 409


def test_a_torn_down_host_reports_that_it_was_torn_down(client, make_token, fleet, monkeypatch):
    async def removed(settings, token, table, params):
        return [{**HOST_ROW, "status": "removed"}] if table == "agent_servers" else []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", removed)
    resp = client.post(f"/api/v1/agent-servers/{HOST_ID}/probe", json={}, headers=_auth(make_token))
    assert resp.status_code == 409
    assert "torn down" in resp.json()["detail"]


# --- capacity is advisory (US-26.7) ---------------------------------------


def test_adding_beyond_the_cpu_count_warns_with_the_numbers(
    client, make_token, fleet, monkeypatch
):
    async def small_box(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "cpu_count": 1, "disk_free_gb": 40.0}]
        if table == "agent_slots":
            return [dict(SLOT_ROW)]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", small_box)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots", json={"slots": 2}, headers=_auth(make_token)
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["confirmable"] is True
    assert "1 CPU core" in str(detail["reasons"])
    assert not fleet["jobs"]


def test_a_warned_capacity_can_be_confirmed_through(client, make_token, fleet, monkeypatch):
    async def small_box(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "cpu_count": 1}]
        if table == "agent_slots":
            return [dict(SLOT_ROW)]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", small_box)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 2, "confirm_capacity": True},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert fleet["jobs"][0]["kind"] == "add_slot"


def test_a_low_disk_warning_names_the_free_space(client, make_token, fleet, monkeypatch):
    async def full_box(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "cpu_count": 16, "disk_free_gb": 3.0}]
        return [dict(SLOT_ROW)] if table == "agent_slots" else []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", full_box)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots", json={"slots": 1}, headers=_auth(make_token)
    )
    assert resp.status_code == 409
    assert "3.0 GB free" in str(resp.json()["detail"]["reasons"])


# --- binding an existing agent (US-26.4) ----------------------------------


def test_binding_an_agent_already_on_a_host_is_refused(client, make_token, fleet, monkeypatch):
    async def bound_elsewhere(settings, token, table, params):
        if table == "agent_servers":
            return [dict(HOST_ROW)]
        if table == "workers":
            return [{"id": WORKER_ID, "name": "bravo-1", "org_id": ORG, "status": "active"}]
        if table == "agent_slots" and params.get("worker_id"):
            return [{"id": "other-slot", "name": "alpha-2", "agent_server_id": "other-host"}]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", bound_elsewhere)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 1, "adopt_worker_id": WORKER_ID, "confirm_capacity": True},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409
    assert "already runs on an agent server" in resp.json()["detail"]


def test_binding_an_agent_from_another_org_is_a_404(client, make_token, fleet, monkeypatch):
    async def foreign(settings, token, table, params):
        if table == "agent_servers":
            return [dict(HOST_ROW)]
        if table == "workers":
            return [{"id": WORKER_ID, "name": "x", "org_id": "other-org", "status": "active"}]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", foreign)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 1, "adopt_worker_id": WORKER_ID, "confirm_capacity": True},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


# --- slot controls (US-26.5) ----------------------------------------------


def test_enable_writes_the_pause_flag_and_pushes_it(client, make_token, fleet):
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}",
        json={"desired_state": "enabled"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert fleet["paused"] == [(WORKER_ID, False)]
    assert fleet["pushed"] == [WORKER_ID]
    assert resp.json()["pushed"] is True


def test_pausing_a_busy_agent_leaves_its_run_alone(client, make_token, fleet):
    fleet["busy"] = {"id": "run-9", "title": "US-1.2 Do the thing"}
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}",
        json={"desired_state": "paused"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert resp.json()["finishing"] == "US-1.2 Do the thing"
    assert fleet["paused"] == [(WORKER_ID, True)]


def test_an_unknown_desired_state_is_refused(client, make_token, fleet):
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}",
        json={"desired_state": "stopped-ish"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422


# --- removal (US-26.9) ----------------------------------------------------


def test_removing_a_busy_agent_is_refused_and_names_the_run(client, make_token, fleet):
    fleet["busy"] = {"id": "run-9", "title": "US-1.2 Do the thing"}
    resp = client.delete(
        f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}", headers=_auth(make_token)
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "US-1.2 Do the thing" in detail["message"]
    assert detail["forcible"] is True
    assert not fleet["jobs"]


def test_forcing_removal_of_a_busy_agent_runs_the_job(client, make_token, fleet):
    fleet["busy"] = {"id": "run-9", "title": "US-1.2 Do the thing"}
    resp = client.delete(
        f"/api/v1/agent-servers/{HOST_ID}/slots/{SLOT_ID}?force=true", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert fleet["jobs"][0]["kind"] == "remove_slot"


def test_teardown_defaults_to_keeping_the_working_folder(client, make_token, fleet, monkeypatch):
    launched = {}
    monkeypatch.setattr(agent_provision, "launch", lambda settings, ctx: launched.update(ctx))
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/teardown", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert launched["wipe_workdir"] is False
    assert launched["force"] is False


# --- a shared pool with tenants refuses to tear down (US-57.5) -------------

TENANT_ORG = "99999999-9999-4999-9999-999999999999"


def test_teardown_of_an_occupied_pool_is_refused_and_names_the_org(
    client, make_token, fleet, monkeypatch
):
    async def occupied(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "shared": True}]
        if table == "agent_slots":
            return [{"org_id": TENANT_ORG}]
        if table == "organizations":
            return [{"id": TENANT_ORG, "name": "Sandy's Workspace"}]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", occupied)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/teardown", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 409
    assert "Sandy's Workspace" in resp.json()["detail"]
    assert not fleet["jobs"]


def test_teardown_of_an_empty_shared_pool_proceeds(client, make_token, fleet, monkeypatch):
    async def empty_pool(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "shared": True}]
        if table == "agent_slots":
            return []  # no tenants (or only the platform's own, filtered out)
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", empty_pool)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/teardown", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 200


def test_teardown_of_a_non_shared_host_skips_the_occupancy_check(
    client, make_token, fleet, monkeypatch
):
    """A grandfathered single-org host never had a `shared` flag; a bare
    active slot there is the host's own org, not a tenant's — the check must
    not misfire and demand it be "vacated" of its own agents."""

    async def own_org_slot(settings, token, table, params):
        if table == "agent_servers":
            return [dict(HOST_ROW)]  # shared defaults False
        if table == "agent_slots":
            return [{"org_id": ORG}]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", own_org_slot)
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/teardown", json={}, headers=_auth(make_token)
    )
    assert resp.status_code == 200


# --- drift (US-26.8) ------------------------------------------------------


def test_current_version_reports_the_bundle_hash(client, make_token, fleet):
    resp = client.get("/api/v1/agent-servers/current-version", headers=_auth(make_token))
    assert resp.status_code == 200
    assert len(resp.json()["bundle_hash"]) == 16


# --- shared pools are the platform's to declare (US-57.1) ------------------


def _dual_rpc(is_platform_admin: bool):
    async def fake(settings, token, fn, args):
        if fn == "has_org_capability":
            return args["p_capability"] == "manage_org"
        if fn == "is_platform_admin":
            return is_platform_admin
        raise AssertionError(f"unexpected rpc: {fn}")

    return fake


def test_a_non_admin_cannot_register_a_shared_machine(
    client, make_token, registerable, monkeypatch
):
    monkeypatch.setattr("app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=False))
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(shared=True, pool_name="Alpha", capacity=4),
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert not registerable["posted"]


def test_a_platform_admin_can_register_a_shared_machine(
    client, make_token, registerable, monkeypatch
):
    monkeypatch.setattr("app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=True))
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(shared=True, pool_name="Alpha", capacity=4),
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    table, body = registerable["posted"][0]
    assert body["shared"] is True
    assert body["pool_name"] == "Alpha"
    assert body["capacity"] == 4


def test_a_shared_machine_needs_a_pool_name_and_capacity(
    client, make_token, registerable, monkeypatch
):
    monkeypatch.setattr("app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=True))
    resp = client.post(
        "/api/v1/agent-servers",
        json=_create_body(shared=True),
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert not registerable["posted"]


def test_a_non_admin_cannot_rename_an_existing_shared_pool(
    client, make_token, fleet, monkeypatch
):
    async def shared_host(settings, token, table, params):
        if table == "agent_servers":
            return [{**HOST_ROW, "shared": True, "pool_name": "Alpha", "capacity": 4}]
        return []

    monkeypatch.setattr("app.routers.agent_servers.postgrest_get", shared_host)
    monkeypatch.setattr("app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=False))
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}",
        json={"pool_name": "Renamed"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert not fleet["patched"]


# --- auto-repair on/off is the platform admin's call (US-68.3) -------------


def test_a_non_admin_cannot_turn_off_auto_repair(client, make_token, fleet, monkeypatch):
    monkeypatch.setattr(
        "app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=False)
    )
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}",
        json={"auto_repair_enabled": False},
        headers=_auth(make_token),
    )
    assert resp.status_code == 403
    assert not fleet["patched"]


def test_a_platform_admin_can_turn_off_auto_repair(client, make_token, fleet, monkeypatch):
    monkeypatch.setattr(
        "app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=True)
    )
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}",
        json={"auto_repair_enabled": False},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert fleet["patched"]


def test_saving_unrelated_settings_never_needs_platform_admin(
    client, make_token, fleet, monkeypatch
):
    """auto_repair_enabled rides the same generic PATCH as modules/extras/etc.
    (host-detail.tsx's SetupTab save) — an ordinary org manager touching only
    those must never 403, even though the field is technically present in
    the body, unchanged from the host's current value."""
    monkeypatch.setattr(
        "app.routers.agent_servers.rpc", _dual_rpc(is_platform_admin=False)
    )
    resp = client.patch(
        f"/api/v1/agent-servers/{HOST_ID}",
        json={"modules": ["claude"], "auto_repair_enabled": True},  # same as HOST_ROW
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text


# --- us-116.6: a new agent starts ready ------------------------------------


def test_add_slots_carries_desired_state_to_the_job(client, make_token, fleet, monkeypatch):
    launched = []
    monkeypatch.setattr(agent_provision, "launch", lambda settings, ctx: launched.append(ctx))
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 1, "confirm_capacity": True, "desired_state": "enabled"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert launched[0]["kind"] == "add_slot"
    assert launched[0]["desired_state"] == "enabled"


def test_add_slots_defaults_to_paused_and_refuses_an_unknown_state(client, make_token, fleet, monkeypatch):
    launched = []
    monkeypatch.setattr(agent_provision, "launch", lambda settings, ctx: launched.append(ctx))
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 1, "confirm_capacity": True},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert launched[0]["desired_state"] == "paused"
    resp = client.post(
        f"/api/v1/agent-servers/{HOST_ID}/slots",
        json={"slots": 1, "confirm_capacity": True, "desired_state": "on"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
