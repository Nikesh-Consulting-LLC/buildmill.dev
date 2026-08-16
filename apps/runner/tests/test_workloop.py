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

    async def claim_alive(self, run_id):
        # us-96.9 AC4: the preflight probe — this double's claims are live.
        return True, None

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


# ---------------------------------------------------------------- us-96.9
# A stop is an answer, not a breakdown.


def test_a_stopped_result_never_selects_a_repair_action():
    """AC1: the field short-circuits before any keyword loop — the words
    'the session was cancelled' alone must never buy a wait or a reclone."""
    from supervisor.repair import classify, classify_fault

    r = ModuleResult(
        outcome="failed",
        error="the session was cancelled",
        stopped=True,
    )
    assert classify(r) == "unrecoverable"
    # And the keyword fault logic is bypassed too.
    assert classify_fault(r) == "work-fault"


def test_a_stopped_payload_carries_no_fault_class():
    """AC2/AC3: neither the box nor the story failed — the payload names
    the decision and omits the classification entirely."""
    from supervisor.workloop import result_to_payload

    r = ModuleResult(outcome="failed", error="whatever the CLI said", stopped=True)
    payload = result_to_payload(r)
    assert payload["error"] == "stopped by the manager"
    assert "fault_class" not in payload
    assert "pause_reason" not in payload


def test_run_claimed_never_boots_on_a_dead_claim():
    """AC4: a lost/expired/stopped claim aborts before the CLI is invoked,
    releases quietly, and submits nothing — the 2026-08-14 zombie."""

    class DeadClaimClient(FakeClient):
        def __init__(self, bundle):
            super().__init__(bundle)
            self.released = None

        async def claim_alive(self, run_id):
            return False, "no live claim on this run to extend"

        async def release(self, run_id, note=""):
            self.released = note

    client = DeadClaimClient(_bundle("code"))
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": ["sim"]})
    result = asyncio.run(sup.run_claimed("r1"))
    assert result is None
    assert client.submitted is None, "a dead claim must not be submitted against"
    assert client.released and "claim lost" in client.released


# --------------------------------------------------------------------- US-103.2
# A restarted runner re-adopts the prep it was already holding.
#
# On 2026-08-16 the runner restarted ten minutes into preparing release
# 2026.08.16.3. Its supervising task died with the process; the
# release_prep_runs row did not. Two and a half hours later the row was still
# `running`, the runner was online and healthy, and it had no idea the job
# existed — because the pool it polls asks for `queued`, so the job it held
# was invisible to it precisely because it held it.


class PrepClient(FakeClient):
    """A worker client with the release-prep contract on it."""

    def __init__(self, held=None, pool=None):
        super().__init__(_bundle("code"))
        self.held = held or []
        self.prep_pool = pool or []
        self.claims = []
        self.failed = []
        self.prep_beats = 0
        self.status = "succeeded"

    async def list_held_release_prep(self):
        return {"items": self.held}

    async def list_release_prep(self):
        return {"items": self.prep_pool}

    async def claim_release_prep(self, prep_id):
        self.claims.append(prep_id)
        return {"ok": True, "id": prep_id, "instruction": "from the claim"}

    async def release_prep_heartbeat(self, prep_id):
        self.prep_beats += 1

    async def release_prep_status(self, prep_id):
        return self.status

    async def fail_release_prep(self, prep_id, error):
        self.failed.append((prep_id, error))


HELD = {
    "id": "prep-1",
    "release_id": "rel-1",
    "project_id": "proj-1",
    "version": "2026.08.16.3",
    "claimed_at": "2026-08-16T13:36:16+00:00",
    "instruction": "the project's own Release instruction",
    "agent_instructions": "house style",
    "notes_vocabulary": "sections: ...",
}


def _sim(config=None):
    return config or {"enabled_modules": ["sim"], "module_routes": {}}


def test_startup_re_adopts_a_held_prep_without_re_claiming():
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim)
    seen = {}

    async def fake_supervise(prep_id, item, claimed):
        seen["args"] = (prep_id, item, claimed)

    sup._supervise_release_prep = fake_supervise
    adopted = asyncio.run(sup.adopt_held_release_preps())

    assert adopted == 1
    assert client.claims == [], "it is already claimed — by this worker"
    prep_id, item, claimed = seen["args"]
    assert prep_id == "prep-1"
    # The briefing rides the held listing, so the job runs exactly as a fresh
    # claim would run it — with the project's Release instruction, not without.
    assert claimed["instruction"] == "the project's own Release instruction"
    assert item["version"] == "2026.08.16.3"


def _mcp_module(monkeypatch, outcome="succeeded"):
    """Release prep needs a module that can be given an MCP config (the whole
    job is claim/read/submit over the factory's MCP tools), and `sim` cannot
    be. Stand one in rather than spawning a real CLI."""
    from supervisor import workloop as wl

    class FakeModule:
        supports_mcp = True
        provider_type = ""
        settings = ()

        async def execute(self, ctx, prim):
            return ModuleResult(outcome=outcome, error=None if outcome == "succeeded" else "boom")

    monkeypatch.setattr(wl, "select_release_prep_module", lambda cfg: "fake")
    monkeypatch.setattr(wl.modules, "get", lambda name: FakeModule())
    return FakeModule


def test_re_adoption_drives_the_job_to_a_verified_submit(monkeypatch):
    """End to end through the real supervision path: the module runs and the
    outcome is checked against the server's own status, exactly as a freshly
    claimed prep is — a clean exit is not taken as done."""
    _mcp_module(monkeypatch)
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim)

    adopted = asyncio.run(sup.adopt_held_release_preps())

    assert adopted == 1
    assert client.failed == [], "a job that reached 'succeeded' is not failed"
    assert client.claims == []


def test_a_re_adopted_prep_that_never_submits_is_failed_not_orphaned_again(
    monkeypatch,
):
    """The failure mode this whole phase exists for: a job must never end by
    quietly staying `running`."""
    _mcp_module(monkeypatch)
    client = PrepClient(held=[dict(HELD)])
    client.status = "running"  # the CLI exited clean without submitting
    sup = Supervisor(client, config_provider=_sim)

    asyncio.run(sup.adopt_held_release_preps())

    assert client.failed and "without calling submit_release_notes" in client.failed[0][1]


def test_the_live_prep_registry_is_cleared_after_supervision(monkeypatch):
    """Otherwise one adopted prep would poison the guard for the rest of the
    process's life."""
    _mcp_module(monkeypatch)
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim)

    asyncio.run(sup.adopt_held_release_preps())

    assert sup._live_preps == set()


def test_nothing_held_adopts_nothing():
    client = PrepClient(held=[])
    sup = Supervisor(client, config_provider=_sim)
    assert asyncio.run(sup.adopt_held_release_preps()) == 0


def test_a_prep_already_being_supervised_is_never_adopted_twice():
    """AC3: the guard. Two supervisors on one job would heartbeat and submit
    over each other."""
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim)
    sup._live_preps.add("prep-1")

    assert asyncio.run(sup.adopt_held_release_preps()) == 0


def test_a_failing_held_query_never_blocks_startup():
    class Broken(PrepClient):
        async def list_held_release_prep(self):
            raise RuntimeError("api is still booting")

    sup = Supervisor(Broken(), config_provider=_sim)
    assert asyncio.run(sup.adopt_held_release_preps()) == 0


def test_re_adoption_with_no_module_fails_cleanly_rather_than_orphaning():
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=lambda: {"enabled_modules": []})

    asyncio.run(sup.adopt_held_release_preps())

    assert client.failed and "no enabled module" in client.failed[0][1]


def test_supervise_adopts_held_work_before_its_first_poll():
    """The whole point: the restart that orphaned the prep is the one that
    has to find it again, and it must happen before idling on an empty pool."""
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim, poll_seconds=0.01)
    order = []

    async def fake_supervise(prep_id, item, claimed):
        order.append(f"adopt:{prep_id}")

    async def watch_pool():
        order.append("poll")
        return {"runs": []}

    sup._supervise_release_prep = fake_supervise
    client.list_pool = watch_pool

    asyncio.run(sup.supervise(once=True))

    assert order[0] == "adopt:prep-1", f"adoption must precede the poll: {order}"


def test_adoption_happens_once_per_process_not_every_loop():
    client = PrepClient(held=[dict(HELD)])
    sup = Supervisor(client, config_provider=_sim, poll_seconds=0.01)
    adopted = []

    async def fake_supervise(prep_id, item, claimed):
        adopted.append(prep_id)

    sup._supervise_release_prep = fake_supervise

    asyncio.run(sup.supervise(once=True))
    asyncio.run(sup.supervise(once=True))

    assert adopted == ["prep-1"]
