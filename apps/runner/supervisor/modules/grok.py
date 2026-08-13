"""Grok Build agent module (US-10.5).

Headless: `grok -p "<prompt>" --format json`. Auth is `GROK_API_KEY`.

Measured directly against the real CLI (superagent-ai/grok-cli) TWICE, because
its interface changed completely between the version first measured (1.0.0,
which had `--output-format`/`--always-approve`/a `mcp add` subcommand) and the
version its own release channel actually resolves to today (1.1.7, which has
none of those — `--format text|json`, no approval flag at all, and MCP
management no longer in the top-level command list). This module targets
1.1.7's interface. If `grok --version` on a given host reads as some other
generation again, the CLI moved out from under this module a second time —
`grok --help` on that host is the source of truth, not this comment.

  * Auth: `GROK_API_KEY` (its own error text names this exactly).
  * `-p/--prompt <PROMPT>` — headless one-shot.
  * `--format {text,json}` (default `text`) — `text` crashed outright in
    every headless invocation tested (SIGSEGV, no output); `json` is what
    this module always passes, reassembled by `_GrokStream` below the same
    way Claude's and OpenCode's own JSON stream formats are.
  * `-m/--model` — argv, no env var documented for it.
  * No `--always-approve` or `--permission-mode` of any kind exists on this
    version. Nothing here supplies one; if headless tool calls turn out to
    need an explicit grant on some future version, RUNNER_GROK_ARGS is the
    escape hatch until a real flag is measured.
  * MCP: no `mcp` subcommand at the top level of `grok --help` on 1.1.7 (it
    was there on 1.0.0). This module still writes `.grok/config.toml` in the
    run's checkout on the chance the underlying support outlived the CLI
    subcommand that used to manage it — declared as a best-effort, not a
    proven capability; a run that needs MCP and doesn't get it will fail
    loudly with a real error, not silently.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from . import register
from .base import Knob, ShellResult
from .cli_base import CLIModule, split_cmd

logger = logging.getLogger("supervisor.grok")


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _servers_to_toml(servers: dict) -> str:
    """The subset of `grok`'s own config.toml shape this module needs: one
    `[mcp_servers.<name>]` table per server, http or stdio."""
    blocks = []
    for name, entry in servers.items():
        lines = [f"[mcp_servers.{name}]"]
        if entry.get("type") == "stdio":
            lines.append(f"command = {_toml_string(entry.get('command', ''))}")
            args = entry.get("args") or []
            lines.append("args = [" + ", ".join(_toml_string(a) for a in args) + "]")
        else:
            lines.append(f"url = {_toml_string(entry.get('url', ''))}")
            headers = entry.get("headers") or {}
            if headers:
                pairs = ", ".join(
                    f"{_toml_string(k)} = {_toml_string(v)}" for k, v in headers.items()
                )
                lines.append(f"headers = {{ {pairs} }}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""


class _GrokCollector:
    """Reassembles the final answer from `--format json`'s NDJSON stream.

    Shapes captured directly off a real run (grok-cli 1.1.7), not taken from
    documentation:

        {"type":"step_start","stepNumber":0,"timestamp":...,"sessionID":"..."}
        {"type":"text","stepNumber":0,"text":"...","timestamp":...,"sessionID":"..."}
        {"type":"step_finish","stepNumber":0,"timestamp":...,"finishReason":"stop",
                               "usage":{...},"sessionID":"..."}
        {"type":"error","message":"..."}

    Flat, unlike OpenCode's `part`-nested shape and Claude's message-block
    shape — each `text` event carries the field directly. Never raises: a
    malformed line is skipped, because losing one event is survivable and
    losing the run is not.
    """

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.error_message: str | None = None
        self.saw_json = False
        self.session_id: str | None = None

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
        if self.session_id is None:
            sid = event.get("sessionID")
            if isinstance(sid, str) and sid:
                self.session_id = sid
        etype = event.get("type")
        if etype == "text":
            chunk = event.get("text")
            if chunk:
                self.texts.append(str(chunk))
        elif etype == "error":
            self.error_message = str(event.get("message") or "unknown error")
        return event

    def final_text(self) -> str | None:
        if self.texts:
            return "\n".join(self.texts)
        return None


class _GrokStream:
    """Watches one `grok` invocation; hands back a ShellResult whose stdout
    is the final answer, the same contract `_ClaudeStream`/`_OpenCodeStream`
    keep. Progress narration is best-effort and must never affect the run."""

    def __init__(self, sink=None) -> None:
        self.collector = _GrokCollector()
        self.sink = sink

    def on_line(self, line: str) -> None:
        try:
            event = self.collector.feed(line)
            if event is None or self.sink is None:
                return
            etype = event.get("type")
            if etype == "tool_use":
                self.sink("tool", str(event.get("tool") or "tool"))
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
                self.sink(
                    "error",
                    "the agent's output could not be reassembled into an "
                    "answer — passing its raw stream through",
                )
        except Exception:  # noqa: BLE001
            logger.warning("could not reassemble agent output", exc_info=True)
        return res


class GrokModule(CLIModule):
    name = "grok"
    provider_type = "xai"

    settings = (
        Knob(
            "model",
            kind="text",
            delivery="argv",
            flag="-m",
            help="The model this run reasons with — no env var documented "
            "for it, so it rides on argv like Claude Code's.",
        ),
        Knob(
            "standing_instructions",
            kind="text",
            delivery="prompt",
            help="No system-prompt flag on this CLI version — prepended to "
            "the prompt text instead, same as OpenCode.",
        ),
        Knob(
            "mcp",
            kind="bool",
            delivery="argv",
            flag="",
            help="Best-effort: writes `.grok/config.toml` into the run's "
            "checkout — the CLI subcommand that used to manage this file no "
            "longer appears in `grok --help`, so whether it is still read "
            "is unverified.",
        ),
    )

    def mcp_argv(self, config_path: str) -> list[str]:
        try:
            data = json.loads(Path(config_path).read_text(encoding="utf-8"))
            servers = data.get("mcpServers") or {}
            grok_dir = Path(config_path).parent / ".grok"
            grok_dir.mkdir(parents=True, exist_ok=True)
            (grok_dir / "config.toml").write_text(
                _servers_to_toml(servers), encoding="utf-8"
            )
        except (OSError, ValueError) as e:
            logger.warning("could not write grok MCP config from %s: %s", config_path, e)
        return []

    def stream_watcher(self, sink):
        return _GrokStream(sink)

    def build_argv(
        self, prompt: str, run_kind: str, extra: list[str] | None = None
    ) -> list[str]:
        cmd = split_cmd(os.environ.get("RUNNER_GROK_CMD", "grok"))
        # US-32.8: the escape hatch applies last, as on every module. No
        # default args — 1.1.7 has no approval flag of any kind to default.
        args = split_cmd(os.environ.get("RUNNER_GROK_ARGS", ""))
        return [
            *cmd,
            "-p",
            prompt,
            "--format",
            "json",
            *(extra or []),
            *args,
        ]


register(GrokModule())
