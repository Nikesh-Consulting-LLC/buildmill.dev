"""US-10.6: supervisor brain loop — module selection, submit mapping, run flow."""

import asyncio

from supervisor.workloop import (
    Supervisor,
    build_run_context,
    kind_enabled,
    model_env,
    result_to_payload,
    select_module,
    subscription_env,
    subscription_mode,
    why_no_module,
)
from supervisor import modules
from supervisor.modules.base import ModuleResult


def test_select_module_prefers_route_then_falls_back():
    cfg = {"enabled_modules": ["sim", "claude"], "module_routes": {"code": "claude"}}
    assert select_module(cfg, "code") == "claude"
    # no route for plan -> first enabled capable module
    assert select_module({"enabled_modules": ["sim"]}, "plan") == "sim"
    # nothing enabled -> None
    assert select_module({"enabled_modules": []}, "code") is None
    # preferred not enabled -> fall back
    assert select_module({"enabled_modules": ["sim"], "module_routes": {"code": "grok"}}, "code") == "sim"


def test_result_to_payload_success_and_failure():
    ok = result_to_payload(
        ModuleResult(outcome="succeeded", branch_ref="b", test_cases=[{"title": "t"}])
    )
    assert ok["branch_ref"] == "b" and ok["test_cases"]
    assert "error" not in ok
    # US-10.11: failures also carry a fault_class (work vs runner fault)
    bad = result_to_payload(ModuleResult(outcome="failed", error="boom"))
    assert bad == {"error": "boom", "fault_class": "work-fault"}


# --------------------------------------------------------------------- US-53.4
def test_kind_checkboxes_gate_selection():
    cfg = {"enabled_modules": ["sim"], "enabled_kinds": ["plan"]}
    assert kind_enabled(cfg, "plan") is True
    assert kind_enabled(cfg, "code") is False
    assert select_module(cfg, "plan") == "sim"
    # An unchecked kind is refused before any module is consulted, with a
    # reason naming the checkbox — the run stays poolable for other agents.
    assert select_module(cfg, "code") is None
    assert "unchecked" in why_no_module(cfg, "code")
    # Null/absent = all kinds: a pre-checkbox config keeps today's behavior.
    assert select_module({"enabled_modules": ["sim"]}, "code") == "sim"
    # [] = a deliberately benched agent.
    benched = {"enabled_modules": ["sim"], "enabled_kinds": []}
    assert select_module(benched, "plan") is None
    assert "unchecked" in why_no_module(benched, "plan")


def test_model_env_shapes():
    assert model_env("anthropic", "http://g", "k", "m")["ANTHROPIC_API_KEY"] == "k"
    # Measured live against the real grok CLI's current generation (1.1.7,
    # US-10.5 follow-up): GROK_API_KEY / GROK_BASE_URL / GROK_MODEL.
    xai = model_env("xai", "http://g", "k", "grok-4.5")
    assert xai["GROK_API_KEY"] == "k"
    assert xai["GROK_BASE_URL"] == "http://g/v1"
    assert xai["GROK_MODEL"] == "grok-4.5"


# ------------------------------------------------------- US-52.1 → US-53.1
def test_subscription_mode_is_the_agents_switch_plus_the_knob():
    claude = modules.get("claude")
    grok = modules.get("grok")
    # The mode is the AGENT's config switch, honored only by a module that
    # declared `auth` (us-53.1 moved it off the resolved settings entirely).
    assert subscription_mode(claude, {"claude_billing": "subscription"}) is True
    assert subscription_mode(claude, {"claude_billing": "api"}) is False
    assert subscription_mode(claude, {}) is False
    assert subscription_mode(claude, None) is False
    # A resolved-settings-shaped dict no longer flips anything.
    assert subscription_mode(claude, {"auth": "subscription"}) is False
    # Grok never declared `auth`; the switch must not strip its gateway env.
    assert subscription_mode(grok, {"claude_billing": "subscription"}) is False


def test_subscription_env_is_an_absence():
    env = subscription_env("claude-sonnet-5")
    # The whole point: no API-key variable may shadow the machine's own
    # subscription credential in Claude Code's precedence chain.
    assert env == {"ANTHROPIC_MODEL": "claude-sonnet-5"}
    for forbidden in ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
        assert forbidden not in env
    assert subscription_env("") == {}


def test_subscription_env_carries_the_factory_token_when_given():
    # US-52.2: the factory-held token rides as CLAUDE_CODE_OAUTH_TOKEN — and
    # STILL no API-key variable, or the token itself would be shadowed.
    env = subscription_env("claude-sonnet-5", "sk-ant-oat01-abc")
    assert env == {
        "ANTHROPIC_MODEL": "claude-sonnet-5",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat01-abc",
    }
    # No token (org holds none / older server) → us-52.1's machine-held shape.
    assert subscription_env("claude-sonnet-5", None) == {
        "ANTHROPIC_MODEL": "claude-sonnet-5"
    }


def test_claude_declares_auth_with_both_modes():
    claude = modules.get("claude")
    knob = next(k for k in claude.settings if k.name == "auth")
    assert knob.kind == "enum"
    assert knob.delivery == "runner"
    # US-60.1: `platform` bills the superadmin's own key (Buildmill Agent) —
    # the runner never distinguishes it from `api`, both ride the gateway.
    assert knob.choices == ("api", "subscription", "platform")


class FakeClient:
    def __init__(self, bundle):
        self.bundle = bundle
        self.submitted = None
        self.beats = 0

    async def get_context(self, run_id):
        return self.bundle

    async def submit(self, run_id, payload):
        self.submitted = payload

    async def heartbeat(self, run_id):
        self.beats += 1

    async def list_pool(self):
        return {"runs": []}

    async def claim(self, run_id):
        return None


def _bundle(kind):
    return {
        "run_id": "r1",
        "kind": kind,
        "context": {"title": "Widget"},
        "branch_name": "factory/widget",
        "git_remote_url": "https://f/git/o/p.git",
        "default_branch": "main",
    }


def test_run_claimed_code_submits_branch_with_sim():
    client = FakeClient(_bundle("code"))
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": ["sim"]})
    result = asyncio.run(sup.run_claimed("r1"))
    assert result.outcome == "succeeded"
    assert client.submitted["branch_ref"] == "factory/widget"
    assert client.submitted["test_cases"]


def test_run_claimed_plan_submits_plan():
    client = FakeClient(_bundle("plan"))
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": ["sim"]})
    asyncio.run(sup.run_claimed("r1"))
    assert client.submitted["plan"]
    assert client.submitted["test_plan"]


def test_run_claimed_no_module_submits_error():
    client = FakeClient(_bundle("code"))
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": []})
    asyncio.run(sup.run_claimed("r1"))
    assert "error" in client.submitted
    assert "no enabled module" in client.submitted["error"]


# --------------------------------------------------------------------- US-63.x
def test_heartbeat_survives_a_failed_beat(monkeypatch):
    """A factory-api restart fails whichever beat lands mid-outage. The old
    behavior let that exception escape and end the whole heartbeat task,
    silently stopping every beat for the rest of the run. One failed beat
    must not stop the next one from being attempted."""
    from supervisor import workloop

    monkeypatch.setattr(workloop, "HEARTBEAT_SECONDS", 0.01)

    class FlakyClient(FakeClient):
        def __init__(self, bundle):
            super().__init__(bundle)
            self.calls = 0

        async def heartbeat(self, run_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("connection refused")
            await super().heartbeat(run_id)

    client = FlakyClient(_bundle("code"))
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": ["sim"]})
    stop = asyncio.Event()

    async def run():
        task = asyncio.create_task(sup._heartbeat("r1", stop))
        await asyncio.sleep(0.05)
        stop.set()
        await task

    asyncio.run(run())
    # The old behavior would leave calls == 1 forever (task died on the first
    # exception). A second attempted beat proves the loop kept going.
    assert client.calls >= 2
    assert client.beats >= 1
