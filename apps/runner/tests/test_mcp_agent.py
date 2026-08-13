"""US-31.9: an agent works through MCP, not git.

The properties worth pinning: the config points only at the factory and
carries the token; it is removed on success AND failure (it bears a secret);
the token never reaches a log; Claude gets --strict-mcp-config; a module that
cannot take MCP cannot take code/plan work and says which condition it fails.
"""

from __future__ import annotations

import asyncio
import json

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

