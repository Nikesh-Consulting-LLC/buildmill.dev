"""US-78.5 / US-78.6: the Buildmill Interactive Agent's rules, server side."""

import pytest
from fastapi import HTTPException

from app.routers import agent_servers, runner_socket
from app.routers.llm_gateway import module_env


# -- US-78.5: platform LLM ---------------------------------------------------


def test_the_interactive_env_is_keyed_on_the_module_not_the_provider():
    """Two different programs speak `xai` here: the Grok Build module runs
    superagent-ai/grok-cli, the interactive one runs our fork of
    xai-org/grok-build. They read different variables."""
    grok = module_env("xai", "https://f/gw", "k", "grok-4.5")
    interactive = module_env("xai", "https://f/gw", "k", "grok-4.5", module="interactive")
    assert grok["GROK_API_KEY"] == "k"
    assert "GROK_API_KEY" not in interactive
    assert interactive["GROK_MODELS_BASE_URL"] == "https://f/gw/v1"
    # nothing may slip past the gateway to the real api.x.ai and off-meter
    assert interactive["GROK_XAI_API_BASE_URL"] == "https://f/gw/v1"
    assert interactive["BUILDMILL_GATEWAY_KEY"] == "k"


def test_the_interactive_module_is_known_but_not_provider_paired():
    """Its model is platform-owned, so validating it against the TENANT's
    providers would refuse a pairing the tenant does not choose and cannot
    fix — the same reason `buildmill` is absent from that map."""
    assert "interactive" in runner_socket.KNOWN_MODULES
    assert "interactive" not in runner_socket.MODULE_PROVIDER_TYPES


def test_a_platform_model_pairing_is_not_refused_for_an_interactive_agent():
    problem = runner_socket.validate_model_provider_pairing(
        ["interactive"],
        {"code": "grok-4.5"},
        [],  # the tenant has configured no providers at all
    )
    assert problem is None


# -- US-78.6: pool machines only --------------------------------------------


class _Recorder:
    def __init__(self, enabled_modules):
        self.enabled_modules = enabled_modules

    async def __call__(self, settings, token, path, params):
        assert path == "runner_config"
        return [{"enabled_modules": self.enabled_modules}]


@pytest.mark.anyio
async def test_an_interactive_agent_is_refused_a_slot_on_an_owned_machine(monkeypatch):
    monkeypatch.setattr(
        agent_servers, "postgrest_get", _Recorder(["interactive"])
    )
    with pytest.raises(HTTPException) as caught:
        await agent_servers._check_modules_allowed_on_host(
            None, _user(), {"shared": False, "org_id": "org-1"}, "w-1"
        )
    assert caught.value.status_code == 409
    assert "pool" in str(caught.value.detail)


@pytest.mark.anyio
async def test_a_pool_machine_accepts_it(monkeypatch):
    monkeypatch.setattr(
        agent_servers, "postgrest_get", _Recorder(["interactive"])
    )
    # shared host: allowed, and the config is not even read
    await agent_servers._check_modules_allowed_on_host(
        None, _user(), {"shared": True, "org_id": "org-1"}, "w-1"
    )


@pytest.mark.anyio
async def test_other_modules_are_untouched_on_an_owned_machine(monkeypatch):
    """The rule governs `interactive` only. Grok Build and OpenCode are
    deliberately placeable on a machine an org manages, and stay that way."""
    monkeypatch.setattr(
        agent_servers, "postgrest_get", _Recorder(["grok", "opencode"])
    )
    await agent_servers._check_modules_allowed_on_host(
        None, _user(), {"shared": False, "org_id": "org-1"}, "w-1"
    )


def _user():
    from app.auth import AuthUser

    return AuthUser(id="u-1", email="m@example.com", token="t")
