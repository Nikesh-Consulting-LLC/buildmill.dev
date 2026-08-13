"""US-85.1: Prepare Agent Workspace — the job endpoint, the prep.step relay,
and the orchestrator's honest endings.

DB helpers are monkeypatched (the SQL is trivial CRUD); the runner is a fake
on the far side of request_from_worker. No database, no network — Essential.
"""

import asyncio
import uuid

import pytest

from app import workspace_prep

ORG_ID = str(uuid.uuid4())
WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner",
    "type": "autonomous",
    "status": "active",
}
SESSION_ID = str(uuid.uuid4())

PROJECT = {
    "id": str(uuid.uuid4()),
    "name": "Demo",
    "repo_full_name": "acme/demo",
    "default_branch": "main",
    "slug": "demo",
    "org_shortname": "acme",
}


# --------------------------------------------------------------------------
# The checklist and the payload builders
# --------------------------------------------------------------------------


def test_initial_steps_are_the_eight_pending_steps():
    steps = workspace_prep.initial_steps()
    assert [s["key"] for s in steps] == list(workspace_prep.STEP_KEYS)
    assert len(steps) == 8
    assert all(s["status"] == "pending" for s in steps)
    assert all(s["label"] for s in steps)


def test_tool_server_bundle_shapes_and_skips(monkeypatch):
    monkeypatch.setattr(
        workspace_prep.db,
        "list_mcp_servers",
        lambda s, org: [
            {
                "slug": "browser",
                "name": "Browser",
                "enabled": True,
                "transport": "stdio",
                "needs_credential": False,
                "command": "npx playwright-mcp",
            },
            {
                "slug": "jira",
                "name": "Jira",
                "enabled": True,
                "transport": "http",
                "needs_credential": True,
                "command": None,
            },
            {
                "slug": "off",
                "name": "Disabled",
                "enabled": False,
                "transport": "stdio",
                "needs_credential": False,
                "command": "x",
            },
            {
                "slug": "factory",
                "name": "Shadow",
                "enabled": True,
                "transport": "http",
                "needs_credential": False,
            },
        ],
    )

    class S:
        api_base_url = "https://api.example.test/"

    bundle = workspace_prep.tool_server_bundle(S(), ORG_ID)
    by_slug = {b["slug"]: b for b in bundle}
    # enabled stdio rides as a local command; credentialed http rides as the
    # factory proxy URL with NO key; disabled and factory-shadowing are gone.
    assert by_slug["browser"]["transport"] == "stdio"
    assert by_slug["browser"]["command"] == "npx playwright-mcp"
    assert by_slug["jira"]["transport"] == "http"
    assert by_slug["jira"]["url"] == "https://api.example.test/api/v1/mcp-proxy/jira"
    assert "key" not in by_slug["jira"]
    assert set(by_slug) == {"browser", "jira"}


# --------------------------------------------------------------------------
# Step 8: the settings judgement
# --------------------------------------------------------------------------


def test_settings_check_fails_with_no_module(monkeypatch):
    monkeypatch.setattr(
        workspace_prep.db, "get_runner_config", lambda s, w: {"enabled_modules": []}
    )
    ok, detail = workspace_prep.settings_check(None, WORKER["id"])
    assert ok is False
    assert "no module" in detail


def test_settings_check_names_undeliverable_knobs(monkeypatch):
    monkeypatch.setattr(
        workspace_prep.db,
        "get_runner_config",
        lambda s, w: {
            "enabled_modules": ["grok"],
            "claude_billing": None,
            "model_overrides": {"plan": "grok-4.5"},
            "model_routes": {},
            "run_routes": {},
        },
    )
    monkeypatch.setattr(
        workspace_prep,
        "_latest_module_settings",
        lambda s, w: [{"module": "grok", "settings": [{"name": "model"}]}],
    )
    ok, detail = workspace_prep.settings_check(None, WORKER["id"])
    assert ok is True
    assert "plan→grok-4.5" in detail
    # The US-2.8.1 lesson: an undeliverable knob is NAMED, not silently dropped.
    assert "cannot be told: effort, max_turns" in detail


def test_settings_check_without_session_declarations(monkeypatch):
    monkeypatch.setattr(
        workspace_prep.db,
        "get_runner_config",
        lambda s, w: {"enabled_modules": ["claude"]},
    )
    monkeypatch.setattr(workspace_prep, "_latest_module_settings", lambda s, w: [])
    ok, detail = workspace_prep.settings_check(None, WORKER["id"])
    assert ok is True
    assert "module: claude" in detail
    assert "none pinned" in detail


# --------------------------------------------------------------------------
# The orchestrator
# --------------------------------------------------------------------------


@pytest.fixture
def prep_log(monkeypatch):
    """Record every job mutation the orchestrator makes, no database."""
    log = {"steps": [], "finished": []}
    monkeypatch.setattr(
        workspace_prep,
        "set_step",
        lambda s, job_id, key, status, detail="", worker_id=None: log["steps"].append(
            (key, status, detail)
        ),
    )
    monkeypatch.setattr(
        workspace_prep,
        "finish_job",
        lambda s, job_id, **kw: log["finished"].append(kw),
    )
    return log


def _run_orchestrator(monkeypatch, prep_log, reply):
    async def fake_request(worker_id, method, params, timeout=90):
        if isinstance(reply, Exception):
            raise reply
        assert method == "workspace.prepare"
        return reply

    monkeypatch.setattr(
        "app.routers.runner_socket.request_from_worker", fake_request
    )
    monkeypatch.setattr(
        workspace_prep, "settings_check", lambda s, w: (True, "module: claude")
    )
    asyncio.run(
        workspace_prep.run_prep_job(
            None, "job-1", WORKER["id"], {"project_id": "p", "remote": "r"}
        )
    )
    return prep_log


def test_orchestrator_success_closes_with_commit(monkeypatch, prep_log):
    log = _run_orchestrator(
        monkeypatch,
        prep_log,
        {"ok": True, "base_sha": "deadbeef", "workdir": "/w", "bytes": 5},
    )
    assert ("settings", "ok", "module: claude") in log["steps"]
    assert log["finished"] == [
        {
            "status": "succeeded",
            "error": None,
            "prepared_commit": "deadbeef",
            "workdir": "/w",
        }
    ]


def test_orchestrator_runner_failure_is_the_jobs_failure(monkeypatch, prep_log):
    log = _run_orchestrator(
        monkeypatch,
        prep_log,
        {"ok": False, "error": "bash not found", "failed_step": "checks"},
    )
    # The runner already painted the failing step; the job records the outcome
    # and step 8 is never judged.
    assert log["finished"] == [{"status": "failed", "error": "bash not found"}]
    assert not any(k == "settings" and s == "ok" for k, s, _ in log["steps"])


def test_orchestrator_disconnect_fails_the_invoke_step(monkeypatch, prep_log):
    fails = []
    monkeypatch.setattr(
        workspace_prep,
        "fail_step_and_job",
        lambda s, job_id, key, error: fails.append((key, error)),
    )
    _run_orchestrator(
        monkeypatch, prep_log, RuntimeError("runner is not connected")
    )
    assert fails == [("invoke", "runner is not connected")]


# --------------------------------------------------------------------------
# The endpoint
# --------------------------------------------------------------------------


@pytest.fixture
def job_endpoint_stubs(monkeypatch):
    """A connected, idle runner and an org with one project."""
    state = {"created": [], "launched": []}
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_worker",
        lambda s, wid: dict(WORKER) if wid == WORKER["id"] else None,
    )

    async def fake_rpc(s, token, fn, params):
        return True

    monkeypatch.setattr("app.routers.runner_socket.rpc", fake_rpc)
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_project_repo_by_id",
        lambda s, pid, org: dict(PROJECT),
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.is_worker_live", lambda wid: True
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.agent_provision.worker_is_busy",
        lambda s, wid: None,
    )

    def fake_create(s, **kw):
        state["created"].append(kw)
        return {"id": "job-1", "steps": workspace_prep.initial_steps()}

    monkeypatch.setattr(
        "app.routers.runner_socket.workspace_prep.create_job", fake_create
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.workspace_prep.tool_server_bundle",
        lambda s, org: [{"slug": "browser", "transport": "stdio", "command": "npx x"}],
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.workspace_prep.launch",
        lambda s, job_id, wid, payload: state["launched"].append((job_id, wid, payload)),
    )
    return state


def test_job_endpoint_creates_and_launches(client, job_endpoint_stubs, make_token):
    token = make_token()
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace-job",
        json={"project_id": PROJECT["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["job_id"] == "job-1"
    (job_id, wid, payload) = job_endpoint_stubs["launched"][0]
    assert job_id == "job-1"
    assert wid == WORKER["id"]
    assert payload["job_id"] == "job-1"
    assert payload["remote"].endswith("/git/acme/demo.git")
    assert payload["tool_servers"][0]["slug"] == "browser"


def test_job_endpoint_409s_when_runner_offline(
    client, job_endpoint_stubs, make_token, monkeypatch
):
    token = make_token()
    monkeypatch.setattr(
        "app.routers.runner_socket.is_worker_live", lambda wid: False
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace-job",
        json={"project_id": PROJECT["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "not connected" in resp.json()["detail"]
    assert job_endpoint_stubs["launched"] == []


def test_job_endpoint_409s_when_agent_mid_run(
    client, job_endpoint_stubs, make_token, monkeypatch
):
    token = make_token()
    monkeypatch.setattr(
        "app.routers.runner_socket.agent_provision.worker_is_busy",
        lambda s, wid: {"id": "r1", "title": "Story X"},
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace-job",
        json={"project_id": PROJECT["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert "Story X" in resp.json()["detail"]


def test_job_endpoint_hands_back_the_live_job_on_conflict(
    client, job_endpoint_stubs, make_token, monkeypatch
):
    token = make_token()

    def raise_active(s, **kw):
        raise workspace_prep.JobActive("already running")

    monkeypatch.setattr(
        "app.routers.runner_socket.workspace_prep.create_job", raise_active
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.workspace_prep.live_job",
        lambda s, wid, pid: {"id": "job-live"},
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace-job",
        json={"project_id": PROJECT["id"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["job_id"] == "job-live"


# --------------------------------------------------------------------------
# prep.step relay on the socket
# --------------------------------------------------------------------------


def test_dispatch_writes_prep_step_scoped_to_the_worker(monkeypatch):
    from app.routers import runner_socket

    written = []
    monkeypatch.setattr(
        runner_socket.workspace_prep,
        "set_step",
        lambda s, job_id, key, status, detail, worker_id=None: written.append(
            (job_id, key, status, detail, worker_id)
        ),
    )
    asyncio.run(
        runner_socket._dispatch(
            settings=None,
            websocket=None,
            worker=WORKER,
            session_id=SESSION_ID,
            msg={
                "jsonrpc": "2.0",
                "method": "prep.step",
                "params": {
                    "job_id": "job-1",
                    "step": "fetch",
                    "status": "ok",
                    "detail": "default branch at deadbeef12",
                },
            },
        )
    )
    assert written == [
        ("job-1", "fetch", "ok", "default branch at deadbeef12", WORKER["id"])
    ]
