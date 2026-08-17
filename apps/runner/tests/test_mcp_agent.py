"""US-31.9: an agent works through MCP, not git.

The properties worth pinning: the config points only at the factory and
carries the token; it is removed on success AND failure (it bears a secret);
the token never reaches a log; Claude gets --strict-mcp-config; a module that
cannot take MCP cannot take code/plan work and says which condition it fails.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from supervisor import mcpconfig
from supervisor.modules import get as get_module
from supervisor.workloop import (
    module_can_do,
    select_module,
    why_no_module,
)

API = "https://api.buildmill.dev"
TOKEN = "sfw_supersecrettoken"


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------- the config


def test_config_points_only_at_the_factory_and_carries_the_token():
    body = mcpconfig.build(API, TOKEN, "proj-1")
    assert list(body["mcpServers"]) == ["factory"]
    server = body["mcpServers"]["factory"]
    assert server["url"] == f"{API}/mcp/"  # trailing slash: no 307 for MCP clients
    assert server["headers"]["X-Worker-Token"] == TOKEN


def test_write_then_remove(tmp_path):
    ws = tmp_path / "project-abc"
    path = mcpconfig.write(ws, API, TOKEN)
    assert path is not None and path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["mcpServers"]["factory"]
    mcpconfig.remove(ws)
    assert not path.exists()


def test_remove_is_safe_when_there_is_nothing_to_remove(tmp_path):
    mcpconfig.remove(tmp_path / "nope")  # must not raise


def test_no_token_or_url_writes_nothing(tmp_path):
    """Handing the CLI a config that cannot authenticate is worse than
    running without one and saying so."""
    assert mcpconfig.write(tmp_path / "a", API, "") is None
    assert mcpconfig.write(tmp_path / "b", "", TOKEN) is None


# ------------------------------------------------------------- module flags


def test_claude_declares_mcp_and_passes_strict():
    claude = get_module("claude")
    assert claude.supports_mcp is True
    argv = claude.mcp_argv("/w/.factory-mcp.json")
    assert "--mcp-config" in argv and "/w/.factory-mcp.json" in argv
    # Strict, so the tool surface is exactly what the factory granted.
    assert "--strict-mcp-config" in argv


def test_grok_declares_mcp_and_writes_its_own_config_toml(tmp_path):
    """Grok's real CLI has no --mcp-config flag â€” it discovers
    `.grok/config.toml` from the process cwd instead (US-10.5 follow-up)."""
    grok = get_module("grok")
    assert grok.supports_mcp is True
    json_path = mcpconfig.write(tmp_path, API, TOKEN, "proj-1")
    assert grok.mcp_argv(str(json_path)) == []
    toml = (tmp_path / ".grok" / "config.toml").read_text(encoding="utf-8")
    assert "[mcp_servers.factory]" in toml
    assert API in toml and TOKEN in toml


@pytest.mark.parametrize("name", ["opencode", "sim"])
def test_other_modules_do_not_claim_mcp(name):
    mod = get_module(name)
    assert getattr(mod, "supports_mcp", False) is False
    assert mod.mcp_argv("/w/x.json") == [] if hasattr(mod, "mcp_argv") else True


# ------------------------------------------- a module that cannot work says so


@pytest.mark.parametrize("kind", ["code", "plan"])
def test_non_mcp_module_refused_for_code_and_plan(kind):
    ok, why = module_can_do("opencode", kind)
    assert ok is False
    assert why is not None and "MCP" in why and kind in why


@pytest.mark.parametrize("kind", ["prd", "breakdown"])
def test_non_mcp_module_still_fine_for_stdout_kinds(kind):
    """prd and breakdown answer in their stdout and touch no repository, so
    MCP support is irrelevant to them."""
    ok, why = module_can_do("opencode", kind)
    assert ok is True and why is None


def test_select_module_skips_a_non_mcp_module_for_code():
    config = {"enabled_modules": ["opencode", "claude"]}
    assert select_module(config, "code") == "claude"


def test_select_module_returns_none_when_only_non_mcp_modules_are_enabled():
    config = {"enabled_modules": ["opencode"]}
    assert select_module(config, "code") is None


def test_route_preference_is_overridden_when_it_cannot_do_the_work():
    """A configured route pointing at a module that cannot take MCP must not
    win â€” otherwise the run fails instead of going to a module that can."""
    config = {"enabled_modules": ["opencode", "claude"], "module_routes": {"code": "opencode"}}
    assert select_module(config, "code") == "claude"


def test_why_no_module_names_the_condition_per_module():
    config = {"enabled_modules": ["opencode"]}
    why = why_no_module(config, "code")
    assert "opencode" in why
    assert "MCP" in why


def test_why_no_module_with_nothing_enabled():
    why = why_no_module({"enabled_modules": []}, "code")
    assert "no enabled module" in why and "none are enabled" in why


def test_sim_is_exempt_because_it_opens_no_repository():
    """The MCP requirement keys on needing a checkout, not on the run kind
    alone â€” otherwise it disqualifies the one module whose job is proving the
    pipeline without an agent."""
    sim = get_module("sim")
    assert sim.needs_repo is False
    for kind in ("code", "plan", "prd", "breakdown"):
        ok, why = module_can_do("sim", kind)
        assert ok is True, (kind, why)
    assert select_module({"enabled_modules": ["sim"]}, "plan") == "sim"


# ------------------------------------------------- the token stays out of logs


def test_token_is_not_in_the_argv_the_auditor_sees():
    """The audit trail records argv. The token lives in the config FILE, so
    the recorded command line must never contain it."""
    claude = get_module("claude")
    argv = claude.build_argv("do the thing", "code") + claude.mcp_argv(
        "/w/.factory-mcp.json"
    )
    assert TOKEN not in " ".join(argv)



# ------------------------------------------- us-115.1: the CLI's own dialect


def test_the_factory_entry_renders_as_toml_with_no_secret_in_the_text():
    """The whole point of moving the servers into the CLI's config: the file
    describes the surface, the environment carries the credential."""
    body = mcpconfig.build(API, TOKEN, "proj-1")
    text, env = mcpconfig.to_grok_toml(body, tool_timeout_sec=900)

    assert '[mcp_servers."factory"]' in text
    assert f'url = "{API}/mcp/"' in text
    assert 'bearer_token_env_var = "FACTORY_MCP_KEY"' in text
    assert "enabled = true" in text
    assert "startup_timeout_sec = 60" in text
    assert "tool_timeout_sec = 900" in text

    # The credential is in the mapping, and ONLY in the mapping.
    assert env == {"FACTORY_MCP_KEY": TOKEN}
    assert TOKEN not in text


def test_the_broker_shape_renders_the_same_way():
    """us-89.1's local key is a different secret in a different header, and it
    must be just as absent from the text."""
    body = {
        "mcpServers": {
            "factory": {
                "type": "http",
                "url": "http://127.0.0.1:41983/factory",
                "headers": {"X-Factory-Local-Key": "local-key-value"},
            }
        }
    }
    text, env = mcpconfig.to_grok_toml(body)
    assert 'url = "http://127.0.0.1:41983/factory"' in text
    assert 'bearer_token_env_var = "FACTORY_MCP_KEY"' in text
    assert env == {"FACTORY_MCP_KEY": "local-key-value"}
    assert "local-key-value" not in text


def test_granted_servers_ride_beside_the_factory_behind_placeholders():
    """US-34.2's servers keep the factory proxy's own header name, so the value
    goes behind a ${VAR} the CLI expands rather than a bearer token."""
    body = mcpconfig.build(
        API,
        TOKEN,
        None,
        [
            {"slug": "browser", "transport": "stdio", "command": "npx browser-mcp"},
            {
                "slug": "linear",
                "transport": "http",
                "url": "https://api.example/proxy/linear",
                "key": "scoped-key",
            },
        ],
    )
    text, env = mcpconfig.to_grok_toml(body)

    assert '[mcp_servers."browser"]' in text
    assert 'command = "npx"' in text
    assert 'args = ["browser-mcp"]' in text

    assert '[mcp_servers."linear"]' in text
    assert '"X-Factory-MCP-Key" = "${FACTORY_MCP_KEY_LINEAR}"' in text
    assert env["FACTORY_MCP_KEY_LINEAR"] == "scoped-key"
    assert "scoped-key" not in text
    assert TOKEN not in text


def test_a_server_with_no_url_or_command_is_dropped_not_half_written():
    text, env = mcpconfig.to_grok_toml(
        {
            "mcpServers": {
                "broken-http": {"type": "http", "url": ""},
                "broken-stdio": {"type": "stdio", "command": "  "},
            }
        }
    )
    assert text == ""
    assert env == {}


def test_the_probe_returns_the_tool_names_the_server_lists():
    """us-115.1 AC5: the run's proof, spoken before a model is paid to discover
    the tools are missing."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append({"method": body.get("method"), "headers": dict(request.headers)})
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"tools": [{"name": "get_work_context"}, {"name": "x"}]},
            },
        )

    async def go():
        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient

        def patched(*a, **kw):
            kw["transport"] = transport
            return real(*a, **kw)

        httpx.AsyncClient = patched  # noqa: SLF001 — narrow, restored below
        try:
            return await mcpconfig.probe(
                "https://api.example/mcp/", {"X-Worker-Token": TOKEN}
            )
        finally:
            httpx.AsyncClient = real

    names = asyncio.run(go())
    assert names == ["get_work_context", "x"]
    assert [s["method"] for s in seen] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
    ]
    # Both content types offered: a spec-following server 406s a client that
    # names only one.
    accept = seen[0]["headers"]["accept"]
    assert "application/json" in accept and "text/event-stream" in accept
    assert seen[0]["headers"]["x-worker-token"] == TOKEN


def test_a_refusing_server_raises_rather_than_returning_an_empty_surface():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text='{"error": "invalid or revoked worker token"}')

    async def go():
        transport = httpx.MockTransport(handler)
        real = httpx.AsyncClient

        def patched(*a, **kw):
            kw["transport"] = transport
            return real(*a, **kw)

        httpx.AsyncClient = patched
        try:
            await mcpconfig.probe("https://api.example/mcp/", {})
        finally:
            httpx.AsyncClient = real

    with pytest.raises(RuntimeError) as e:
        asyncio.run(go())
    assert "401" in str(e.value)


def test_the_rendered_config_is_valid_toml_that_parses_to_the_right_shape():
    """Substring assertions would pass on a file the CLI cannot read. This
    parses it the way the CLI's own loader does."""
    import tomllib

    body = mcpconfig.build(
        API,
        TOKEN,
        None,
        [
            {
                "slug": "linear",
                "transport": "http",
                "url": "https://api.example/proxy/linear",
                "key": "scoped-key",
            },
            {"slug": "browser", "transport": "stdio", "command": "npx browser-mcp"},
        ],
    )
    text, env = mcpconfig.to_grok_toml(body, tool_timeout_sec=1200)
    parsed = tomllib.loads(text)

    assert set(parsed["mcp_servers"]) == {"factory", "browser", "linear"}
    factory = parsed["mcp_servers"]["factory"]
    assert factory == {
        "url": f"{API}/mcp/",
        "bearer_token_env_var": "FACTORY_MCP_KEY",
        "enabled": True,
        "startup_timeout_sec": 60,
        "tool_timeout_sec": 1200,
    }
    assert parsed["mcp_servers"]["browser"]["args"] == ["browser-mcp"]
    assert parsed["mcp_servers"]["linear"]["headers"] == {
        "X-Factory-MCP-Key": "${FACTORY_MCP_KEY_LINEAR}"
    }
    # Every placeholder the text names has a value to expand from.
    assert set(env) == {"FACTORY_MCP_KEY", "FACTORY_MCP_KEY_LINEAR"}


def test_a_hostile_server_name_or_url_cannot_break_out_of_its_string():
    """The slug comes from the factory's catalog, but a renderer that trusts
    its input is one bad row from writing a config that means something else."""
    import tomllib

    text, _ = mcpconfig.to_grok_toml(
        {
            "mcpServers": {
                'ev"il': {
                    "type": "http",
                    "url": 'https://x/mcp"\nenabled = false',
                    "headers": {"X-Factory-MCP-Key": "k"},
                }
            }
        }
    )
    parsed = tomllib.loads(text)
    assert list(parsed["mcp_servers"]) == ['ev"il']
    assert parsed["mcp_servers"]['ev"il']["enabled"] is True


def test_the_holder_directory_goes_away_with_the_file(tmp_path):
    """us-115.1: an empty `project-x.mcp` left in the workspace root would be
    reported as a workspace by `workspace.usage()` and aged out by `reclaim()`."""
    from supervisor.modules.interactive import InteractiveModule

    workdir = tmp_path / "project-abc"
    workdir.mkdir()
    holder = InteractiveModule().mcp_config_dir(workdir)
    assert holder == tmp_path / "project-abc.mcp"

    written = mcpconfig.write(holder, API, TOKEN)
    assert written is not None and written.exists()
    mcpconfig.remove(holder)
    assert not holder.exists()
    # The checkout it sat beside is untouched.
    assert workdir.is_dir()


def test_remove_never_deletes_a_real_workspace(tmp_path):
    """Every other module passes its actual checkout here."""
    workdir = tmp_path / "project-abc"
    workdir.mkdir()
    (workdir / "README.md").write_text("real work", encoding="utf-8")
    mcpconfig.write(workdir, API, TOKEN)
    mcpconfig.remove(workdir)
    assert workdir.is_dir()
    assert (workdir / "README.md").exists()
    assert not (workdir / mcpconfig.CONFIG_NAME).exists()
