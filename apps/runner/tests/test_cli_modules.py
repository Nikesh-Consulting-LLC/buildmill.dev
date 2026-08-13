"""US-10.5: built-in CLI modules — argv shapes, story parsing, code-run flow."""

import asyncio
import json
from pathlib import Path

from supervisor import modules
from supervisor.modules.base import RunContext, ShellResult
from supervisor.modules.cli_base import parse_stories


def test_all_three_modules_registered():
    avail = modules.available()
    for name in ("claude", "grok", "opencode"):
        assert name in avail


def test_buildmill_agent_behaves_exactly_like_claude():
    """US-60.1: Buildmill Agent is Claude Code under a platform-billed name —
    same argv, same declared settings, same stream handling. Only the
    gateway's credential resolution differs, which is server-side and
    invisible to the runner."""
    assert "buildmill" in modules.available()
    claude = modules.get("claude")
    buildmill = modules.get("buildmill")
    assert buildmill is not claude
    assert buildmill.name == "buildmill"
    assert buildmill.build_argv("DO IT", "code") == claude.build_argv("DO IT", "code")
    assert buildmill.settings == claude.settings
    assert buildmill.provider_type == claude.provider_type


def test_claude_and_grok_use_dash_p_shape(monkeypatch):
    claude = modules.get("claude").build_argv("DO IT", "code")
    assert claude[0] == "claude"
    assert "-p" in claude and claude[claude.index("-p") + 1] == "DO IT"
    assert claude[-2:] == ["--permission-mode", "bypassPermissions"]

    # Measured live against the real CLI (superagent-ai/grok-cli, not the
    # unrelated @vibe-kit/grok-cli npm package it was first — wrongly —
    # measured against). Its interface changed completely between 1.0.0 (the
    # first version measured) and 1.1.7 (what its release channel actually
    # resolves to): `--format json` replaces `--output-format plain`, and
    # `--always-approve` no longer exists at all on 1.1.7.
    monkeypatch.delenv("RUNNER_GROK_ARGS", raising=False)
    grok = modules.get("grok").build_argv("DO IT", "code")
    assert grok[0] == "grok"
    assert grok == ["grok", "-p", "DO IT", "--format", "json"]


def test_opencode_uses_run_subcommand_positional_prompt():
    argv = modules.get("opencode").build_argv("DO IT", "code")
    assert argv[0] == "opencode"
    assert "run" in argv
    assert "-p" not in argv  # positional, not a flag
    assert argv[-1] == "DO IT"
    assert "--format" in argv and argv[argv.index("--format") + 1] == "json"


def test_opencode_takes_no_model_via_env_only_via_dash_m(tmp_path, monkeypatch):
    """US-62.x: measured against the real CLI, `opencode run` reads no model
    from any environment variable -- only `-m <provider>/<model>` on argv.
    A run through execute() must synthesize a gateway-pointed provider and
    pass that flag; build_argv alone (no execute()) must add nothing, since
    it has no run to carry a model for."""
    argv = modules.get("opencode").build_argv("DO IT", "code")
    assert "-m" not in argv

    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    model_env = {
        "OPENAI_BASE_URL": "https://api.buildmill.dev/api/v1/llm-gateway",
        "OPENAI_API_KEY": "scoped-key-abc",
        "OPENAI_MODEL": "llama-3.3-70b-versatile",
    }
    ctx = RunContext(
        run_id="run-1",
        kind="code",
        context={"title": "Add health route", "issue_id": "abc12345"},
        branch_name="factory/issue-abc12345",
        git_remote_url="https://factory.example.com/git/org/proj.git",
        default_branch="main",
        model_env=model_env,
    )
    # Read the config file's content from inside the fake CLI call, before
    # execute()'s own `finally` deletes it — the same instant the real CLI
    # would read it.
    captured: dict = {}
    orig_run_shell = prim.run_shell

    async def spying_run_shell(argv, cwd=None, timeout=None, on_line=None):
        if argv and argv[0] == "opencode":
            captured["config"] = Path(model_env["OPENCODE_CONFIG"]).read_text()
        return await orig_run_shell(argv, cwd=cwd, timeout=timeout, on_line=on_line)

    prim.run_shell = spying_run_shell

    result = asyncio.run(modules.get("opencode").execute(ctx, prim))
    assert result.outcome == "succeeded"

    cli_call = next(c for c in prim.calls if c and c[0] == "opencode")
    assert "-m" in cli_call
    flag_value = cli_call[cli_call.index("-m") + 1]
    assert flag_value.endswith("/llama-3.3-70b-versatile")

    # The config named the gateway as an OpenAI-compatible provider, so the
    # model id above actually resolves.
    written = json.loads(captured["config"])
    provider = written["provider"][flag_value.split("/", 1)[0]]
    # `@ai-sdk/openai-compatible` appends `/chat/completions` straight onto
    # baseURL with no `/v1` of its own -- without this, Groq answers 404
    # "Unknown request URL", one segment short of its real endpoint.
    assert provider["options"]["baseURL"] == model_env["OPENAI_BASE_URL"] + "/v1"
    assert provider["options"]["apiKey"] == "scoped-key-abc"
    assert "llama-3.3-70b-versatile" in provider["models"]

    # Cleaned up after the run — nothing left behind on the machine.
    assert not Path(model_env["OPENCODE_CONFIG"]).exists()


def test_opencode_omits_the_dash_m_flag_without_gateway_env(tmp_path, monkeypatch):
    """The `sim`-like case: a module with no resolvable gateway env (e.g. an
    empty model_env) must not crash and must not invent a flag."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    ctx = RunContext(
        run_id="run-1",
        kind="code",
        context={"title": "Add health route", "issue_id": "abc12345"},
        branch_name="factory/issue-abc12345",
        git_remote_url="https://factory.example.com/git/org/proj.git",
        default_branch="main",
        model_env={},
    )
    result = asyncio.run(modules.get("opencode").execute(ctx, prim))
    assert result.outcome == "succeeded"
    cli_call = next(c for c in prim.calls if c and c[0] == "opencode")
    assert "-m" not in cli_call


def test_parse_stories_tolerates_fences_and_prose():
    text = 'here you go:\n```json\n[{"title":"A","acceptance_criteria":["x"]}]\n```\ndone'
    stories = parse_stories(text)
    assert stories == [{"title": "A", "body": "", "acceptance_criteria": ["x"]}]


class FakePrim:
    """Records shell calls; fakes git + CLI so a code run needs no real repo."""

    def __init__(self):
        self.calls = []

    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        self.calls.append(list(argv))
        if argv and argv[0] == "git":
            sub = argv[1]
            if sub == "status":
                return ShellResult(argv, 0, " M app/file.py\n")  # dirty -> commit
            if sub == "ls-remote":
                return ShellResult(argv, 0, "")  # branch not upstream
            return ShellResult(argv, 0, "")
        return ShellResult(argv, 0, "[cli] wrote changes")  # the agent CLI

    async def run_api(self, *a, **k):
        raise AssertionError("run_api not expected")


def test_code_run_clones_checks_out_commits_pushes(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    ctx = RunContext(
        run_id="run-1",
        kind="code",
        context={"title": "Add health route", "issue_id": "abc12345"},
        branch_name="factory/issue-abc12345",
        git_remote_url="https://factory.example.com/git/org/proj.git",
        default_branch="main",
    )
    result = asyncio.run(modules.get("claude").execute(ctx, prim))
    assert result.outcome == "succeeded"
    assert result.branch_ref == "factory/issue-abc12345"

    verbs = [c[1] for c in prim.calls if c and c[0] == "git"]
    assert "clone" in verbs
    assert "push" in verbs
    # the agent CLI ran between checkout and push
    assert any(c[0] == "claude" for c in prim.calls)


def test_a_code_run_emits_four_stage_lines_in_order(tmp_path, monkeypatch):
    """US-62.10: a run's time, broken into named stages -- timed by the
    supervisor's own control flow, not dependent on the CLI agent
    narrating anything. Encoded as `stage:<name> <ms>ms` on the existing
    progress sink (no new run_trace column)."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    ctx = RunContext(
        run_id="run-1",
        kind="code",
        context={"title": "Add health route", "issue_id": "abc12345"},
        branch_name="factory/issue-abc12345",
        git_remote_url="https://factory.example.com/git/org/proj.git",
        default_branch="main",
    )
    module = modules.get("claude")
    lines = []
    module.set_progress_sink(lambda kind, line: lines.append((kind, line)))
    result = asyncio.run(module.execute(ctx, prim))
    assert result.outcome == "succeeded"

    stage_lines = [line for kind, line in lines if kind == "step"]
    stage_names = [line.split()[0].removeprefix("stage:") for line in stage_lines]
    assert stage_names == ["checkout", "invoke_cli", "collect_output", "commit_and_push"]
    for line in stage_lines:
        assert line.endswith("ms")


def test_a_stage_line_is_never_sent_without_a_progress_sink(tmp_path, monkeypatch):
    """A module with no sink attached (no live socket) must still complete
    a run -- narration is best-effort, never load-bearing."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    prim = FakePrim()
    ctx = RunContext(
        run_id="run-1",
        kind="code",
        context={"title": "Add health route", "issue_id": "abc12345"},
        branch_name="factory/issue-abc12345",
        git_remote_url="https://factory.example.com/git/org/proj.git",
        default_branch="main",
    )
    result = asyncio.run(modules.get("claude").execute(ctx, prim))
    assert result.outcome == "succeeded"
