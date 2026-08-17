"""US-78.3 / US-78.4 / US-78.9: the interactive module, driven against a
scripted ACP agent.

The double answers real JSON-RPC rather than being a mock with assertions on
call counts — if the module sends a frame the protocol does not define, these
tests fail the same way a real agent would.
"""

import asyncio
import json
from pathlib import Path

import pytest

from supervisor import modules
from supervisor.acp.mcp import to_acp_servers
from supervisor.modules.base import RunContext
from supervisor.modules.interactive import InteractiveModule


class ScriptedAgent:
    """A SessionProcess-shaped ACP agent that actually answers."""

    def __init__(
        self,
        *,
        load_session=False,
        http_mcp=True,
        prompt_updates=None,
        stop_reason="Completed",
        fail_load=False,
    ):
        self.sent: list[dict] = []
        self._out: asyncio.Queue = asyncio.Queue()
        self._eof = False
        self.load_session = load_session
        self.http_mcp = http_mcp
        self.stop_reason = stop_reason
        self.fail_load = fail_load
        self.session_new_params: dict | None = None
        self.session_load_params: dict | None = None
        # `is None`, not `or`: an EMPTY list is a legitimate script — an agent
        # that ends a turn having said nothing — and `or` would silently swap
        # in the default and test the opposite case.
        self.prompt_updates = (
            [
                ("agent_thought_chunk", "considering the story"),
                ("agent_message_chunk", "Done."),
            ]
            if prompt_updates is None
            else prompt_updates
        )

    async def next_line(self):
        if self._eof:
            return None
        line = await self._out.get()
        if line is None:
            self._eof = True
            return None
        return line

    def _emit(self, obj):
        self._out.put_nowait(json.dumps(obj))

    async def send(self, text: str) -> None:
        msg = json.loads(text)
        self.sent.append(msg)
        method, rid = msg.get("method"), msg.get("id")
        params = msg.get("params") or {}
        if method == "initialize":
            self._emit(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {
                            "loadSession": self.load_session,
                            "mcpCapabilities": {"http": self.http_mcp, "sse": False},
                        },
                    },
                }
            )
        elif method == "session/new":
            self.session_new_params = params
            self._emit({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": "sess-new"}})
        elif method == "session/load":
            self.session_load_params = params
            if self.fail_load:
                self._emit(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32601, "message": "no such session"},
                    }
                )
            else:
                # The spec requires the whole conversation replayed as updates
                # BEFORE the load returns.
                for text_ in ("earlier turn one", "earlier turn two"):
                    self._emit(
                        {
                            "jsonrpc": "2.0",
                            "method": "session/update",
                            "params": {
                                "sessionId": params.get("sessionId"),
                                "update": {
                                    "sessionUpdate": "agent_message_chunk",
                                    "content": {"type": "text", "text": text_},
                                },
                            },
                        }
                    )
                self._emit({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/prompt":
            for variant, text_ in self.prompt_updates:
                self._emit(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": params.get("sessionId"),
                            "update": {
                                "sessionUpdate": variant,
                                "content": {"type": "text", "text": text_},
                            },
                        },
                    }
                )
            self._emit(
                {"jsonrpc": "2.0", "id": rid, "result": {"stopReason": self.stop_reason}}
            )

    def stderr_tail(self) -> str:
        return ""

    async def close(self, timeout: float = 5) -> int:
        self._out.put_nowait(None)
        return 0


class FakePrimitives:
    # The gateway env, as `LocalPrimitives` exposes it. A CLASS attribute, so a
    # subclass can override it declaratively (and so `__init__` does not shadow
    # one that does).
    env: dict = {}

    def __init__(self, agent):
        self.agent = agent
        self.sessions: list[list[str]] = []
        # us-115.1: the child's environment carries the MCP strategy and the
        # credential its config names, so the tests can see both.
        self.session_env: list[dict] = []

    async def run_session(self, argv, cwd=None, env=None):
        self.sessions.append(argv)
        self.session_env.append(dict(env or {}))
        return self.agent

    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        from supervisor.modules.base import ShellResult

        return ShellResult(argv=argv, exit_code=0, stdout="")


def _run(module, prim, **kw):
    return asyncio.run(
        module._run_cli(
            prim,
            kw.pop("prompt", "do the work"),
            kw.pop("run_kind", "plan"),
            kw.pop("cwd", "."),
            kw.pop("timeout", 30),
            **kw,
        )
    )


def test_the_module_is_registered_and_offers_mcp():
    assert "interactive" in modules.available()
    module = modules.get("interactive")
    assert module.supports_mcp is True
    assert module.needs_repo is True
    # US-78.3 AC2: it can take any of the four roles.
    for kind in ("plan", "code", "test", "prd", "release", "deploy"):
        assert kind in module.capabilities


def test_it_spawns_the_acp_subcommand_not_a_prompt_argv():
    """The prompt is a message, not an argument — that is the whole point."""
    module = InteractiveModule()
    argv = module.build_argv("some prompt", "code")
    assert argv[-2:] == ["agent", "stdio"]
    assert "some prompt" not in argv


def test_a_completed_turn_returns_the_agent_answer_as_stdout():
    """Everything downstream parses stdout — parse_stories, the PRD, the plan.
    If reassembly is wrong, every run in the factory breaks."""
    agent = ScriptedAgent(
        prompt_updates=[
            ("agent_message_chunk", "Hello"),
            ("agent_message_chunk", " world"),
        ]
    )
    module = InteractiveModule()
    res = _run(module, FakePrimitives(agent))
    assert res.exit_code == 0
    assert res.stdout == "Hello world"
    assert res.claude_session_id == "sess-new"


def test_the_handshake_is_recorded_so_nobody_has_to_infer_it_again():
    """`loadSession` and HTTP MCP were inferred from third-party integrations
    for the whole build. One line per run ends that, and a CLI upgrade that
    drops a capability shows up here rather than as a puzzling failure later."""
    agent = ScriptedAgent(load_session=True, http_mcp=True)
    module = InteractiveModule()
    seen = []
    module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
    _run(module, FakePrimitives(agent))
    line = next(l for _, l in seen if "handshake" in l)
    assert "protocol 1" in line
    assert "resume yes" in line
    assert "http" in line


def test_a_run_with_no_model_refuses_before_it_spends_anything():
    """US-78.5: it used to proceed and let the CLI fall back to whatever it was
    configured with, which is how a missing model surfaced as an opaque 404
    naming a model nobody chose."""

    class NoModelPrims(FakePrimitives):
        env = {"GROK_MODELS_BASE_URL": "https://g/v1"}  # base, but no GROK_MODEL

    agent = ScriptedAgent()
    res = _run(InteractiveModule(), NoModelPrims(agent))
    assert res.exit_code == 1
    assert "no model" in res.stdout
    # nothing was asked of the model
    assert not any(m.get("method") == "session/prompt" for m in agent.sent)


def test_thoughts_and_tools_reach_the_progress_sink():
    agent = ScriptedAgent()
    module = InteractiveModule()
    seen = []
    module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
    _run(module, FakePrimitives(agent))
    kinds = {k for k, _ in seen}
    assert "step" in kinds  # the thought
    assert any("Done." in line for _, line in seen)


def test_an_answer_counts_even_when_the_stop_reason_is_unfamiliar():
    """Measured live: the real CLI ended a turn on a reason outside the spec's
    PascalCase list, and a run that had written a complete PRD was failed with
    the PRD sitting in its own error message. The answer decides, not the
    label."""
    for reason in ("completed", "end_turn", "something_new", ""):
        agent = ScriptedAgent(
            stop_reason=reason,
            prompt_updates=[("agent_message_chunk", "# PRD\nreal content")],
        )
        res = _run(InteractiveModule(), FakePrimitives(agent))
        assert res.exit_code == 0, f"{reason!r} threw away a real answer"
        assert "real content" in res.stdout


def test_a_cancel_overrides_the_text_it_had_written():
    """The manager stopped it (US-78.8), so what it had said is not an answer."""
    agent = ScriptedAgent(
        stop_reason="Cancelled",
        prompt_updates=[("agent_message_chunk", "half a thought")],
    )
    res = _run(InteractiveModule(), FakePrimitives(agent))
    assert res.exit_code == 1


def test_a_truncated_answer_fails_with_its_partial_text_preserved():
    """US-83.4: max_tokens / max_turn_requests mean the agent was still
    talking when a ceiling cut it off — a half-written PRD must not pass as a
    whole one. The partial text rides stdout so the failure report leads with
    the agent's own words."""
    for reason in ("max_tokens", "MaxTokens", "max_turn_requests"):
        agent = ScriptedAgent(
            stop_reason=reason,
            prompt_updates=[("agent_message_chunk", "## Problem\nhalf a PRD")],
        )
        module = InteractiveModule()
        seen = []
        module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
        res = _run(module, FakePrimitives(agent))
        assert res.exit_code == 1, f"{reason!r} passed a truncated answer"
        assert "half a PRD" in res.stdout
        assert any("ceiling" in line for _, line in seen)


def test_a_turn_ceiling_exit_speaks_the_vocabulary_failed_reads():
    """`_failed` names a turn-budget exit from end_subtype `error_max_turns`
    (US-54.1); ACP's word for the same event maps onto it."""
    agent = ScriptedAgent(
        stop_reason="max_turn_requests",
        prompt_updates=[("agent_message_chunk", "partial")],
    )
    res = _run(InteractiveModule(), FakePrimitives(agent))
    assert res.end_subtype == "error_max_turns"


def test_no_answer_is_a_failure_that_names_the_reason():
    agent = ScriptedAgent(stop_reason="MaxTokens", prompt_updates=[])
    module = InteractiveModule()
    seen = []
    module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
    res = _run(module, FakePrimitives(agent))
    assert res.exit_code == 1
    assert any("MaxTokens" in line for _, line in seen)


def test_effort_and_max_turns_reach_the_command_line_before_the_subcommand():
    """US-83.4: the trace pair this fixes — 'escalated to Deep' immediately
    followed by 'cannot be told effort'. Both halves must hold: the knobs are
    declared (so the workloop delivers them) AND build_argv splices them in
    (it used to drop `extra` on the floor). Placement parse-verified on grok
    1.0.0: globals go BEFORE `agent stdio`."""
    module = InteractiveModule()
    flags = module.settings_argv({"effort": "high", "max_turns": 4})
    assert flags == ["--reasoning-effort", "high", "--max-turns", "4"]

    agent = ScriptedAgent()
    prim = FakePrimitives(agent)
    _run(module, prim, ctx_settings={"effort": "low", "max_turns": 2})
    argv = prim.sessions[0]
    agent_at = argv.index("agent")
    assert argv.index("--reasoning-effort") < agent_at
    assert argv.index("--max-turns") < agent_at
    assert argv[agent_at : agent_at + 2] == ["agent", "stdio"]


def _mcp_config(tmp_path: Path, **servers) -> str:
    config = tmp_path / "mcp.json"
    body = servers or {
        "factory": {
            "type": "http",
            "url": "https://api.example/mcp/",
            # Distinctive on purpose: a short value like "tok" is a substring of
            # `bearer_token_env_var` and would make the no-secret assertion pass
            # for the wrong reason.
            "headers": {"X-Worker-Token": "sfw_secret_value_115"},
        }
    }
    config.write_text(json.dumps({"mcpServers": body}), encoding="utf-8")
    return str(config)


@pytest.fixture
def factory_answers(monkeypatch):
    """The preflight succeeds; the calls it made are recorded."""
    from supervisor import mcpconfig

    calls: list[tuple[str, dict]] = []

    async def probe(url, headers, timeout=30):
        calls.append((url, headers))
        return ["get_work_context", "submit_changeset"]

    monkeypatch.setattr(mcpconfig, "probe", probe)
    return calls


def test_the_servers_go_into_the_clis_own_config_not_session_new(
    tmp_path: Path, monkeypatch, factory_answers
):
    """us-115.1 AC1/AC3/AC4: the CLI reads its servers from its own
    `config.toml`, `session/new` carries none, and the child is spawned with the
    strategy that makes the first turn wait for the handshake.

    This replaces US-78.4's "a parameter of the session, not a config file and a
    hope" — the hand-off worked, but nothing told the CLI the session was
    headless, so it resolved Progressive and started reasoning while the factory
    was still connecting. The config file is not a hope when the run has already
    spoken to the server itself (AC5).
    """
    home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(home))
    agent = ScriptedAgent(http_mcp=True)
    prim = FakePrimitives(agent)
    module = InteractiveModule()

    res = _run(module, prim, mcp_config=_mcp_config(tmp_path))
    assert res.exit_code == 0

    # Nothing over ACP: one description of the tool surface, not two.
    assert agent.session_new_params["mcpServers"] == []

    config = (home / "config.toml").read_text(encoding="utf-8")
    assert '[mcp_servers."factory"]' in config
    assert 'url = "https://api.example/mcp/"' in config
    assert 'bearer_token_env_var = "FACTORY_MCP_KEY"' in config
    # US-83.1's hardening survives the new section.
    assert "auto_update = false" in config and "[compat.claude]" in config
    # AC2: the credential is not in the file.
    assert "sfw_secret_value_115" not in config

    env = prim.session_env[0]
    assert env["MCP_INIT_STRATEGY"] == "blocking"
    assert env["FACTORY_MCP_KEY"] == "sfw_secret_value_115"

    # AC5: the server answered before the CLI existed.
    assert factory_answers[0][0] == "https://api.example/mcp/"


def test_the_model_block_and_the_servers_share_one_file(
    tmp_path: Path, monkeypatch, factory_answers
):
    """The model block used to REPLACE the body rather than join it, which
    would have dropped the servers the moment a model resolved."""
    home = tmp_path / "grok-home"
    monkeypatch.setenv("GROK_HOME", str(home))
    prim = FakePrimitives(ScriptedAgent(http_mcp=True))
    prim.env = {
        "GROK_MODELS_BASE_URL": "https://gw.example/v1",
        "GROK_MODEL": "grok-4.5",
    }
    res = _run(InteractiveModule(), prim, mcp_config=_mcp_config(tmp_path))
    assert res.exit_code == 0

    config = (home / "config.toml").read_text(encoding="utf-8")
    assert '[model."grok-4.5"]' in config
    assert '[mcp_servers."factory"]' in config
    assert "auto_update = false" in config


def test_a_factory_that_does_not_answer_refuses_before_the_cli_is_spawned(
    tmp_path: Path, monkeypatch
):
    """us-115.1 AC5, replacing US-78.4 AC5's check on the ACP list: an agent
    that starts without its tools burns a model budget to discover it cannot do
    the job — and, on six measured runs, invents a way around instead."""
    from supervisor import mcpconfig

    async def probe(url, headers, timeout=30):
        raise RuntimeError("HTTP 401: invalid or revoked worker token")

    monkeypatch.setattr(mcpconfig, "probe", probe)
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))

    agent = ScriptedAgent(http_mcp=True)
    prim = FakePrimitives(agent)
    res = _run(InteractiveModule(), prim, mcp_config=_mcp_config(tmp_path))

    assert res.exit_code == 1
    assert "401" in res.stdout
    assert "Nothing was spent" in res.stdout
    assert prim.sessions == []  # the CLI was never started
    assert not any(m.get("method") == "session/prompt" for m in agent.sent)


def test_the_config_json_is_written_beside_the_workspace_never_inside_it():
    """us-115.1 AC4: the file the six bypassing runs read out of their own
    checkout does not live there any more."""
    module = InteractiveModule()
    workdir = Path("/w/project-628de3f7")
    target = module.mcp_config_dir(workdir)
    assert target != workdir
    assert workdir not in target.parents and target.parent == workdir.parent

    # Every other module keeps writing it into the checkout.
    from supervisor.modules import get as get_module

    assert get_module("claude").mcp_config_dir(workdir) == workdir


def test_resume_loads_the_prior_session_and_does_not_retrace_the_replay():
    """US-78.9 AC3: `session/load` replays the whole conversation first. Those
    replayed updates must not be written to the trace a second time."""
    agent = ScriptedAgent(load_session=True)
    module = InteractiveModule()
    seen = []
    module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
    res = _run(module, FakePrimitives(agent), resume_session_id="sess-old")
    assert agent.session_load_params["sessionId"] == "sess-old"
    assert agent.session_new_params is None
    assert res.claude_session_id == "sess-old"
    lines = " ".join(line for _, line in seen)
    assert "earlier turn one" not in lines
    assert "resumed the earlier session" in lines


def test_an_agent_that_cannot_load_a_session_starts_a_fresh_one_and_says_so():
    agent = ScriptedAgent(load_session=False)
    module = InteractiveModule()
    seen = []
    module.set_progress_sink(lambda kind, line: seen.append((kind, line)))
    res = _run(module, FakePrimitives(agent), resume_session_id="sess-old")
    assert agent.session_load_params is None
    assert res.claude_session_id == "sess-new"
    assert any("does not support resuming" in line for _, line in seen)


def test_a_failed_load_falls_back_to_a_new_session_rather_than_failing_the_run():
    agent = ScriptedAgent(load_session=True, fail_load=True)
    module = InteractiveModule()
    res = _run(module, FakePrimitives(agent), resume_session_id="gone")
    assert res.exit_code == 0
    assert res.claude_session_id == "sess-new"


def test_standing_instructions_reach_the_prompt_since_acp_has_no_flag_for_them():
    agent = ScriptedAgent()
    module = InteractiveModule()
    _run(
        module,
        FakePrimitives(agent),
        ctx_settings={"standing_instructions": "be terse"},
    )
    prompt = next(m for m in agent.sent if m.get("method") == "session/prompt")
    text = prompt["params"]["prompt"][0]["text"]
    assert "be terse" in text and "do the work" in text


# -- the MCP translation, on its own ---------------------------------------


def test_stdio_servers_translate_without_a_type_tag():
    servers, notes = to_acp_servers(
        {"mcpServers": {"browser": {"type": "stdio", "command": "npx", "args": ["-y", "b"]}}}
    )
    assert servers == [
        {"name": "browser", "command": "npx", "args": ["-y", "b"], "env": []}
    ]
    assert notes == []


def test_the_gateway_config_names_an_env_var_and_never_holds_the_key(tmp_path: Path):
    """US-78.5: an inline api_key would be a credential written to disk. The
    whole point of the per-run mint is that it never lands anywhere."""
    from supervisor.modules.interactive import write_model_config

    written = write_model_config(
        str(tmp_path),
        {
            "GROK_MODELS_BASE_URL": "https://api.example/llm-gateway/v1",
            "GROK_MODEL": "grok-4.5",
            "BUILDMILL_GATEWAY_KEY": "sfg_supersecret",
        },
    )
    body = Path(written).read_text(encoding="utf-8")
    assert 'env_key = "BUILDMILL_GATEWAY_KEY"' in body
    assert "sfg_supersecret" not in body
    assert "https://api.example/llm-gateway/v1" in body
    assert 'api_backend = "chat_completions"' in body


def test_the_model_table_is_keyed_by_the_real_model_id(tmp_path: Path):
    """Measured against CLI 1.0.0: it sends the ENTRY NAME as the model, not the
    inner `model` field. Keyed as `[model.buildmill]`, the provider answered
    "The model `buildmill` does not exist" while the CLI reported
    `Available: buildmill`. The key must be the model id, and quoted — an
    unquoted `[model.grok-4.5]` is a nested table, not a model called
    grok-4.5."""
    from supervisor.modules.interactive import write_model_config

    written = write_model_config(
        str(tmp_path),
        {"GROK_MODELS_BASE_URL": "https://g/v1", "GROK_MODEL": "grok-4.5"},
    )
    body = Path(written).read_text(encoding="utf-8")
    assert '[model."grok-4.5"]' in body
    assert 'model = "grok-4.5"' in body
    assert "buildmill" not in body.split("env_key")[0]


def test_no_resolved_model_writes_no_model_block(tmp_path: Path):
    """A block with no model is what produced the 404 — refuse it. US-83.1:
    the hardening sections still land; only the model block is withheld."""
    from supervisor.modules.interactive import write_model_config

    assert (
        write_model_config(str(tmp_path), {"GROK_MODELS_BASE_URL": "https://g/v1"})
        is None
    )
    body = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "[model." not in body


def test_a_model_less_call_overwrites_an_earlier_runs_model_block(tmp_path: Path):
    """GROK_HOME persists between runs, so a config an earlier run wrote stays
    in force and silently decides this one. Measured live: a run with no model
    kept using the previous run's `[model.buildmill]` block and 404'd on a name
    this codebase no longer produces. US-83.1 retires the delete with an
    overwrite — the stale block dies either way, and the hardening remains."""
    from supervisor.modules.interactive import write_model_config

    stale = tmp_path / "config.toml"
    stale.write_text('[model.buildmill]\nmodel = "grok-4.5"\n', encoding="utf-8")

    assert write_model_config(str(tmp_path), {}) is None
    body = stale.read_text(encoding="utf-8")
    assert "buildmill" not in body, "a stale model block must not outlive its run"
    assert "auto_update = false" in body


def test_a_fresh_config_replaces_an_earlier_one(tmp_path: Path):
    from supervisor.modules.interactive import write_model_config

    (tmp_path / "config.toml").write_text("[model.buildmill]\n", encoding="utf-8")
    written = write_model_config(
        str(tmp_path),
        {"GROK_MODELS_BASE_URL": "https://g/v1", "GROK_MODEL": "grok-4.5"},
    )
    body = Path(written).read_text(encoding="utf-8")
    assert "buildmill" not in body
    assert '[model."grok-4.5"]' in body


def test_no_home_writes_nowhere(tmp_path: Path):
    from supervisor.modules.interactive import write_model_config

    assert write_model_config("", {"GROK_MODELS_BASE_URL": "x"}) is None
    assert list(tmp_path.iterdir()) == []


def test_every_config_closes_the_cli_doors(tmp_path: Path):
    """US-83.1: auto-update defaults ON and compat scanning defaults ON — a
    fleet binary that moves mid-run, and workspace repos injecting
    skills/rules/MCP/hooks through Claude/Cursor compat. Both off, in every
    config this runner writes, model or no model."""
    from supervisor.modules.interactive import write_model_config

    write_model_config(
        str(tmp_path),
        {"GROK_MODELS_BASE_URL": "https://g/v1", "GROK_MODEL": "grok-4.5"},
    )
    body = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "[cli]\nauto_update = false" in body
    for section in ("[compat.claude]", "[compat.cursor]"):
        assert section in body
    assert body.count("mcps = false") == 2
    assert body.count("hooks = false") == 2

    write_model_config(str(tmp_path), {})
    stripped = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert "auto_update = false" in stripped
    assert stripped.count("hooks = false") == 2


def test_context_window_rides_the_env_or_stays_out(tmp_path: Path):
    """US-83.1: auto-compact needs the window and a BYOK entry knows only what
    the config says — but a wrong number is worse than none, so only a numeric
    env value lands."""
    from supervisor.modules.interactive import write_model_config

    env = {"GROK_MODELS_BASE_URL": "https://g/v1", "GROK_MODEL": "grok-4.5"}
    write_model_config(str(tmp_path), {**env, "GROK_MODEL_CONTEXT_WINDOW": "500000"})
    assert "context_window = 500000" in (tmp_path / "config.toml").read_text(
        encoding="utf-8"
    )

    write_model_config(str(tmp_path), env)
    assert "context_window" not in (tmp_path / "config.toml").read_text(
        encoding="utf-8"
    )

    write_model_config(str(tmp_path), {**env, "GROK_MODEL_CONTEXT_WINDOW": "lots"})
    assert "context_window" not in (tmp_path / "config.toml").read_text(
        encoding="utf-8"
    )


def test_the_interactive_env_shape_is_keyed_on_the_module_not_the_provider():
    """Two different programs speak `xai` here: the Grok Build module runs
    superagent-ai/grok-cli, this one runs our fork of xai-org/grok-build, and
    they read different variables."""
    from supervisor.workloop import model_env

    grok = model_env("xai", "http://g", "k", "m")
    interactive = model_env("xai", "http://g", "k", "m", module="interactive")
    assert grok["GROK_API_KEY"] == "k"
    assert "GROK_API_KEY" not in interactive
    assert interactive["GROK_MODELS_BASE_URL"] == "http://g/v1"
    # nothing may slip past the gateway to the real api.x.ai
    assert interactive["GROK_XAI_API_BASE_URL"] == "http://g/v1"
    assert interactive["BUILDMILL_GATEWAY_KEY"] == "k"


def test_a_measured_context_window_rides_the_interactive_env():
    """US-83.1: grok-4.5 declares totalContextTokens=500000 in its own
    handshake catalog (CLI 1.0.0). Unmeasured models carry no window."""
    from supervisor.workloop import model_env

    known = model_env("xai", "http://g", "k", "grok-4.5", module="interactive")
    assert known["GROK_MODEL_CONTEXT_WINDOW"] == "500000"
    unknown = model_env("xai", "http://g", "k", "mystery-model", module="interactive")
    assert "GROK_MODEL_CONTEXT_WINDOW" not in unknown


def test_an_http_server_is_never_silently_downgraded_to_stdio():
    """There is no stdio equivalent of 'this URL with this header'. A server
    quietly missing is how an agent cannot read its own work item while looking
    like it started fine."""
    servers, notes = to_acp_servers(
        {"mcpServers": {"factory": {"type": "http", "url": "https://x/mcp"}}},
        {"stdio": True, "http": False, "sse": False},
    )
    assert servers == []
    assert notes and "HTTP MCP" in notes[0]
