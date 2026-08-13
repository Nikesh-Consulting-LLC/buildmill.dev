"""OpenCode agent module (US-10.5).

Headless: `opencode run --format json "<prompt>"` — note the `run` subcommand and
the POSITIONAL prompt (no `-p`), which is exactly why the CLI base can't assume
Claude's arg shape.

US-62.x: model access does NOT come from the injected gateway env, despite what
this file used to claim. Measured against the real CLI: `opencode run` takes no
model via any environment variable — only `-m <provider>/<model>` on argv — and
its built-in `openai`/`groq` provider catalogs only recognize those providers'
own real model ids, so an org's configured model (reached through the factory
gateway, e.g. `llama-3.3-70b-versatile`) comes back `ProviderModelNotFoundError:
Model not found`. Every run silently used to fall through to whatever the CLI's
own default resolution picked (`model=undefined` in its own debug log), which on
a bare agent server with nothing pre-configured hangs indefinitely rather than
failing loudly.

The fix: synthesize a one-off `@ai-sdk/openai-compatible` custom provider
pointed at the gateway (OpenCode's documented mechanism for an arbitrary
OpenAI-wire endpoint — see https://opencode.ai/docs/providers/), written to a
temp file named by `OPENCODE_CONFIG`, and pass `-m <that provider>/<model>`.
This works whether the org's provider is really OpenAI or Groq — it never
depends on OpenCode recognizing the real provider by name.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path

from . import register
from .base import Knob, ModuleResult, Primitives, RunContext, ShellResult
from .cli_base import CLIModule, split_cmd

logger = logging.getLogger(__name__)

# Carries this run's `-m` value from execute() (which has the RunContext) down
# to build_argv() (which, per the shared CLIModule contract, does not).
# contextvars rather than an attribute on `self`: the module is a process-wide
# singleton (US-10.4's registry), and two runs can be in flight on it at once
# — an instance attribute would let one run's model flag leak into another's
# argv across an `await`. A contextvar is asyncio-task-local, so it can't.
_model_flag: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "opencode_model_flag", default=None
)


class _OpenCodeCollector:
    """Reassembles the final answer from `--format json`'s NDJSON stream.

    Every module parses `res.stdout` as if it were `--format default`'s plain
    text — `parse_stories`, the PRD, the plan — so the raw event stream must
    never reach any of them; that produced a PRD field that was the whole
    NDJSON transcript verbatim, unreadable and unparseable, the first time
    this ran for real (US-62.x).

    Shapes captured directly off a real run (opencode-ai 1.18.15), not taken
    from documentation:

        {"type":"step_start","part":{"type":"step-start",...}}
        {"type":"text","part":{"type":"text","text":"...",...}}
        {"type":"tool_use","part":{"type":"tool","tool":"write",...}}
        {"type":"step_finish","part":{"type":"step-finish","reason":"stop",
                                       "tokens":{...},"cost":...}}
        {"type":"error","error":{"name":"...","data":{"message":"..."}}}

    Unlike Claude Code's stream-json, each `text` event already carries that
    part's FULL text (not a delta) — multiple such events (one per turn) are
    joined with a blank line. Never raises: a malformed line is skipped,
    because losing one event is survivable and losing the run is not.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.error_message: str | None = None
        self.saw_json = False

    def feed(self, line: str) -> dict | None:
        text = line.strip()
        if not text.startswith("{"):
            return None
        try:
            event = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None
        self.saw_json = True
        etype = event.get("type")
        if etype == "text":
            chunk = (event.get("part") or {}).get("text")
            if chunk:
                self.texts.append(str(chunk))
        elif etype == "error":
            err = event.get("error") or {}
            data = err.get("data") or {}
            self.error_message = str(
                data.get("message") or err.get("name") or "unknown error"
            )
        return event

    def final_text(self) -> str | None:
        if self.texts:
            return "\n\n".join(self.texts)
        return None


class _OpenCodeStream:
    """Watches one `opencode` invocation; hands back a ShellResult whose
    stdout is the final answer, the same contract `_ClaudeStream` keeps for
    Claude Code. Progress narration is best-effort and must never affect the
    run it is watching."""

    def __init__(self, sink=None) -> None:
        self.collector = _OpenCodeCollector()
        self.sink = sink

    def on_line(self, line: str) -> None:
        try:
            event = self.collector.feed(line)
            if event is None or self.sink is None:
                return
            etype = event.get("type")
            if etype == "tool_use":
                tool = (event.get("part") or {}).get("tool") or "tool"
                self.sink("tool", str(tool))
            elif etype == "error":
                self.sink("error", self.collector.error_message or "error")
        except Exception:  # noqa: BLE001 — watching must not break running
            logger.debug("progress line dropped", exc_info=True)

    def finalize(self, res: ShellResult) -> ShellResult:
        try:
            final = self.collector.final_text()
            if final is not None:
                return ShellResult(
                    argv=res.argv, exit_code=res.exit_code, stdout=final,
                    allowed=res.allowed,
                )
            if self.collector.saw_json and self.sink is not None:
                # Read events but found no text in them (e.g. every step was
                # a tool call) — passing the raw NDJSON through would fail
                # whatever parses it next; say why here instead.
                self.sink(
                    "error",
                    "the agent's output could not be reassembled into an "
                    "answer — passing its raw stream through",
                )
        except Exception:  # noqa: BLE001
            logger.warning("could not reassemble agent output", exc_info=True)
        return res


class OpenCodeModule(CLIModule):
    name = "opencode"
    provider_type = "openai"

    # US-32.4: the same two as Grok. Its positional-prompt shape is why the
    # prompt delivery is a first-class option rather than an afterthought.
    settings = (
        Knob(
            "model",
            kind="text",
            delivery="env",
            flag="OPENAI_MODEL",
            help="The model this run reasons with. Delivered as `-m "
            "<gateway provider>/<model>` on the command line — OpenCode "
            "takes no model via environment variable.",
        ),
        Knob(
            "standing_instructions",
            kind="text",
            delivery="prompt",
            help="Prepended to the positional prompt — OpenCode has no "
            "system-prompt flag.",
        ),
    )

    async def execute(self, ctx: RunContext, prim: Primitives) -> ModuleResult:
        config_path, flag_value = self._configure_gateway(ctx.model_env)
        token = _model_flag.set(flag_value)
        try:
            return await super().execute(ctx, prim)
        finally:
            _model_flag.reset(token)
            if config_path:
                try:
                    os.unlink(config_path)
                except OSError:
                    pass

    def _configure_gateway(
        self, model_env: dict[str, str]
    ) -> tuple[str | None, str | None]:
        """Write the synthesized provider config and point `OPENCODE_CONFIG`
        at it.

        Mutates `model_env` IN PLACE rather than returning a new dict — it is
        the exact same object the supervisor already merges into the CLI's
        subprocess environment (`RunContext.model_env` and
        `LocalPrimitives._env` are built from one shared dict per run in
        `workloop.py`), and OPENCODE_CONFIG is an env var, not a flag
        OpenCode accepts on its own command line.
        """
        base = (model_env or {}).get("OPENAI_BASE_URL")
        key = (model_env or {}).get("OPENAI_API_KEY")
        model = (model_env or {}).get("OPENAI_MODEL")
        if not (base and key and model):
            return None, None
        config = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {
                "buildmill-gateway": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Build Mill gateway",
                    # US-62.x: `@ai-sdk/openai-compatible` appends
                    # `/chat/completions` straight onto baseURL with no `/v1`
                    # of its own — measured live: without this, the gateway
                    # forwards to `.../openai/chat/completions` and Groq
                    # answers 404 "Unknown request URL", one segment short of
                    # its real `.../openai/v1/chat/completions`.
                    "options": {"baseURL": f"{base}/v1", "apiKey": key},
                    "models": {model: {"name": model}},
                }
            },
        }
        path = (
            Path(tempfile.gettempdir())
            / f"opencode-gateway-{uuid.uuid4().hex}.json"
        )
        path.write_text(json.dumps(config), encoding="utf-8")
        model_env["OPENCODE_CONFIG"] = str(path)
        return str(path), f"buildmill-gateway/{model}"

    def stream_watcher(self, sink):
        return _OpenCodeStream(sink)

    def build_argv(
        self, prompt: str, run_kind: str, extra: list[str] | None = None
    ) -> list[str]:
        cmd = split_cmd(os.environ.get("RUNNER_OPENCODE_CMD", "opencode"))
        args = split_cmd(os.environ.get("RUNNER_OPENCODE_ARGS", ""))
        model_flag = ["-m", _model_flag.get()] if _model_flag.get() else []
        # positional prompt at the END, JSON output — NOT the `-p` shape, which
        # is why placement is the module's decision and not the base's.
        return [
            *cmd, "run", "--format", "json",
            *model_flag, *(extra or []), *args, prompt,
        ]


register(OpenCodeModule())
