"""US-78.10: opening and closing a session with no work item."""

import json

import pytest

from app.routers import agent_sessions, run_console, runner_socket


@pytest.fixture()
def wired(monkeypatch):
    """The reads the router does, and a runner that answers."""
    state = {
        "worker": {"id": "w-1", "name": "BM", "org_id": "org-1"},
        "modules": ["interactive"],
        "live": True,
        "opened": {"id": "sess-1", "git_remote_url": "https://f/git/o/p.git"},
        # us-116.1: the model is RESOLVED, not read — the fixture holds the
        # agent's config and the org's default preset, and the router asks the
        # same resolver a run's claim asks.
        "config": {"enabled_kinds": ["code"], "model_overrides": {"code": "grok-4.5"}},
        "org_default": None,
        "recorded": [],
        "minted": [],
        "requests": [],
        "finished": [],
        "marked": [],
    }

    async def _get(settings, token, path, params):
        if path == "workers":
            return [state["worker"]] if state["worker"] else []
        if path == "runner_config":
            return [{"enabled_modules": state["modules"]}]
        if path == "agent_sessions":
            return [{"id": "sess-1", "org_id": "org-1", "worker_id": "w-1", "status": "open"}]
        raise AssertionError(f"unfaked read: {path}")

    async def _rpc(settings, token, fn, args):
        # Pinned: the capability check must call the RPC that actually exists.
        # The original fake returned True for ANY name, which is how a call to
        # a nonexistent `has_capability` shipped and 403'd every session open.
        assert fn == "has_org_capability", f"unknown capability RPC: {fn}"
        assert set(args) == {"p_org", "p_capability"}, f"wrong args: {sorted(args)}"
        return True

    async def _request(worker_id, method, params=None, timeout=90):
        state["requests"].append((method, params))
        return {"ok": True, "acp_session_id": "acp-9", "workspace_path": "/w"}

    monkeypatch.setattr(agent_sessions, "postgrest_get", _get)
    monkeypatch.setattr(agent_sessions, "rpc", _rpc)
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: state["live"])
    monkeypatch.setattr(runner_socket, "request_from_worker", _request)
    monkeypatch.setattr(
        agent_sessions.db, "open_agent_session", lambda *a, **k: state["opened"]
    )
    monkeypatch.setattr(
        agent_sessions.db, "get_runner_config", lambda *a, **k: state["config"]
    )
    monkeypatch.setattr(
        agent_sessions.model_resolution.db, "presets_by_id", lambda *a, **k: {}
    )
    monkeypatch.setattr(
        agent_sessions.model_resolution.db,
        "org_default_preset",
        lambda *a, **k: state["org_default"],
    )
    # us-116.7: the org's default provider model — the floor. None by default
    # so every case above still exercises the layers above it.
    monkeypatch.setattr(
        agent_sessions.model_resolution.db,
        "org_default_provider_model",
        lambda *a, **k: state.get("floor"),
    )
    monkeypatch.setattr(
        agent_sessions.db,
        "record_session_model",
        lambda s, sid, model, kind: state["recorded"].append((sid, model, kind)),
    )

    def _mint(settings, org_id, worker_id, **kw):
        state["minted"].append({"org_id": org_id, "worker_id": worker_id, **kw})
        return "sfg_test_key"

    monkeypatch.setattr(agent_sessions.db, "mint_gateway_key", _mint)
    monkeypatch.setattr(
        agent_sessions.db,
        "mark_agent_session_open",
        lambda *a, **k: state["marked"].append(k),
    )
    monkeypatch.setattr(
        agent_sessions.db,
        "finish_agent_session",
        lambda s, sid, status, error=None: state["finished"].append((sid, status, error)),
    )
    return state


def _open(client, token, worker="w-1"):
    return client.post(
        "/api/v1/agent-sessions",
        json={"worker_id": worker, "project_id": "proj-1"},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_opening_a_session_reaches_the_runner_and_records_the_acp_id(
    client, make_token, wired
):
    res = _open(client, make_token())
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "open"
    # AC3: the timeout is stated before it bites
    assert body["idle_timeout_minutes"] == agent_sessions.IDLE_TIMEOUT_MINUTES
    method, params = wired["requests"][0]
    assert method == "session.open"
    assert params["session_id"] == "sess-1"
    assert wired["marked"][0]["acp_session_id"] == "acp-9"


def test_a_non_interactive_agent_has_no_session_to_open(client, make_token, wired):
    """The other modules run a one-shot command line — there is nothing to
    attach to, and saying so beats opening something that cannot work."""
    wired["modules"] = ["grok"]
    res = _open(client, make_token())
    assert res.status_code == 409
    assert "one-shot" in res.json()["detail"]
    assert wired["requests"] == []


def test_an_offline_agent_is_refused_before_a_row_is_written(client, make_token, wired):
    wired["live"] = False
    res = _open(client, make_token())
    assert res.status_code == 409
    assert "not connected" in res.json()["detail"]


def test_a_session_opens_with_a_real_gateway_env_not_a_stub(client, make_token, wired):
    """US-83.2: the old session_model_env returned {"model": ...} while the
    runner expected the full gateway env — no key minted, CLI credential-less,
    and the no-model refusal unable to fire. The shape is pinned here so a
    stub cannot satisfy this test again."""
    res = _open(client, make_token())
    assert res.status_code == 200
    _method, params = wired["requests"][0]
    env = params["model_env"]
    assert env["BUILDMILL_GATEWAY_KEY"] == "sfg_test_key"
    assert env["GROK_MODELS_BASE_URL"].endswith("/api/v1/llm-gateway/v1")
    assert env["GROK_XAI_API_BASE_URL"].endswith("/api/v1/llm-gateway/v1")
    assert env["GROK_DEFAULT_MODEL"] == "grok-4.5"
    # and the key was scoped to THIS session, so its calls meter under it
    mint = wired["minted"][0]
    assert mint["session_id"] == "sess-1"
    assert mint["route"] == "session"
    assert mint["model"] == "grok-4.5"


def test_an_agent_with_no_model_is_refused_before_anything_spawns(
    client, make_token, wired
):
    """US-78.5's rule, applied server-side: no model, no session — and the row
    must not stay 'opening' forever."""
    wired["config"] = {"enabled_kinds": ["code"], "model_overrides": {}}
    res = _open(client, make_token())
    assert res.status_code == 409
    assert "no model" in res.json()["detail"]
    assert wired["requests"] == [], "nothing may reach the machine"
    assert wired["minted"] == [], "no credential may be minted for a dead open"
    assert wired["finished"] == [("sess-1", "failed", "no model configured")]


def test_a_failed_mint_closes_the_row_and_names_the_factory_side(
    client, make_token, wired, monkeypatch
):
    def _boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(agent_sessions.db, "mint_gateway_key", _boom)
    res = _open(client, make_token())
    assert res.status_code == 502
    assert "factory-side" in res.json()["detail"]
    assert wired["requests"] == []
    assert wired["finished"] == [("sess-1", "failed", "db is down")]


def test_a_second_session_on_one_agent_is_refused(client, make_token, wired, monkeypatch):
    """The unique partial index is the enforcement; this is the sentence the
    manager gets instead of a 500."""
    monkeypatch.setattr(agent_sessions.db, "open_agent_session", lambda *a, **k: None)
    res = _open(client, make_token())
    assert res.status_code == 409
    assert "already holds a session" in res.json()["detail"]


def test_a_runner_that_refuses_marks_the_row_failed_rather_than_leaving_it_open(
    client, make_token, wired, monkeypatch
):
    async def _refuse(worker_id, method, params=None, timeout=90):
        return {"ok": False, "error": "no model to reason with"}

    monkeypatch.setattr(runner_socket, "request_from_worker", _refuse)
    res = _open(client, make_token())
    assert res.status_code == 502
    assert wired["finished"] == [("sess-1", "failed", "no model to reason with")]


def test_closing_closes_the_row_even_when_the_machine_is_unreachable(
    client, make_token, wired, monkeypatch
):
    """A session row left open against a process nobody can reach is how an
    agent stays held forever."""

    async def _dead(worker_id, method, params=None, timeout=90):
        raise RuntimeError("runner is not connected")

    monkeypatch.setattr(runner_socket, "request_from_worker", _dead)
    res = client.post(
        "/api/v1/agent-sessions/sess-1/close",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "closed"
    assert wired["finished"] == [("sess-1", "closed", None)]


def test_the_idle_sweep_closes_what_the_ui_promised_to_close(monkeypatch):
    """US-83.3 AC1: db.idle_agent_sessions existed from US-78.10 with zero
    callers — the 30-minute promise was UI copy. The sweep closes through the
    same path the Close button uses: ask a live machine, close the row
    regardless."""
    import asyncio

    calls = {"socket": [], "finished": []}
    monkeypatch.setattr(
        agent_sessions.db,
        "idle_agent_sessions",
        lambda s, m: [
            {"id": "sess-1", "worker_id": "w-live"},
            {"id": "sess-2", "worker_id": "w-dead"},
        ],
    )
    monkeypatch.setattr(
        agent_sessions.db,
        "finish_agent_session",
        lambda s, sid, status, error=None: calls["finished"].append((sid, status)),
    )
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: w == "w-live")

    async def _req(worker_id, method, params=None, timeout=90):
        calls["socket"].append((worker_id, method, params))
        return {"ok": True}

    monkeypatch.setattr(runner_socket, "request_from_worker", _req)

    closed = asyncio.run(agent_sessions.sweep_idle_sessions(object()))
    assert closed == 2
    # the live machine was asked; the dead one was not waited on
    assert calls["socket"] == [("w-live", "session.close", {"session_id": "sess-1"})]
    assert ("sess-1", "closed") in calls["finished"]
    assert ("sess-2", "closed") in calls["finished"]


def test_the_idle_sweep_closes_the_row_even_when_the_machine_hangs_up(monkeypatch):
    import asyncio

    finished = []
    monkeypatch.setattr(
        agent_sessions.db,
        "idle_agent_sessions",
        lambda s, m: [{"id": "sess-1", "worker_id": "w-1"}],
    )
    monkeypatch.setattr(
        agent_sessions.db,
        "finish_agent_session",
        lambda s, sid, status, error=None: finished.append((sid, status)),
    )
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)

    async def _boom(worker_id, method, params=None, timeout=90):
        raise RuntimeError("socket died mid-close")

    monkeypatch.setattr(runner_socket, "request_from_worker", _boom)
    closed = asyncio.run(agent_sessions.sweep_idle_sessions(object()))
    assert closed == 1
    assert finished == [("sess-1", "closed")]


def test_the_console_serves_a_session_the_same_way_it_serves_a_run(
    client, make_token, monkeypatch
):
    """One socket protocol and one client component: a session is a different
    owner of the conversation, not a different kind of conversation."""

    async def _get(settings, token, path, params):
        if path == "agent_sessions":
            return [{"id": "sess-1", "status": "open", "worker_id": "w-1", "org_id": "o"}]
        if path == "agent_session_events":
            return [
                {"kind": "output", "content": "hello from the session", "at": "2026-01-01"},
                {"kind": "step", "content": "stage:checkout 3ms", "at": "2026-01-01"},
            ]
        raise AssertionError(path)

    monkeypatch.setattr(run_console, "postgrest_get", _get)
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)
    with client.websocket_connect("/api/v1/runs/sessions/sess-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        hello = ws.receive_json()
    assert hello["type"] == "attached"
    assert hello["steerable"] is True
    contents = [t["content"] for t in hello["trace"]]
    assert "hello from the session" in contents
    assert not any(c.startswith("stage:") for c in contents)


# ---------------------------------------------------------------------------
# us-116.1: a session picks a model the agent actually has.
# ---------------------------------------------------------------------------

ARCHITECT_KINDS = ["prd", "breakdown", "plan", "guidelines", "elaborate", "wireframe"]


def test_an_architect_with_six_planning_roles_pinned_opens_a_session(
    client, make_token, wired
):
    """AC1: every role it claims is pinned to grok-4.5 and none of them is
    `code`. It has a model; it reasons with that model all day; the old
    `session_model` refused it for lacking a model for work it is configured
    never to do."""
    wired["worker"] = {"id": "w-1", "name": "Architect", "org_id": "org-1"}
    wired["config"] = {
        "enabled_kinds": ARCHITECT_KINDS,
        "model_overrides": {k: "grok-4.5" for k in ARCHITECT_KINDS},
    }
    res = _open(client, make_token())
    assert res.status_code == 200, res.text
    assert wired["minted"][0]["model"] == "grok-4.5"
    # AC3: the kind it resolved through is on the row — the first claimed
    # kind in ROUTE_KINDS order, since `code` is not claimed.
    assert wired["recorded"] == [("sess-1", "grok-4.5", "prd")]


def test_code_is_preferred_when_the_agent_claims_it(client, make_token, wired):
    """AC3/AC5: Programmer resolves exactly as it always did — `code` first."""
    wired["config"] = {
        "enabled_kinds": ["test", "code"],
        "model_overrides": {"test": "claude-haiku-4-5", "code": "grok-4.5"},
    }
    res = _open(client, make_token())
    assert res.status_code == 200
    assert wired["minted"][0]["model"] == "grok-4.5"
    assert wired["recorded"][0][2] == "code"


def test_a_null_enabled_kinds_claims_every_kind_and_resolves_through_code(
    client, make_token, wired
):
    """us-53.4: a never-saved agent is unrestricted, not benched — it tries
    `code` first exactly as before."""
    wired["config"] = {"enabled_kinds": None, "model_overrides": {"code": "grok-4.5"}}
    res = _open(client, make_token())
    assert res.status_code == 200
    assert wired["recorded"][0] == ("sess-1", "grok-4.5", "code")


def test_the_org_default_preset_reaches_a_session(client, make_token, wired):
    """AC6: an agent that pins nothing, in an org whose default preset names
    a model, opens on that model — the tier a run already gets."""
    wired["config"] = {"enabled_kinds": ["code"], "model_overrides": {}}
    wired["org_default"] = {
        "id": "p-bal", "name": "Balanced", "model": "claude-sonnet-5",
        "settings": {}, "version": 3, "tool_grants": [],
    }
    res = _open(client, make_token())
    assert res.status_code == 200
    assert wired["minted"][0]["model"] == "claude-sonnet-5"
    assert wired["recorded"][0] == ("sess-1", "claude-sonnet-5", "code")


def test_the_refusal_names_the_agent_the_roles_tried_and_both_places(
    client, make_token, wired
):
    """AC4: never a control that is not on the page. The org default is
    Balanced with model null — the message says so rather than implying the
    tier will cover it."""
    wired["worker"] = {"id": "w-1", "name": "Architect", "org_id": "org-1"}
    wired["config"] = {"enabled_kinds": ARCHITECT_KINDS, "model_overrides": {}}
    wired["org_default"] = {
        "id": "p-bal", "name": "Balanced", "model": None,
        "settings": {}, "version": 3, "tool_grants": [],
    }
    res = _open(client, make_token())
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail.startswith("Architect has no model for any of the roles it claims (Planning).")
    assert "Model per role" in detail
    assert "Balanced" in detail and "has none today" in detail
    assert "Programming" not in detail, "it must not name a role the agent does not claim"
    assert wired["finished"] == [("sess-1", "failed", "no model configured")]
    assert wired["recorded"] == []


def test_the_session_and_the_claim_build_the_resolver_arguments_in_one_place():
    """AC8: the seam. `run_settings.resolve(` is called from exactly one
    module in the app; a second copy of the precedence rules is how the
    session path drifted in the first place."""
    import re
    from pathlib import Path

    app_dir = Path(agent_sessions.__file__).resolve().parents[1]
    callers = sorted(
        str(p.relative_to(app_dir)).replace("\\", "/")
        for p in app_dir.rglob("*.py")
        if re.search(r"run_settings\.resolve\(", p.read_text(encoding="utf-8"))
    )
    assert callers == ["model_resolution.py"], callers
    # and both owners go through it
    from app.routers import worker

    assert "model_resolution.resolve_for_kind(" in Path(worker.__file__).read_text(encoding="utf-8")
    assert "model_resolution.resolve_session(" in Path(agent_sessions.__file__).read_text(encoding="utf-8")


def test_the_org_default_provider_model_reaches_a_session(client, make_token, wired):
    """us-116.7 AC2: DevOps — no pins, org default preset with no model, org
    default provider names grok-4.6 — opens on grok-4.6."""
    wired["worker"] = {"id": "w-1", "name": "DevOps", "org_id": "org-1"}
    wired["config"] = {"enabled_kinds": ["test", "release", "deploy"], "model_overrides": {}}
    wired["org_default"] = {"id": "p-bal", "name": "Balanced", "model": None,
                            "settings": {}, "version": 3, "tool_grants": []}
    wired["floor"] = "grok-4.6"
    res = _open(client, make_token())
    assert res.status_code == 200, res.text
    assert wired["minted"][0]["model"] == "grok-4.6"
    assert wired["recorded"] == [("sess-1", "grok-4.6", "test")]


def test_the_session_refusal_names_the_third_place(client, make_token, wired):
    wired["worker"] = {"id": "w-1", "name": "DevOps", "org_id": "org-1"}
    wired["config"] = {"enabled_kinds": ["release", "deploy"], "model_overrides": {}}
    wired["org_default"] = None
    wired["floor"] = None
    res = _open(client, make_token())
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert "(Deployment)" in detail
    assert "Settings → LLM providers" in detail
