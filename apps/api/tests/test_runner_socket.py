"""US-10.1: supervisor-runner control socket — handshake, presence, heartbeat.

Endpoint-level via the Starlette TestClient's websocket support; the
`runner_sessions` DB helpers are monkeypatched (SQL is trivial CRUD).
"""

import json
import uuid

import pytest

ORG_ID = str(uuid.uuid4())
WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner",
    "type": "autonomous",
    "status": "active",
}
SESSION_ID = str(uuid.uuid4())


@pytest.fixture
def socket_stubs(monkeypatch):
    state = {"opened": {}, "touched": [], "closed": [], "upserted": {}}

    def fake_open(
        settings,
        worker_id,
        org_id,
        host_info=None,
        agent_versions=None,
        modules_available=None,
        # US-32.4: what each of those modules can be told.
        module_settings=None,
    ):
        state["opened"] = {
            "worker_id": worker_id,
            "org_id": org_id,
            "host_info": host_info,
            "agent_versions": agent_versions,
            "module_settings": module_settings,
            "modules_available": modules_available,
        }
        return SESSION_ID

    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_worker_by_token",
        lambda s, t: dict(WORKER) if t == "sfw_tok" else None,
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.open_runner_session", fake_open
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.touch_runner_session",
        lambda s, sid: state["touched"].append(sid),
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.close_runner_session",
        lambda s, sid: state["closed"].append(sid),
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_runner_config",
        lambda s, wid: {
            "enabled_modules": ["sim"],
            "model_routes": {},
            "autonomy_policy": {},
        },
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_worker",
        lambda s, wid: dict(WORKER) if wid == WORKER["id"] else None,
    )

    def fake_upsert(s, wid, org, **kw):
        state["upserted"] = {"worker_id": wid, "org": org, **kw}
        return {
            "enabled_modules": kw.get("enabled_modules") or ["sim"],
            "model_routes": kw.get("model_routes") or {},
            "autonomy_policy": kw.get("autonomy_policy") or {},
        }

    monkeypatch.setattr(
        "app.routers.runner_socket.db.upsert_runner_config", fake_upsert
    )

    async def fake_rpc(s, token, fn, params):
        return True

    monkeypatch.setattr("app.routers.runner_socket.rpc", fake_rpc)
    return state


def _hello(**params):
    return {"jsonrpc": "2.0", "id": 1, "method": "runner.hello", "params": params}


def test_handshake_opens_session_then_heartbeat_and_close(client, socket_stubs):
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(
            json.dumps(_hello(host_info={"hostname": "h"}, modules_available=["sim"]))
        )
        reply = json.loads(ws.receive_text())
        assert reply["result"]["session_id"] == SESSION_ID
        assert reply["result"]["config"]["enabled_modules"] == ["sim"]
        assert socket_stubs["opened"]["modules_available"] == ["sim"]
        assert socket_stubs["opened"]["worker_id"] == WORKER["id"]

        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "heartbeat"}))
        ack = json.loads(ws.receive_text())
        assert ack["result"] == {"ok": True}

    assert socket_stubs["closed"] == [SESSION_ID]
    assert SESSION_ID in socket_stubs["touched"]


def test_invalid_token_refused(client, socket_stubs):
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "bad"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        reply = json.loads(ws.receive_text())
        assert reply["error"]["code"] == 4401
    # never opened a session for a bad token
    assert socket_stubs["opened"] == {}


def test_first_frame_must_be_hello(client, socket_stubs):
    from starlette.testclient import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
        ) as ws:
            ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "heartbeat"}))
            ws.receive_text()


def test_unknown_method_errors(client, socket_stubs):
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())  # hello result
        ws.send_text(json.dumps({"jsonrpc": "2.0", "id": 9, "method": "bogus"}))
        reply = json.loads(ws.receive_text())
        assert reply["error"]["code"] == -32601


def test_config_patch_pushes_to_connected_runner(client, socket_stubs, make_token):
    token = make_token()
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())  # hello result registers the live socket

        resp = client.patch(
            f"/api/v1/runner/{WORKER['id']}/config",
            json={"enabled_modules": ["grok"]},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.json()["pushed"] is True
        assert socket_stubs["upserted"]["enabled_modules"] == ["grok"]

        pushed = json.loads(ws.receive_text())
        assert pushed["method"] == "config.update"
        assert pushed["params"]["config"]["enabled_modules"] == ["grok"]


def test_llm_infer_relays_to_server_brain(client, socket_stubs, monkeypatch):
    class FakeResult:
        text = "the answer"
        model = "claude-x"
        provider_name = "Anthropic"
        used_fallback = False

    async def fake_complete(settings, org_id, function_key, *, messages, temperature=None):
        assert org_id == ORG_ID
        return FakeResult()

    monkeypatch.setattr(
        "app.routers.runner_socket.llm.complete_as_org", fake_complete
    )
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "llm.infer",
                    "params": {
                        "route": "runner_brain",
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                }
            )
        )
        reply = json.loads(ws.receive_text())
        assert reply["result"]["completion"] == "the answer"
        assert reply["result"]["model"] == "claude-x"


def test_gateway_mint_returns_scoped_key(client, socket_stubs, monkeypatch):
    minted = {}

    def fake_mint(
        settings,
        org_id,
        worker_id,
        run_id=None,
        route="runner_brain",
        model=None,
        platform_billed=False,
    ):
        minted.update(
            org_id=org_id,
            worker_id=worker_id,
            run_id=run_id,
            route=route,
            # US-27.8: the model rides the mint so the gateway can pick the
            # provider that offers it.
            model=model,
            # US-60.1: whether this run bills the platform's own key.
            platform_billed=platform_billed,
        )
        return "sfg_scoped"

    monkeypatch.setattr("app.routers.runner_socket.db.mint_gateway_key", fake_mint)
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "gateway.mint",
                    "params": {
                        "run_id": "run-9",
                        "route": "runner_code",
                        "model": "claude-sonnet-5",
                    },
                }
            )
        )
        reply = json.loads(ws.receive_text())
        assert reply["result"]["key"] == "sfg_scoped"
        assert minted["route"] == "runner_code"
        assert minted["org_id"] == ORG_ID
        # US-27.8: `runner_code` is not a key in LLM_FUNCTIONS and routes
        # nowhere; the model is what tells the gateway who should answer.
        assert minted["model"] == "claude-sonnet-5"
        # US-60.1: this worker's config carries no claude_billing at all —
        # never platform-billed by default.
        assert minted["platform_billed"] is False


def test_gateway_mint_stamps_platform_billed_from_the_agents_own_config(
    client, socket_stubs, monkeypatch
):
    """US-60.1: whether a run bills the platform's own key is decided by the
    worker's OWN config, never by anything the caller sends over the
    socket."""
    minted = {}

    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_runner_config",
        lambda s, wid: {"enabled_modules": ["buildmill"], "claude_billing": "platform"},
    )

    def fake_mint(
        settings,
        org_id,
        worker_id,
        run_id=None,
        route="runner_brain",
        model=None,
        platform_billed=False,
    ):
        minted["platform_billed"] = platform_billed
        return "sfg_scoped"

    monkeypatch.setattr("app.routers.runner_socket.db.mint_gateway_key", fake_mint)
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 8,
                    "method": "gateway.mint",
                    "params": {"route": "runner_code", "model": "claude-sonnet-5"},
                }
            )
        )
        json.loads(ws.receive_text())
        assert minted["platform_billed"] is True


def test_command_audit_allows_and_records(client, socket_stubs, monkeypatch):
    recorded = {}

    def fake_record(settings, org_id, worker_id, session_id, run_id, argv, cwd, decision):
        recorded.update(decision=decision, argv=argv, run_id=run_id)
        return "audit-1"

    monkeypatch.setattr("app.routers.runner_socket.db.record_command_audit", fake_record)
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())
        ws.send_text(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "command.audit",
                    "params": {"run_id": "r1", "argv": ["git", "status"], "cwd": "/w"},
                }
            )
        )
        reply = json.loads(ws.receive_text())
        assert reply["result"]["allow"] is True
        assert reply["result"]["audit_id"] == "audit-1"
        assert recorded["decision"] == "allow"


def test_command_audit_denies_under_deny_policy(client, socket_stubs, monkeypatch):
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_runner_config",
        lambda s, wid: {
            "enabled_modules": ["sim"],
            "model_routes": {},
            "autonomy_policy": {"mode": "deny"},
        },
    )
    monkeypatch.setattr(
        "app.routers.runner_socket.db.record_command_audit",
        lambda *a, **k: "audit-2",
    )
    with client.websocket_connect(
        "/api/v1/runner/socket", headers={"X-Worker-Token": "sfw_tok"}
    ) as ws:
        ws.send_text(json.dumps(_hello()))
        json.loads(ws.receive_text())
        ws.send_text(
            json.dumps(
                {"jsonrpc": "2.0", "id": 6, "method": "command.audit", "params": {"argv": ["ls"]}}
            )
        )
        reply = json.loads(ws.receive_text())
        assert reply["result"]["allow"] is False


def test_config_patch_requires_capability(client, socket_stubs, make_token, monkeypatch):
    async def deny_rpc(s, token, fn, params):
        return False

    monkeypatch.setattr("app.routers.runner_socket.rpc", deny_rpc)
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["sim"]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 403


# ---------------------------------- US-57.6: platform-owned fields are gated


def _dual_rpc(is_platform_admin: bool):
    async def fake(s, token, fn, params):
        if fn == "has_org_capability":
            return True  # manage_work — the org gate, unaffected by this story
        if fn == "is_platform_admin":
            return is_platform_admin
        raise AssertionError(f"unexpected rpc: {fn}")

    return fake


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_routes", {"code": "claude-x"}),
        ("run_routes", {"code": {"preset_id": "11111111-1111-4111-8111-111111111111"}}),
        ("autonomy_policy", {"mode": "deny"}),
        ("max_run_minutes", 30),
        ("max_total_run_minutes", 60),
        ("max_item_attempts", 5),
    ],
)
def test_a_non_admin_cannot_set_a_platform_owned_field(
    client, socket_stubs, make_token, monkeypatch, field, value
):
    monkeypatch.setattr(
        "app.routers.runner_socket.rpc", _dual_rpc(is_platform_admin=False)
    )
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={field: value},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 403, resp.text
    assert not socket_stubs["upserted"]


def test_a_platform_admin_can_set_platform_owned_fields(
    client, socket_stubs, make_token, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.runner_socket.rpc", _dual_rpc(is_platform_admin=True)
    )
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"max_item_attempts": 5},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert socket_stubs["upserted"]["max_item_attempts"] == 5


def test_org_owned_fields_are_unaffected_for_a_non_admin(
    client, socket_stubs, make_token, monkeypatch
):
    """claude_billing/enabled_kinds/enabled_modules stay the org's to set —
    the six platform-owned fields are the only ones this story touches."""
    monkeypatch.setattr(
        "app.routers.runner_socket.rpc", _dual_rpc(is_platform_admin=False)
    )
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"claude_billing": "subscription", "enabled_kinds": ["code"]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text



# --------------------------------------- US-13.8: config validation + preview


def test_config_patch_rejects_invalid_regex(client, socket_stubs, make_token):
    """An unparseable pattern is refused at write time — runner_policy
    silently skips it at evaluation, so accepting it would store a rule
    the operator believes is blocking something while it does nothing."""
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"autonomy_policy": {"mode": "allow", "deny_patterns": ["[bad"]}},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422
    assert "[bad" in resp.json()["detail"]
    assert socket_stubs["upserted"] == {}


def test_config_patch_rejects_unknown_module_and_bad_bounds(
    client, socket_stubs, make_token
):
    token = make_token()
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["claude", "nonsense"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "nonsense" in resp.json()["detail"]

    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"autonomy_policy": {"mode": "yolo"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "yolo" in resp.json()["detail"]

    # US-31.2: lease bounds named in the refusal (−1 is the clear sentinel
    # and must pass).
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"max_run_minutes": 2000},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    assert "1440" in resp.json()["detail"]
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"max_run_minutes": 0},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"max_run_minutes": -1},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


# ----------------------------- US-60.1: platform billing is buildmill-only


def test_platform_billing_is_refused_without_the_buildmill_module(
    client, socket_stubs, make_token
):
    """The anti-loophole: without this, any org could bill the platform's
    key on a plain `claude` agent."""
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["claude"], "claude_billing": "platform"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422
    assert "platform" in resp.json()["detail"]
    assert socket_stubs["upserted"] == {}


def test_saving_buildmill_alone_forces_platform_billing(
    client, socket_stubs, make_token
):
    """Picking Buildmill Agent is minimal-setup — there is nothing to choose
    for billing, so whatever else was sent (or nothing at all) resolves to
    `platform`."""
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["buildmill"]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert socket_stubs["upserted"]["claude_billing"] == "platform"


def test_saving_buildmill_overrides_an_explicit_non_platform_billing(
    client, socket_stubs, make_token
):
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["buildmill"], "claude_billing": "api"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert socket_stubs["upserted"]["claude_billing"] == "platform"


def test_config_patch_reports_what_changed(client, socket_stubs, make_token):
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["grok"]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert "enabled_modules" in resp.json()["changed"]
    assert resp.json()["pushed"] is False  # no live socket in this test


def test_config_patch_refuses_concurrency_rather_than_dropping_it(
    client, socket_stubs, make_token
):
    """US-32.3: the column is gone because the runner never read it. A client
    still sending the field hears that it does nothing — silently accepting and
    discarding it is how a dead dial stays believable for six phases."""
    resp = client.patch(
        f"/api/v1/runner/{WORKER['id']}/config",
        json={"enabled_modules": ["sim"], "concurrency": 8},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422
    assert "concurrency" in resp.text
    assert socket_stubs["upserted"] == {}


def test_policy_preview_matches_evaluator(client, socket_stubs, monkeypatch, make_token):
    """The preview endpoint runs the same runner_policy.evaluate the shell
    audit path uses — allow / hold / block with the deciding pattern."""
    token = make_token()

    def config_with(policy):
        return lambda s, wid: {
            "enabled_modules": ["sim"],
            "model_routes": {},
            "autonomy_policy": policy,
        }

    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_runner_config",
        config_with({"mode": "allow", "deny_patterns": ["^rm -rf"]}),
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/policy-preview",
        json={"command": "rm -rf /"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "block"
    assert body["matched_pattern"] == "^rm -rf"

    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/policy-preview",
        json={"command": "pytest -q"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["decision"] == "allow"

    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_runner_config",
        config_with({"mode": "require-approval", "allow_patterns": ["^pytest"]}),
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/policy-preview",
        json={"command": "npm install"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["decision"] == "hold"
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/policy-preview",
        json={"command": "pytest -q"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.json()["decision"] == "allow"


# --------------------------------------------------------------------------
# workspace.prepare — server->runner request/reply (the symmetric half of
# the runner's own llm.infer-style requests)
# --------------------------------------------------------------------------


def test_request_from_worker_raises_when_not_connected():
    import asyncio

    from app.routers import runner_socket

    with pytest.raises(RuntimeError, match="not connected"):
        asyncio.run(
            runner_socket.request_from_worker(str(uuid.uuid4()), "workspace.prepare")
        )


def test_dispatch_resolves_pending_request_on_reply():
    import asyncio

    from app.routers import runner_socket

    async def _run():
        req_id = "srv-99"
        fut = asyncio.get_running_loop().create_future()
        runner_socket._PENDING[req_id] = fut
        try:
            # A reply frame has no `method` — just id + result, same shape
            # the runner's RunnerConnection.reply() sends.
            await runner_socket._dispatch(
                settings=None,
                websocket=None,
                worker=WORKER,
                session_id=SESSION_ID,
                msg={"jsonrpc": "2.0", "id": req_id, "result": {"ok": True, "base_sha": "abc"}},
            )
            return await fut
        finally:
            runner_socket._PENDING.pop(req_id, None)

    result = asyncio.run(_run())
    assert result == {"ok": True, "base_sha": "abc"}


def test_dispatch_rejects_pending_request_on_error_reply():
    import asyncio

    from app.routers import runner_socket

    async def _run():
        req_id = "srv-100"
        fut = asyncio.get_running_loop().create_future()
        runner_socket._PENDING[req_id] = fut
        try:
            await runner_socket._dispatch(
                settings=None,
                websocket=None,
                worker=WORKER,
                session_id=SESSION_ID,
                msg={"jsonrpc": "2.0", "id": req_id, "error": {"message": "boom"}},
            )
            return await fut
        finally:
            runner_socket._PENDING.pop(req_id, None)

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(_run())


def test_prepare_workspace_404s_on_unknown_project(client, socket_stubs, make_token, monkeypatch):
    token = make_token()
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_project_repo_by_id", lambda s, pid, org: None
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace",
        json={"project_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404


def test_prepare_workspace_returns_runner_result(client, socket_stubs, make_token, monkeypatch):
    token = make_token()
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_project_repo_by_id",
        lambda s, pid, org: {
            "id": pid,
            "name": "Demo",
            "repo_full_name": "acme/demo",
            "default_branch": "main",
            "slug": "demo",
            "org_shortname": "acme",
        },
    )

    async def fake_request(worker_id, method, params, timeout=90):
        assert method == "workspace.prepare"
        assert params["project_id"]
        assert params["remote"].endswith("/git/acme/demo.git")
        return {"ok": True, "base_sha": "deadbeef", "bytes": 123, "workdir": "/x"}

    monkeypatch.setattr(
        "app.routers.runner_socket.request_from_worker", fake_request
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace",
        json={"project_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["base_sha"] == "deadbeef"
    assert body["project"] == "Demo"


def test_prepare_workspace_409s_when_runner_offline(client, socket_stubs, make_token, monkeypatch):
    token = make_token()
    monkeypatch.setattr(
        "app.routers.runner_socket.db.get_project_repo_by_id",
        lambda s, pid, org: {
            "id": pid,
            "name": "Demo",
            "repo_full_name": "acme/demo",
            "default_branch": "main",
            "slug": "demo",
            "org_shortname": "acme",
        },
    )

    async def fake_request(worker_id, method, params, timeout=90):
        raise RuntimeError("runner is not connected")

    monkeypatch.setattr(
        "app.routers.runner_socket.request_from_worker", fake_request
    )
    resp = client.post(
        f"/api/v1/runner/{WORKER['id']}/prepare-workspace",
        json={"project_id": str(uuid.uuid4())},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
