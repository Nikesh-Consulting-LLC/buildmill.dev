"""Agent module contract (US-10.4).

A module = an agent. Each implements `execute(ctx, prim) -> ModuleResult` using
the two runner primitives — `run_shell` (audited) and `run_api`. Modules are
discovered by the registry so adding an agent is a drop-in. `ModuleResult`
mirrors the worker submit contract so the supervisor (US-10.6) can hand a result
straight to `/worker/runs/{id}/submit`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ModuleResult:
    outcome: str  # "succeeded" | "failed"
    stdout: str | None = None
    diff: str | None = None
    branch_ref: str | None = None
    pr_url: str | None = None
    error: str | None = None
    test_cases: list | None = None
    plan: str | None = None
    test_plan: str | None = None
    prd: str | None = None
    stories: list | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    # US-27.12: the evidence a failure is judged on. The brain's diagnosis of
    # run 802506c9 was "the CLI timed out because the command exceeded the
    # default execution limit" — the command had exited in eight seconds with
    # a one-line message naming the real problem. Nothing recorded either
    # fact, so nothing could contradict the invention.
    exit_code: int | None = None
    duration_seconds: float | None = None
    # US-59.1: the Claude Code CLI session id, captured whenever the module's
    # stream carried one — success, failure, or a turn-limit pause alike. The
    # server persists it so a later resume has something to resume.
    claude_session_id: str | None = None
    # US-59.2: whether a best-effort WIP commit preserved the workdir's
    # uncommitted changes before this (non-success) result was returned —
    # so a resumed session's conversation and its code stay in sync instead
    # of the next checkout silently discarding what the agent had written.
    wip_preserved: bool = False


@dataclass
class RunContext:
    """What a module needs for one run — a thin view over the work-context
    bundle plus the gateway env the supervisor injects (US-10.3/10.6)."""

    run_id: str
    kind: str  # any dispatchable run kind (US-43.7)
    context: dict[str, Any] = field(default_factory=dict)
    branch_name: str | None = None
    git_remote_url: str | None = None
    default_branch: str = "main"
    model_env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int = 1200
    # US-59.3: when the server claims this run back onto the worker that
    # already holds its local transcript, it carries the Claude Code session
    # id to resume — `claude -p --resume <id>` continues that exact
    # conversation instead of starting a fresh prompt. None for an ordinary
    # (non-resumed) claim.
    resume_session_id: str | None = None
    # US-32.7/32.8: the settings this run was resolved to, decided once
    # server-side. The module turns them into argv, environment or prompt text
    # through its own declaration; it never re-decides what they should be.
    settings: dict[str, Any] = field(default_factory=dict)
    # US-34.2/34.3: the tool servers this run was granted, and one line per
    # server it was NOT — so the agent is told what is missing and why rather
    # than silently receiving a smaller toolset.
    tool_servers: list[dict[str, Any]] = field(default_factory=list)
    tool_notes: list[str] = field(default_factory=list)


@dataclass
class ShellResult:
    argv: list[str]
    exit_code: int
    stdout: str
    allowed: bool = True
    # US-54.1: how a streaming CLI said its session ended (the result event's
    # `subtype`, e.g. "error_max_turns"), and how many turns it used. None for
    # non-streaming runs. The failure path reads these so a budget exit is
    # named instead of reported as a bare exit code.
    end_subtype: str | None = None
    num_turns: int | None = None
    # US-59.1: the CLI's own session id, when the module's stream carried one.
    claude_session_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.allowed and self.exit_code == 0


@runtime_checkable
class Primitives(Protocol):
    """The entire surface a module has onto the machine and network."""

    async def run_shell(
        self, argv: list[str], cwd: str | None = None, timeout: int | None = None
    ) -> ShellResult: ...

    async def run_api(self, method: str, url: str, **kwargs: Any) -> Any: ...


# ---------------------------------------------------------------------------
# US-32.4: what a module can be told
# ---------------------------------------------------------------------------
# Every tuning dial worth having is CLI-specific. `--effort`, `--fallback-model`,
# `--permission-mode`, `--max-turns`, `--append-system-prompt` are Claude Code's
# vocabulary; Grok spells some of them differently and lacks others; OpenCode
# cannot even take Claude's prompt shape; `sim` has none of it. A Claude-shaped
# form that three modules pretend to fit makes every new module a schema
# migration and every unsupported field a setting that appears to work and
# silently does nothing.
#
# So the module declares — beside the code that builds its command line, because
# a declaration that lives anywhere else drifts from what it describes.

# The canonical setting names. Presets (us-32.5) and the resolver (us-32.7)
# speak these; a module says which of them it understands and how it takes them.
#
# US-47.1 removed `permission_mode` from this list. It was Claude Code's
# vocabulary and it declared cleanly, but only one of its four values produces
# a run that can call an MCP tool at all -- see `claude.PERMISSION_MODE`. A
# canonical name is a promise that setting it changes something.
KNOWN_SETTINGS = (
    "model",
    "fallback_model",
    "effort",
    "max_turns",
    "standing_instructions",
    "mcp",
    "auth",
)

# How a setting reaches the CLI. `prompt` is a real delivery: a module with no
# flag for standing instructions can still be given them in the text it reads.
# US-52.1 adds `runner`: a setting the SUPERVISOR consumes — it never reaches
# the CLI as a flag or variable, because its effect is which environment the
# child process gets in the first place.
DELIVERIES = ("argv", "env", "prompt", "runner")


@dataclass(frozen=True)
class Knob:
    """One setting a module understands, and how it is expressed."""

    name: str
    kind: str  # "text" | "int" | "number" | "enum" | "bool"
    delivery: str  # one of DELIVERIES
    flag: str = ""  # the argv flag or the env var name
    choices: tuple[str, ...] = ()
    help: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "delivery": self.delivery,
            "flag": self.flag,
            "choices": list(self.choices),
            "help": self.help,
        }


def declaration(module: Any) -> dict[str, Any]:
    """What this module accepts, in the shape the runner reports on hello.

    The server stores it with the runner's session so the settings page can be
    honest about a module even while the machine is offline.
    """
    knobs = tuple(getattr(module, "settings", ()) or ())
    return {
        "module": getattr(module, "name", ""),
        "capabilities": sorted(getattr(module, "capabilities", ()) or ()),
        "needs_repo": bool(getattr(module, "needs_repo", True)),
        "settings": [k.as_dict() for k in knobs],
    }


def supports(module: Any, setting: str) -> bool:
    """Whether `module` declares `setting`. The single question every caller
    asks — including the MCP gate us-31.9 introduced as a one-off flag."""
    return any(
        k.name == setting for k in (getattr(module, "settings", ()) or ())
    )


@runtime_checkable
class AgentModule(Protocol):
    name: str
    capabilities: set[str]
    # US-32.4: the knobs this module understands. Empty is a legitimate
    # answer — `sim` takes no tuning and says so.
    settings: tuple[Knob, ...]

    async def execute(self, ctx: RunContext, prim: Primitives) -> ModuleResult: ...
