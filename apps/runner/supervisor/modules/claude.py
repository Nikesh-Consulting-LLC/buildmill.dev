"""Claude Code agent module (US-10.5).

Headless: `claude -p "<prompt>" --output-format stream-json --verbose`
(+ bypass-permissions). Model access comes from the gateway env the supervisor
injects (ANTHROPIC_*).

US-39.1: stream-json rather than text, so a run is watchable while it runs
instead of being a black box with an answer at the end. The events are
reassembled into the final answer before anything downstream sees them, because
every module parses `res.stdout` -- `parse_stories`, the PRD, the plan -- and
NDJSON in that field would break all of it. `RUNNER_STREAM_PROGRESS=0` returns
to text mode exactly as it was.
"""

from __future__ import annotations

import logging
import os

from . import register
from .base import Knob, ShellResult
from .cli_base import CLIModule, split_cmd
from ..progress import StreamCollector, describe

logger = logging.getLogger(__name__)

# A chatty run must not turn one work item into ten thousand database rows.
# The console is uncapped; only the relay to the server stops, and it says so.
MAX_TRACED_LINES = 400

# US-47.1: what a headless factory run REQUIRES, stated by the module rather
# than inherited from an env default nobody has happened to set.
#
# It lived in `RUNNER_CLAUDE_ARGS`'s default until this story, which meant an
# operator setting that variable for any other reason -- the README advertises
# it as "override the CLI binary/flags" -- silently dropped the mode and put
# every run on that box into `default`, where every MCP call is refused and a
# code run therefore cannot read its own work item. It is emitted BEFORE
# `RUNNER_CLAUDE_ARGS`, so the escape hatch can still override it on purpose.
PERMISSION_MODE = ["--permission-mode", "bypassPermissions"]


def streaming_enabled() -> bool:
    return (os.environ.get("RUNNER_STREAM_PROGRESS") or "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


class _ClaudeStream:
    """Watches one `claude` invocation: narrates it, then hands back a
    ShellResult whose stdout is the final answer.

    Nothing here may raise into the run. Progress is the least important thing
    in the system and must behave like it -- us-36.1's whole lesson was a trace
    failure taking down the control socket and then the fleet.
    """

    def __init__(self, sink=None):
        self.collector = StreamCollector()
        self.sink = sink
        self.traced = 0
        self.capped = False

    def on_line(self, line: str) -> None:
        try:
            event = self.collector.feed(line)
            if event is None:
                return
            described = describe(event)
            if described is None:
                return
            kind, text = described
            # The machine's own console, always. This is the "console" the
            # request asked for and it costs nothing.
            logger.info("agent: %s", text)
            if self.sink is None or self.capped:
                return
            if self.traced >= MAX_TRACED_LINES:
                self.capped = True
                self.sink(
                    "progress",
                    f"[further progress not traced — {MAX_TRACED_LINES} line cap "
                    "reached; the agent is still running and its machine console "
                    "still has everything]",
                )
                return
            self.traced += 1
            self.sink(kind, text)
        except Exception:  # noqa: BLE001 -- watching must not break running
            logger.debug("progress line dropped", exc_info=True)

    def finalize(self, res: ShellResult) -> ShellResult:
        try:
            # US-54.1: the stream's verdict travels with the result either way,
            # so a budget exit (error_max_turns) is named, not guessed at.
            res.end_subtype = self.collector.result_subtype
            res.num_turns = self.collector.num_turns
            # US-59.1: same treatment as end_subtype/num_turns — travels with
            # the result on every path, success or not.
            res.claude_session_id = self.collector.session_id
            final = self.collector.final_text()
            if final is not None:
                return ShellResult(
                    argv=res.argv,
                    exit_code=res.exit_code,
                    stdout=final,
                    allowed=res.allowed,
                    end_subtype=self.collector.result_subtype,
                    num_turns=self.collector.num_turns,
                    claude_session_id=self.collector.session_id,
                )
            if self.collector.saw_json and self.sink is not None:
                # We read events but found no answer in them. Passing the raw
                # NDJSON through will fail whatever parses it, so say why here
                # rather than leaving a mystery in the run's error.
                self.sink(
                    "error",
                    "the agent's output could not be reassembled into an answer "
                    "— passing its raw stream through",
                )
        except Exception:  # noqa: BLE001
            logger.warning("could not reassemble agent output", exc_info=True)
        return res


class ClaudeModule(CLIModule):
    name = "claude"
    provider_type = "anthropic"
    # US-59.3: `claude -p --resume <session-id>` continues a prior session
    # headlessly, per Claude Code's own resume support. Grok and OpenCode have
    # no equivalent, so the base class defaults this false and only this
    # module turns it on — the cli_base resume branch checks it before ever
    # appending `--resume` to argv.
    RESUME_SUPPORTED = True

    # US-32.4: the richest declaration of the three CLIs — most of the
    # vocabulary the canonical setting names were drawn from is Claude Code's.
    # US-31.9's `supports_mcp` flag is now just the `mcp` knob below.
    settings = (
        Knob(
            "model",
            kind="text",
            delivery="env",
            flag="ANTHROPIC_MODEL",
            help="The model this run reasons with. Arrives through the gateway "
            "env, which is also what resolves the provider (US-27.8).",
        ),
        Knob(
            "fallback_model",
            kind="text",
            delivery="argv",
            flag="--fallback-model",
            help="Used when the primary model is overloaded, instead of failing "
            "the run.",
        ),
        Knob(
            "effort",
            kind="enum",
            delivery="argv",
            flag="--effort",
            # US-32.10: five levels, matching `claude --help`. The declaration
            # was written against three and the CLI has taken five for as long
            # as the flag has existed, so every preset in the app stopped one
            # level below `xhigh` — the level recommended for coding and
            # agentic work, and Claude Code's own default.
            choices=("low", "medium", "high", "xhigh", "max"),
            help="How hard to think before acting. Higher costs more and takes "
            "longer. `xhigh` is the recommended level for coding work.",
        ),
        # US-47.1: `permission_mode` is deliberately NOT declared. Measured
        # against the real CLI, `default`, `acceptEdits` and `plan` let ZERO
        # MCP calls through — a headless run has no approval channel, and only
        # `bypassPermissions` pre-approves the tools. Since every code and plan
        # run is handed --mcp-config and told to call get_work_context first
        # (us-31.9), the other three are not weaker configurations of a working
        # run; they are a run that cannot read its own work item. `plan` does it
        # while exiting 0, so a code run under it commits an empty tree and
        # reports success. See PERMISSION_MODE at the top of this module.
        Knob(
            "max_turns",
            kind="int",
            delivery="argv",
            flag="--max-turns",
            help="A ceiling on agent turns — a run that is going nowhere stops "
            "going there.",
        ),
        Knob(
            "standing_instructions",
            kind="text",
            delivery="argv",
            flag="--append-system-prompt",
            help="Appended to the system prompt — never replacing it, so the "
            "harness's own instructions survive.",
        ),
        Knob(
            "mcp",
            kind="bool",
            delivery="argv",
            flag="--mcp-config",
            help="Takes the factory's MCP server config, which is how a code or "
            "plan run reads context and hands work back (US-31.9).",
        ),
        # US-52.1: how a run is billed. `api` is today's path — a per-run
        # gateway key, metered. `subscription` delivers NO API-key variables at
        # all, so Claude Code falls through its credential chain to
        # CLAUDE_CODE_OAUTH_TOKEN or the machine's own `claude /login` state
        # and bills the operator's Claude subscription. `platform` (US-60.1)
        # is the same gateway-key path as `api`, just resolved against the
        # platform's own key server-side — nothing here tells them apart.
        # The supervisor consumes this (delivery="runner"); nothing about it
        # reaches the CLI's argv.
        Knob(
            "auth",
            kind="enum",
            delivery="runner",
            choices=("api", "subscription", "platform"),
            help="Claude Code — API mints a metered gateway key per run. "
            "Claude Code — OAuth delivers no API key, so the CLI bills the "
            "machine's Claude subscription (CLAUDE_CODE_OAUTH_TOKEN or its "
            "login state). Subscription runs are off the spend meter. "
            "Platform bills the superadmin's own key (Buildmill Agent).",
        ),
    )

    def mcp_argv(self, config_path: str) -> list[str]:
        # --strict-mcp-config so the agent's tool surface is exactly what the
        # factory granted, not whatever MCP configuration happens to exist on
        # the machine. Phase 34 adds more servers to this same file
        # deliberately; nothing arrives by accident.
        return ["--mcp-config", config_path, "--strict-mcp-config"]

    def build_argv(
        self, prompt: str, run_kind: str, extra: list[str] | None = None
    ) -> list[str]:
        cmd = split_cmd(os.environ.get("RUNNER_CLAUDE_CMD", "claude"))
        # US-32.8: RUNNER_CLAUDE_ARGS survives as a machine-level escape hatch
        # and is applied LAST, so an operator on the box can still override what
        # the app configured. Nothing the app manages depends on it any more.
        args = split_cmd(os.environ.get("RUNNER_CLAUDE_ARGS", ""))
        # US-39.1: `--verbose` is required by the CLI alongside `-p` and
        # stream-json; without it the format is refused.
        fmt = (
            ["--output-format", "stream-json", "--verbose"]
            if streaming_enabled()
            else ["--output-format", "text"]
        )
        return [*cmd, "-p", prompt, *fmt, *PERMISSION_MODE, *(extra or []), *args]

    def stream_watcher(self, sink):
        if not streaming_enabled():
            return None
        return _ClaudeStream(sink)


register(ClaudeModule())
