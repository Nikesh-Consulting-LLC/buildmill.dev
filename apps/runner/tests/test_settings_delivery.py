"""US-32.8 + US-32.9: the resolved settings reach the command line.

Everything before these stories decided what a run should use. This is where it
becomes true. The failure mode being closed is a setting that looks configured
and does nothing — so these tests assert on the argv that would actually be
launched, per module, for every setting each one declares.
"""

from __future__ import annotations

import asyncio

import pytest

from supervisor import modules
from supervisor.modules.base import ModuleResult, RunContext, ShellResult


class FakePrim:
    def __init__(self, exit_code=0, stdout="ok"):
        self.calls: list[list[str]] = []
        self.exit_code = exit_code
        self.stdout = stdout

    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        self.calls.append(list(argv))
        return ShellResult(argv=list(argv), exit_code=self.exit_code, stdout=self.stdout)

    async def run_api(self, method, url, **kw):  # pragma: no cover
        raise AssertionError("no API calls in these tests")


def _module(name):
    m = modules.get(name)
    assert m is not None
    return m


FULL = {
    "model": "claude-opus-5",
    "fallback_model": "claude-sonnet-5",
    "effort": "high",
    "max_turns": 40,
    "standing_instructions": "find the root cause first",
}


# ------------------------------------------------------------------- argv


def test_every_claude_argv_setting_reaches_the_command_line():
    argv = _module("claude").settings_argv(FULL)
    pairs = dict(zip(argv[::2], argv[1::2]))
    assert pairs["--effort"] == "high"
    assert pairs["--max-turns"] == "40"
    assert pairs["--fallback-model"] == "claude-sonnet-5"
    # US-32.9: appended, so the CLI keeps its own tool guidance and safety text.
    assert pairs["--append-system-prompt"] == "find the root cause first"


def test_the_model_does_not_go_on_the_command_line():
    """It is declared as env delivery, because that is how the gateway learns
    which provider should answer (us-27.8)."""
    assert "--model" not in _module("claude").settings_argv(FULL)
    assert "claude-opus-5" not in _module("claude").settings_argv(FULL)


def test_the_mcp_config_path_is_not_a_tunable():
    """`mcp` is an argv knob, but its value is a file the harness writes."""
    argv = _module("claude").settings_argv({**FULL, "mcp": True})
    assert "--mcp-config" not in argv


def test_an_unset_setting_produces_no_flag():
    argv = _module("claude").settings_argv({"effort": "high"})
    assert argv == ["--effort", "high"]
    assert _module("claude").settings_argv({}) == []
    assert _module("claude").settings_argv(None) == []


def test_an_explicitly_empty_setting_produces_no_flag():
    assert _module("claude").settings_argv({"effort": "", "max_turns": None}) == []


def test_a_setting_the_module_no_longer_declares_reaches_nothing():
    """US-47.1: a preset written before this change still carries
    `permission_mode`. It must produce no flag — the whole point is that the
    mode is the module's own statement now, not something a layer can set."""
    argv = _module("claude").settings_argv({**FULL, "permission_mode": "plan"})
    assert "--permission-mode" not in argv
    assert "plan" not in argv


def test_a_module_that_declares_less_delivers_less():
    """Neither has an effort flag, a fallback model, or a turn ceiling, so
    nothing is invented for them — us-32.4 reports the gap rather than the
    delivery path guessing at one."""
    assert _module("opencode").settings_argv(FULL) == []
    # Grok's real CLI takes the model on argv (`-m`), unlike Claude's env
    # delivery — measured against the actual CLI, not the npm impostor this
    # module was first written against (US-10.5 follow-up).
    assert _module("grok").settings_argv(FULL) == ["-m", "claude-opus-5"]


def test_settings_land_before_the_machine_escape_hatch(monkeypatch):
    """RUNNER_*_ARGS still applies and applies LAST — an operator on the box can
    override what the app configured, deliberately."""
    monkeypatch.setenv("RUNNER_CLAUDE_ARGS", "--fallback-model something-else")
    argv = _module("claude").build_argv("do it", "code", ["--effort", "high"])
    assert argv.index("--effort") < argv.index("--fallback-model")
    assert argv[-2:] == ["--fallback-model", "something-else"]


# ------------------------------------------------------- us-47.1's permission


def test_the_permission_mode_does_not_depend_on_an_env_default(monkeypatch):
    """The regression this story exists for.

    It used to live in `RUNNER_CLAUDE_ARGS`'s default, so an operator setting
    that variable for ANY other reason — the README advertises it as "override
    the CLI binary/flags" — silently dropped the mode and put every run on that
    box into `default`, where every MCP call is refused and a code run cannot
    read its own work item. Pinned in all three states."""
    for value in (None, "", "--fallback-model something-else"):
        if value is None:
            monkeypatch.delenv("RUNNER_CLAUDE_ARGS", raising=False)
        else:
            monkeypatch.setenv("RUNNER_CLAUDE_ARGS", value)
        argv = _module("claude").build_argv("do it", "code")
        i = argv.index("--permission-mode")
        assert argv[i + 1] == "bypassPermissions", value


def test_the_resolved_settings_still_come_after_it(monkeypatch):
    """Order matters and is asserted rather than assumed: the mode is emitted
    before `extra` and before the escape hatch, so either can still override
    it on purpose."""
    monkeypatch.delenv("RUNNER_CLAUDE_ARGS", raising=False)
    argv = _module("claude").build_argv("do it", "code", ["--effort", "high"])
    assert argv.index("--permission-mode") < argv.index("--effort")


def test_the_escape_hatch_can_still_override_the_permission_mode(monkeypatch):
    """Deliberate: an operator on the box is allowed to break their own runs.
    What changed is that the harness's own requirement no longer DEPENDS on
    that variable being untouched."""
    monkeypatch.setenv("RUNNER_CLAUDE_ARGS", "--permission-mode acceptEdits")
    argv = _module("claude").build_argv("do it", "code")
    assert argv[-2:] == ["--permission-mode", "acceptEdits"]
    # Last-wins in the CLI, so both are present and the operator's is last.
    assert argv.count("--permission-mode") == 2


def test_opencode_keeps_its_positional_prompt_last(monkeypatch):
    """Its shape is why argv placement is the module's decision, not the base's."""
    monkeypatch.setenv("RUNNER_OPENCODE_ARGS", "")
    argv = _module("opencode").build_argv("do it", "code", ["--x", "1"])
    assert argv[-1] == "do it"
    assert "--x" in argv and argv.index("--x") < argv.index("do it")


# -------------------------------------------------- standing instructions


def test_a_module_with_no_flag_gets_the_instructions_in_the_prompt():
    prefix = _module("grok").prompt_prefix(FULL)
    assert "find the root cause first" in prefix
    assert "Standing instructions" in prefix
    assert "do not replace" in prefix.lower()


def test_a_module_with_a_flag_does_not_also_get_them_in_the_prompt():
    """Claude takes --append-system-prompt; duplicating the text into the prompt
    would spend the work item's attention budget twice."""
    assert _module("claude").prompt_prefix(FULL) == ""


def test_no_instructions_means_no_prefix():
    assert _module("grok").prompt_prefix({}) == ""
    assert _module("grok").prompt_prefix({"standing_instructions": "   "}) == ""
    assert _module("grok").prompt_prefix(None) == ""


# --------------------------------------------- end to end through execute


def _ctx(**kw):
    base = dict(
        run_id="r1",
        kind="prd",
        context={"title": "a feature", "story": "do the thing"},
        settings=dict(FULL),
    )
    base.update(kw)
    return RunContext(**base)


def test_a_prd_run_launches_with_the_resolved_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RUNNER_CLAUDE_ARGS", "")
    prim = FakePrim(stdout="## Problem\n\nx")
    result = asyncio.run(_module("claude").execute(_ctx(), prim))
    assert result.outcome == "succeeded"
    argv = prim.calls[0]
    assert "--effort" in argv and argv[argv.index("--effort") + 1] == "high"
    assert "--append-system-prompt" in argv


def test_a_grok_run_carries_the_instructions_in_its_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("RUNNER_GROK_ARGS", "")
    prim = FakePrim(stdout="## Problem\n\nx")
    asyncio.run(_module("grok").execute(_ctx(), prim))
    argv = prim.calls[0]
    prompt = argv[argv.index("-p") + 1]
    assert "find the root cause first" in prompt
    # And the work item is still in there, after the instructions.
    assert prompt.index("find the root cause") < prompt.index("a feature")


def test_a_run_with_no_resolved_settings_launches_exactly_as_before(
    monkeypatch, tmp_path
):
    """An older server sends no `run_settings`; that must be an empty tuning set,
    not a broken command line."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("RUNNER_CLAUDE_ARGS", raising=False)
    prim = FakePrim(stdout="## Problem\n\nx")
    asyncio.run(_module("claude").execute(_ctx(settings={}), prim))
    argv = prim.calls[0]
    assert argv[:2] == ["claude", "-p"]
    assert argv[-2:] == ["--permission-mode", "bypassPermissions"]
    assert "--effort" not in argv


# ------------------------------------------------- the bundle → context hop


def test_the_bundle_settings_reach_the_run_context():
    from supervisor.workloop import build_run_context

    ctx = build_run_context(
        {"run_id": "r", "kind": "code", "run_settings": {"effort": "high"}}
    )
    assert ctx.settings == {"effort": "high"}


def test_a_bundle_without_settings_gives_an_empty_dict_not_none():
    from supervisor.workloop import build_run_context

    assert build_run_context({"run_id": "r", "kind": "code"}).settings == {}


def test_the_env_provider_signature_takes_the_resolved_settings():
    """The runner is TOLD its model now; it does not look one up. Pinned because
    the signature is the seam between the resolver and the gateway env."""
    import inspect

    from supervisor.workloop import Supervisor

    src = inspect.getsource(Supervisor.run_claimed)
    assert "self.env_provider(run_id, kind, module, resolved)" in src


@pytest.mark.parametrize("name", ["claude", "grok", "opencode"])
def test_build_argv_accepts_the_extra_argv_on_every_cli_module(name, monkeypatch):
    monkeypatch.setenv(f"RUNNER_{name.upper()}_ARGS", "")
    argv = _module(name).build_argv("p", "code", ["--flag", "v"])
    assert "--flag" in argv
    # and it is still callable without one, for anything that has not been
    # updated to pass it
    assert _module(name).build_argv("p", "code")
