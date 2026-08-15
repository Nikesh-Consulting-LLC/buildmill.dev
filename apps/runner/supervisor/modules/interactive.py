"""Buildmill Interactive Agent (US-78.3).

A fork of `xai-org/grok-build` driven over ACP (`agent stdio`) — a persistent
JSON-RPC session instead of a one-shot command line. That is the whole
difference, and it buys four things the argv modules cannot have: structured
live narration, permission interception, a channel the manager can type into
(US-78.8), and real session resume (US-78.9).

**Why this subclasses `CLIModule` after all.** The story said it should not,
reasoning that `cli_base` assumes one-shot argv. Reading it, that assumption
lives in exactly one method — `_run_cli`, which builds argv, runs it to
completion and finalizes a stream. Everything else in `execute()` is about the
*run*, not the CLI: prepare the checkout, write the MCP config, collect
`.factory-out/`, preserve WIP on failure, remove the token-bearing config in a
`finally`. That is ~200 lines of hard-won behavior (US-59.2's WIP preservation,
US-62.10's stage timings, US-31.9's config lifetime) and reimplementing it here
to satisfy a class-diagram preference would mean two copies of the part that
must not be got wrong.

So this overrides `_run_cli` and nothing else. `CLIModule` is not modified —
the other five modules see no change — and this module inherits the run shape
rather than a second version of it. The story's AC1 was updated to say so.

Everything above `_run_cli` still applies: `settings_argv`, `prompt_prefix`,
`_stage`, `_failed`, and the `ShellResult` contract `execute()` reads.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import time
from pathlib import Path
from typing import Any

from . import register
from .base import Knob, ShellResult
from .cli_base import CLIModule, split_cmd

logger = logging.getLogger("supervisor.interactive")

# The binary the provisioner installs (US-78.1). Deliberately NOT `grok`: the
# existing Grok Build module runs superagent-ai/grok-cli under that name and
# both are on every pool machine, so sharing it would be a coin flip over which
# agent answers.
DEFAULT_CMD = "buildmill-agent-cli"

# ACP mode is a subcommand, not a flag.
ACP_ARGS = ("agent", "stdio")

def _toml_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


# US-83.1: sections written into every config, model or no model. The CLI's own
# defaults are wrong for a fleet: `auto_update` defaults ON (a binary that can
# move mid-run is unmeasurable), and `[compat.claude]`/`[compat.cursor]`
# scanning defaults ON — the agent ingests skills, rules, agents, MCP servers
# and HOOKS from Claude/Cursor directories in whatever repo it checks out,
# which is behavior the factory never granted (docs.x.ai/build/settings/reference,
# defaults confirmed there).
_HARDENING = (
    "[cli]\n"
    "auto_update = false\n"
    "\n"
    "[compat.claude]\n"
    "skills = false\n"
    "rules = false\n"
    "agents = false\n"
    "mcps = false\n"
    "hooks = false\n"
    "\n"
    "[compat.cursor]\n"
    "skills = false\n"
    "rules = false\n"
    "agents = false\n"
    "mcps = false\n"
    "hooks = false\n"
)


def write_model_config(home: str, env: dict[str, str]) -> str | None:
    """The CLI's config file: the factory's model, and its doors closed.

    Env alone is not enough: `GROK_MODELS_BASE_URL` moves the endpoint, but the
    per-model block is what pins the backend shape and names the env var the
    key is read from. `env_key` rather than an inline `api_key` because an
    inline key would be a credential written to disk — the whole point of the
    mint is that it lives for one run and never lands anywhere.

    **The table is keyed by the model id itself, and that is load-bearing.**
    It was first written as `[model.buildmill]` with `model = "grok-4.5"`
    inside, on the assumption the inner field is what goes on the wire. It is
    not: measured against CLI 1.0.0, the ENTRY NAME is what it sends, and the
    provider answered `The model 'buildmill' does not exist` while the CLI's own
    diagnostic read `Available: buildmill`. Keying the table by the real model
    id makes the two agree no matter which one it reads.

    The key is quoted because a model id contains dots — an unquoted
    `[model.grok-4.5]` is a nested table (`model.grok."4-5"`), not a model
    called `grok-4.5`.

    US-83.1: the config is now written on EVERY call — the hardening sections
    (auto-update off, compat scanning off) must hold whether or not a model
    resolved. A model-less call writes a config with no `[model.*]` block,
    which retires the old delete-on-no-model rule by the same mechanism: the
    stale block from an earlier run is overwritten out of existence either way
    (measured live once — a run with no model kept using the previous run's
    `[model.buildmill]` block and 404'd on a name that no longer exists).

    `GROK_MODEL_CONTEXT_WINDOW`, when the env carries it, becomes the block's
    `context_window` — the docs say auto-compact timing depends on it, and a
    BYOK entry knows only what this file tells it. Absent, nothing is written:
    a wrong window is worse than none.

    Returns the path when a model block was written, None otherwise — callers
    key their no-model refusal on that, unchanged.
    """
    base = env.get("GROK_MODELS_BASE_URL")
    model = (env.get("GROK_MODEL") or "").strip()
    if not home:
        return None
    path = Path(home) / "config.toml"
    has_model = bool(base and model)
    body = _HARDENING
    if has_model:
        window = (env.get("GROK_MODEL_CONTEXT_WINDOW") or "").strip()
        block = (
            f"[model.{_toml_string(model)}]\n"
            + f"model = {_toml_string(model)}\n"
            + f"base_url = {_toml_string(base)}\n"
            + 'env_key = "BUILDMILL_GATEWAY_KEY"\n'
            + 'api_backend = "chat_completions"\n'
        )
        if window.isdigit():
            block += f"context_window = {int(window)}\n"
        body = block + "\n" + _HARDENING
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        # The file names an env var rather than holding a key, but the home
        # also holds session transcripts — 0600 on a shared pool machine.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:  # noqa: BLE001
        logger.warning("could not write the agent's model config at %s: %s", path, e)
        return None
    return str(path) if has_model else None


class LiveSession:
    """A running ACP session, addressable by run id (US-78.8).

    This is what makes the console possible: the server pushes `session.input`
    over the control socket, `session_input.handle` looks the run up here, and
    the manager's text becomes a prompt on the conversation already in flight.
    """

    def __init__(self, client: Any, session_id: str, emit: Any):
        self.client = client
        self.session_id = session_id
        self._emit = emit
        # One turn at a time. Two managers typing at once would otherwise
        # interleave into a single garbled prompt (US-78.8 AC8).
        self._lock = asyncio.Lock()

    async def steer(self, text: str, author: str = "") -> None:
        who = f" ({author})" if author else ""
        self._emit("decision", f"manager steered the run{who}: {text[:300]}")
        async with self._lock:
            await self.client.prompt(self.session_id, text)

    async def cancel(self) -> None:
        self._emit("decision", "the manager stopped this session")
        await self.client.cancel(self.session_id)


# run_id -> LiveSession, for the runs this machine is currently holding.
LIVE: dict[str, LiveSession] = {}


class InteractiveModule(CLIModule):
    name = "interactive"
    provider_type = "xai"

    # US-78.9: resume is `session/load`, gated at runtime on the agent actually
    # declaring `loadSession` — this flag only says the module knows how to ask.
    RESUME_SUPPORTED = True

    # us-96.8: this module's agent hands code back over factory MCP
    # (submit_changeset) — run 51cd4fd3 proved it does, and the factory
    # accepted it. One voice: the prompt says MCP, the harness never
    # commits the tree a second time, and the post-run sweep names any
    # file modified but never submitted.
    MCP_HANDBACK_KINDS = frozenset({"code"})

    settings = (
        Knob(
            "model",
            kind="text",
            delivery="runner",
            help="The model this run reasons with. Platform-managed: it is "
            "resolved server-side and reaches the CLI through its own config, "
            "not on a command line.",
        ),
        Knob(
            "effort",
            kind="enum",
            delivery="argv",
            flag="--reasoning-effort",
            # US-83.4: measured, not guessed — the CLI's own `initialize`
            # handshake declares the vocabulary per model (grok-4.5:
            # low/medium/high, default high). The value is a free string to
            # the CLI's parser (probed: `--reasoning-effort bogus` parses),
            # so a wrong choice here fails at the MODEL, not the argv — the
            # choices list is the only guard.
            choices=("low", "medium", "high"),
            help="How hard to think before acting. The CLI's default is "
            "`high`, so presets that want escalation headroom should run "
            "Balanced at `medium`.",
        ),
        Knob(
            "max_turns",
            kind="int",
            delivery="argv",
            flag="--max-turns",
            help="A ceiling on agent turns. Parse-verified before the agent "
            "subcommand on grok 1.0.0; whether `agent stdio` honors it per "
            "session is pending a live probe (us-83.4 AC3) — if it turns out "
            "inert, withdraw this knob rather than let it lie.",
        ),
        Knob(
            "standing_instructions",
            kind="text",
            delivery="prompt",
            help="Prepended to the prompt text. `--rules` exists and parses "
            "on grok 1.0.0, but whether agent-mode sessions apply it to the "
            "system prompt is unmeasured (us-83.4 AC4) — in-prompt delivery "
            "is the declared-and-real fallback until that probe runs.",
        ),
        Knob(
            "mcp",
            kind="bool",
            delivery="argv",
            flag="",
            help="The factory's tools, passed as a parameter of `session/new` "
            "rather than written to a config file the CLI may or may not read.",
        ),
    )

    # US-78.8: the run currently in `execute()`, so `_run_cli` can register its
    # live session under an id the server can address. `execute()` does not pass
    # the context down, and threading it through every caller of `_run_cli`
    # would change the signature for five other modules that do not need it.
    _run_id: str | None = None

    async def execute(self, ctx, prim):
        self._run_id = ctx.run_id
        try:
            return await super().execute(ctx, prim)
        finally:
            self._run_id = None

    def mcp_argv(self, config_path: str) -> list[str]:
        """Nothing on the command line: ACP takes MCP servers as a session
        parameter. `execute()` still hands us the path, and `_run_cli` reads it."""
        return []

    def build_argv(
        self, prompt: str, run_kind: str, extra: list[str] | None = None
    ) -> list[str]:
        """How the session host is spawned. There is no prompt here — the
        prompt is a message, not an argument, which is the point.

        US-83.4: `extra` (the delivered settings flags) goes BEFORE the
        subcommand — parse-verified on grok 1.0.0
        (`grok --max-turns 2 --reasoning-effort high agent stdio` parses;
        `agent stdio` itself takes only debug/leader flags). This used to
        drop `extra` on the floor, which was half of why escalation's effort
        never reached the CLI."""
        cmd = split_cmd(os.environ.get("RUNNER_INTERACTIVE_CMD", DEFAULT_CMD))
        args = split_cmd(os.environ.get("RUNNER_INTERACTIVE_ARGS", ""))
        return [*cmd, *(extra or []), *ACP_ARGS, *args]

    async def _run_cli(
        self,
        prim: Any,
        prompt: str,
        run_kind: str,
        cwd,
        timeout,
        mcp_config: str | None = None,
        ctx_settings: dict[str, Any] | None = None,
        resume_session_id: str | None = None,
    ) -> ShellResult:
        """One ACP session, presented to `execute()` as a `ShellResult`.

        The contract that has to hold: `stdout` is the agent's final answer,
        exactly as `--output-format text` would have produced, because every
        caller downstream parses it (`parse_stories`, the PRD, the plan).
        `exit_code` 0 means the turn completed.
        """
        # Imported here so `supervisor.modules` stays importable on a machine
        # where the ACP extra is not present — same reason mcpconfig is a local
        # import in cli_base.
        from ..acp import AcpError, describe_update
        from ..acp.engine import open_session
        from ..acp.events import Coalescer, _content_text

        resolved = ctx_settings or {}
        self._last_max_turns = resolved.get("max_turns")
        prefix = self.prompt_prefix(resolved)
        if prefix:
            prompt = f"{prefix}\n\n{prompt}"

        # US-83.4: the other half of knob delivery — the base class hands
        # settings_argv to build_argv, but this override builds its own argv
        # and used to leave the flags behind.
        argv = self.build_argv(prompt, run_kind, self.settings_argv(resolved))
        started = time.monotonic()
        session_id: str | None = None
        answer: list[str] = []
        coalescer = Coalescer()

        def emit(kind: str, line: str) -> None:
            if self._on_progress is None:
                return
            try:
                self._on_progress(kind, line)
            except Exception:  # noqa: BLE001 — narration must not break running
                pass

        async def on_update(params: dict) -> None:
            # US-83.2: the engine already filtered to unmuted session/update,
            # so a resume replay can no longer pollute `answer` (it used to —
            # chunks were collected before the mute check).
            update = (params or {}).get("update") or {}
            if update.get("sessionUpdate") == "agent_message_chunk":
                # The answer is assembled from the same events the trace shows,
                # so what the manager watched and what the factory stored are
                # the same text.
                chunk = _content_text(update.get("content"))
                if chunk:
                    answer.append(chunk)
            described = describe_update(params)
            if described is None:
                return
            for kind, line in coalescer.feed(*described, time.monotonic()):
                emit(kind, line)

        async def on_permission(tool_call: dict, outcome: str, reason: str) -> None:
            emit(
                "decision",
                f"permission {outcome} for {tool_call.get('title') or 'a tool'} ({reason})",
            )

        opened = None
        try:
            # US-78.5: written before the child starts, because the CLI reads
            # its config at startup. `GROK_HOME` comes from the slot's env file
            # (US-78.1), so each agent on a shared pool machine has its own.
            gateway_env = getattr(prim, "env", {}) or {}
            written = write_model_config(
                os.environ.get("GROK_HOME", ""), gateway_env
            )
            # US-78.5: a run with no model must not start. It used to proceed
            # and let the CLI fall back to whatever it was configured with,
            # which is how a missing model surfaced as an opaque 404 naming a
            # model nobody chose. Same rule as the gateway mint: no credential,
            # no run — a model is half of that credential.
            if written is None and gateway_env.get("GROK_MODELS_BASE_URL"):
                raise AcpError(
                    "this agent has no model to reason with. Set one on its "
                    "settings page under Model per role, or give the platform "
                    "a default run model — nothing was spent."
                )
            # US-83.2: the open sequence — spawn, handshake, tools, resume —
            # lives in the shared engine, the same one the session host uses.
            opened = await open_session(
                prim,
                argv,
                str(cwd),
                mcp_config=mcp_config,
                resume_session_id=resume_session_id,
                emit=emit,
                on_update=on_update,
                on_permission=on_permission,
            )
            session_id = opened.session_id
            self._last_session_id = session_id

            # US-78.8: from here the run is addressable — a manager attaching a
            # console can steer it. Registered before the first prompt, because
            # the first turn is the longest and the one worth interrupting.
            live = LiveSession(opened.client, session_id, emit)
            if self._run_id:
                LIVE[self._run_id] = live

            stop_reason = await opened.client.prompt(
                session_id, prompt, timeout=timeout or 3600
            )
            for kind, line in coalescer.drain():
                emit(kind, line)

            text = "".join(answer).strip()
            # US-78.5: the agent's ANSWER decides the outcome, not the label it
            # ended on. This was a fixed list of PascalCase stop reasons from
            # the spec (`Completed`, `ToolUse`, …); the real CLI returned
            # something outside it and a run that had written a complete PRD
            # was thrown away as a failure with the PRD sitting in its own
            # error message. Evidence over vocabulary, as everywhere else here.
            #
            # Two reasons override text (US-78.8, US-83.4): a cancel — the
            # manager stopped it, so what it had said is not an answer — and a
            # truncation. `max_tokens`/`max_turn_requests` mean the agent was
            # still talking when a ceiling cut it off; a half-written PRD
            # parses as a whole one downstream, which is worse than a loud
            # stop. (Measured vocabulary: end_turn, max_tokens,
            # max_turn_requests, refusal, cancelled — snake_case.)
            reason = stop_reason.strip().lower().replace("_", "").replace("-", "")
            cancelled = reason in ("cancelled", "canceled", "refusal", "refused")
            truncated = reason in ("maxtokens", "maxturnrequests")
            ok = bool(text) and not cancelled and not truncated
            if not ok:
                if cancelled:
                    emit("error", "the session was cancelled")
                elif truncated:
                    emit(
                        "error",
                        f"a ceiling cut the answer off mid-work ({stop_reason}) "
                        "— a truncated answer must not pass as a whole one",
                    )
                else:
                    emit(
                        "error",
                        "the session ended without an answer "
                        f"({stop_reason or 'no reason given'})",
                    )
            return ShellResult(
                argv=argv,
                exit_code=0 if ok else 1,
                stdout=text or opened.proc.stderr_tail(),
                # `error_max_turns` is the vocabulary `_failed` already reads
                # to name a turn-budget exit (US-54.1) — map ACP's word for
                # the same event onto it rather than teaching a second one.
                end_subtype=(
                    None
                    if ok
                    else ("error_max_turns" if reason == "maxturnrequests" else stop_reason)
                ),
                claude_session_id=session_id,
            )
        except Exception as e:  # noqa: BLE001 — a failed session is a failed run
            logger.warning("interactive session failed: %s", e, exc_info=True)
            for kind, line in coalescer.drain():
                emit(kind, line)
            # The engine already folded the child's stderr into an open-time
            # error and closed the process; a post-open failure still has one.
            tail = opened.proc.stderr_tail() if opened is not None else ""
            partial = "".join(answer).strip()
            detail = f"{e}"
            if tail:
                detail += f"\n{tail}"
            return ShellResult(
                argv=argv,
                exit_code=1,
                stdout=(partial + "\n" if partial else "") + detail,
                claude_session_id=session_id,
            )
        finally:
            self._last_duration = time.monotonic() - started
            self._last_timeout = timeout
            # US-78.8: unregister FIRST. A console that sends into a session
            # being torn down should be told the run is over, not raise inside
            # a closing client.
            if self._run_id:
                LIVE.pop(self._run_id, None)
            if opened is not None:
                await opened.client.close()
                await opened.proc.close()


register(InteractiveModule())
