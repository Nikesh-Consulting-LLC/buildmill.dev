"""CLI agent-module base (US-10.5).

The shared body for headless-CLI agents (Claude Code, Grok Build, OpenCode).
Concrete modules supply only `name`, `provider_type`, and `build_argv(prompt,
run_kind)` — everything else (checkout via the factory remote, prompt building,
commit/push, `.factory-out/` reading, prd/breakdown scratch runs) is shared and
routed through the `run_shell` primitive, so it's audited (US-10.7) and testable.
The model gateway env is injected by the supervisor via the primitives, not here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import time
from pathlib import Path
from typing import Any

from .. import gitwork
from .base import Knob, ModuleResult, Primitives, RunContext, supports

OUT_DIR = gitwork.OUT_DIR
logger = logging.getLogger("supervisor.cli_base")


def split_cmd(value: str) -> list[str]:
    return shlex.split(value, posix=(os.name != "nt"))


# US-43.7: the runner knows hand-back SHAPES, not run kinds.
#
# `capabilities` used to be {code, plan, prd, breakdown} and the prompt had two
# branches — `plan`, and "everything else is a code run". Five kinds shipped
# since (test, release, deploy, guidelines, elaborate) and every one of them was
# refused with "the claude module does not do 'X' work". Widening the set alone
# would have been worse than the refusal: a guidelines run would have been told
# to implement a change and let the harness commit it.
#
# The task description was never the runner's to give. `get_work_context`
# returns `get_worker_instruction(project, kind)` — the per-kind text a manager
# can edit on the Worker Instructions tab. What the runner owns is what happens
# to the result afterwards, and there are only three of those.
#
# US-48.2: the sentence that used to end this comment — "a kind added later
# needs no change here unless it invents a fourth" — was NOT true, and
# `wireframe` proved it. `handback_shape()` does default an unknown kind
# safely, but `capabilities` below is `set(HANDBACK_SHAPE)`, a CLOSED set, and
# `workloop._module_can_take` refuses anything outside it with "the claude
# module does not do 'X' work". So a new kind is still refused — it just gets
# refused by the capability check instead of the prompt builder. Adding the
# row below is the whole fix for this kind; `test_runner_kind_coverage.py`
# pins the set against the database's own `runs_kind_check` so the next kind
# fails a test here instead of silently sitting in the queue forever.
HANDBACK_FILES = "files"      # the harness reads files out of OUT_DIR
HANDBACK_WORKTREE = "worktree"  # the harness commits the working tree
HANDBACK_MCP = "mcp"          # the agent submits directly; nothing is collected

HANDBACK_SHAPE: dict[str, str] = {
    "plan": HANDBACK_FILES,
    "code": HANDBACK_WORKTREE,
    "prd": HANDBACK_MCP,
    "breakdown": HANDBACK_MCP,
    "test": HANDBACK_MCP,
    "release": HANDBACK_MCP,
    "deploy": HANDBACK_MCP,
    "guidelines": HANDBACK_MCP,
    "elaborate": HANDBACK_MCP,
    "wireframe": HANDBACK_MCP,
}


def handback_shape(run_kind: str) -> str:
    """How the result of this kind comes home.

    Unknown kinds get `mcp` — the SAFE default. A kind this runner has never
    heard of must not be told to leave a working tree for the harness to
    commit; being told to submit over MCP is wrong-but-harmless, and the
    factory's own instruction will put it right.
    """
    return HANDBACK_SHAPE.get(run_kind, HANDBACK_MCP)


def _task_section(run_kind: str, shape: str | None = None) -> str:
    """What the runner has to say about the task: only how to hand it back.

    Deliberately says nothing about WHAT the work is. That lives in the
    factory's per-kind worker instruction, which the agent fetches with
    get_work_context — and which a manager can edit per project. Repeating a
    guess at it here is what made this module wrong for five run kinds, and
    what made a manager's edit lose to hardcoded prose.

    us-96.8: `shape` overrides the kind's default — the transport actually
    in play, resolved by the module (`handback_shape_for`). A code run whose
    agent hands back over factory MCP must never read the worktree wording:
    run 51cd4fd3 (2026-08-14) spent four minutes deliberating between this
    section and get_work_context, then had its changeset refused for the
    `.factory-out/test_cases.json` this section told it to write.
    """
    shape = shape or handback_shape(run_kind)
    if shape == HANDBACK_FILES:
        return (
            "## Handing back\nDo NOT modify any project file. Write the "
            f"implementation plan to {OUT_DIR}/plan.md and the test plan to "
            f"{OUT_DIR}/test_plan.md. Optionally propose structured cases in "
            f"{OUT_DIR}/test_cases.json. The harness collects those files."
        )
    if shape == HANDBACK_WORKTREE:
        return (
            "## Handing back\nLeave your changes uncommitted — the harness "
            "commits and pushes. Do not create branches or open PRs. Write "
            f"test cases a human should run to {OUT_DIR}/test_cases.json.\n\n"
            "A refused hand-back is NOT a finished run: if a tool call is "
            "refused, read the reason, fix it and try again — never report "
            "success you were not given."
        )
    if run_kind == "code":
        # us-96.8: code over MCP — the agent edits the checkout but the
        # factory does all the git. One voice, one submit tool, one route
        # for test cases, and the echo check that catches a partial submit.
        return (
            "## Handing back\nEdit files in this checkout freely, but do "
            "not commit, do not create branches, do not open PRs, and "
            "write no factory scratch files — the factory does all the "
            "git. Hand back with submit_changeset: it takes your changed "
            "files, and its `test_cases` field is the one place for the "
            "test cases a human should run (never a file). Then check the "
            "submit's echo — the files it says it received — against your "
            "own list of changed files before reporting done; a partial "
            "hand-back is yours to catch.\n\n"
            "A refused hand-back is NOT a finished run: if a tool call is "
            "refused, read the reason, fix it and try again — never report "
            "success you were not given."
        )
    return (
        "## Handing back\nWrite no project file and commit nothing — this run "
        "hands back entirely through the factory's tools. get_work_context "
        "names the submit tool for this run and what it expects; call it and "
        "follow it.\n\n"
        "A refused hand-back is NOT a finished run: if a tool call is "
        "refused, read the reason, fix it and try again — never report "
        "success you were not given."
    )


def _mcp_sections(ctx: RunContext, run_kind: str, shape: str | None = None) -> str:
    """US-31.9: the prompt as a POINTER, not a payload.

    With the factory's MCP server wired into the CLI, the agent fetches what
    it needs when it needs it — including files nobody anticipated — instead
    of living off whatever `_sections()` guessed at before the work started.
    The identifying facts stay inline so the agent knows what it is doing
    before its first tool call.
    """
    c = ctx.context or {}
    run_id = ctx.run_id
    out = [
        "You are completing a Software Factory work item in this checkout.",
        f"# {c.get('title', 'Work item')} ({c.get('type', 'story')})",
        f"Run id: `{run_id}`",
        "\n## Your tools\n"
        "The `factory` MCP server is configured and is your source of truth. "
        "Do NOT run git — the factory owns the repository and does every "
        "commit itself.\n"
        f"- `get_work_context(\"{run_id}\")` — the story, acceptance "
        "criteria, approved plan and test plan, the manager's test cases, and "
        "how to hand back. **Call this first.**\n"
        "- `get_project_guidelines()` / `get_project_learnings()` — how this "
        "project expects code to be written.\n"
        "- `get_repo_tree()` / `read_repo_file(path)` — study anything you "
        "need, including files not in your working copy.\n"
        f"- `report_progress(\"{run_id}\", ...)` — say what you are doing; it "
        "also keeps your claim alive.\n"
        f"- `request_clarification(\"{run_id}\", ...)` — ask when the work is "
        "genuinely ambiguous, instead of guessing.\n",
    ]
    # US-34.3: the tools beyond the factory's own, and — just as important — the
    # ones this run does NOT have, and why. An agent that silently receives a
    # smaller toolset produces worse work for reasons nobody can see afterwards.
    if ctx.tool_servers:
        lines = [
            f"- `{e.get('slug')}` ({e.get('name')}): "
            + (", ".join(e.get("tools") or []) or "see its own tool list")
            for e in ctx.tool_servers
        ]
        out.append(
            "## Other tool servers you have\n"
            + "\n".join(lines)
            + "\n\nThese are configured and ready. Use them rather than "
            "guessing — a browser check beats asserting the page works."
        )
    if ctx.tool_notes:
        out.append(
            "## Tools you do NOT have on this run\n"
            + "\n".join(f"- {note}" for note in ctx.tool_notes)
            + "\n\nWork within that. Say so in your hand-back if it stopped you "
            "verifying something, rather than claiming you verified it."
        )
    out.append(_task_section(run_kind, shape=shape))
    return "\n\n".join(out)


def _sections(
    ctx: RunContext, run_kind: str, mcp: bool = False, shape: str | None = None
) -> str:
    if mcp:
        return _mcp_sections(ctx, run_kind, shape=shape)
    c = ctx.context or {}
    out = [
        "You are completing a Software Factory work item inside this git checkout.",
        f"# {c.get('title', 'Work item')} ({c.get('type', 'story')})",
    ]
    for key, heading in (
        ("story", "Story"),
        ("acceptance_criteria", "Acceptance criteria"),
        ("plan", "Approved implementation plan"),
        ("test_plan", "Approved test plan"),
        ("prd", "PRD context"),
        ("guidelines", "Project guidelines"),
        ("learnings", "Project learnings"),
        ("feedback", "Rejection feedback — this is a retry; address it"),
    ):
        val = c.get(key)
        if not val:
            continue
        if isinstance(val, list):
            val = "\n".join(f"- {v}" for v in val)
        out.append(f"## {heading}\n{val}")
    out.append(_task_section(run_kind, shape=shape))
    return "\n\n".join(out)


def _prd_prompt(ctx: RunContext) -> str:
    c = ctx.context or {}
    out = [
        "Write a product requirements document (PRD). Respond with ONLY markdown "
        "containing exactly these headings, in order: ## Problem, ## Goals, "
        "## Out of scope, ## Acceptance criteria.",
        f"# {c.get('title', 'Feature')}",
    ]
    for key, heading in (("story", "Raw idea"), ("feedback", "Send-back feedback")):
        if c.get(key):
            out.append(f"## Context: {heading}\n{c[key]}")
    return "\n\n".join(out)


def _breakdown_prompt(ctx: RunContext) -> str:
    c = ctx.context or {}
    return (
        "Break this feature's approved PRD into engineering stories. Respond with "
        'ONLY a JSON array; each element {"title": string, "body": string, '
        '"acceptance_criteria": [string]}.\n\n'
        f"# {c.get('title', 'Feature')}\n\n## PRD\n{c.get('prd', '')}"
    )


def _release_prep_prompt(ctx: RunContext) -> str:
    c = ctx.context or {}
    prep_id = c.get("prep_id", "")
    version = c.get("version", "")
    return (
        "You already hold a claimed release-prep job — the factory claimed "
        "it on your behalf before this session started, the moment it "
        "assigned you the work. Do NOT call claim_release_prep_work or "
        "list_release_prep_work: the job is 'running', not 'queued', so "
        "claiming it again will only ever answer 'not available to claim', "
        "and listing the queue will never show your own in-progress job — "
        "neither of those means someone else has it. Go straight to reading "
        "the changes and writing the notes.\n\n"
        "This is NOT story work and has no repository checkout — everything "
        "happens through the factory MCP tools below.\n\n"
        f"Prep id: `{prep_id}`  Release: {version}\n\n"
        "## Your tools\n"
        f"- `get_release_changes(\"{prep_id}\", path_prefix=\"\", cursor=0)` — "
        "the commits and changed files since the previous release, and the "
        "work items this release includes. Read this BEFORE writing anything; "
        "page through it with `cursor` when `truncated` is true. If you "
        "cannot read the whole range, say so in the notes rather than "
        "guessing.\n"
        f"- `submit_release_notes(\"{prep_id}\", notes_summary, notes_detail, "
        "test_cases)` — hands the job back. The FIRST LINE of notes_summary "
        f"must contain the version `{version}` verbatim. notes_summary is a "
        "few lines a manager reads at a glance; notes_detail explains what "
        "actually changed (schema, migrations, modules) from what you read "
        "in get_release_changes. test_cases is optional: regression cases "
        "(title, steps, expected_result) for the release as a whole.\n\n"
        "## Handing back\n"
        "That is the whole job — call submit_release_notes and stop. "
        "Deploying to UAT and everything after is the system's own pipeline, "
        "fired the moment that call succeeds; there is nothing else to "
        "trigger or verify. A refused call is NOT a finished job: read why, "
        "fix it, and try again — never report done without a successful "
        "submit_release_notes."
    )


def parse_stories(text: str) -> list:
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    out = []
    for s in data if isinstance(data, list) else []:
        if isinstance(s, dict) and str(s.get("title") or "").strip():
            out.append(
                {
                    "title": str(s["title"]).strip(),
                    "body": str(s.get("body") or "").strip(),
                    "acceptance_criteria": [
                        str(a) for a in (s.get("acceptance_criteria") or [])
                    ],
                }
            )
    return out


def _read_out(workdir: Path, name: str) -> str | None:
    p = workdir / OUT_DIR / name
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def _read_test_cases(workdir: Path) -> list | None:
    raw = _read_out(workdir, "test_cases.json")
    if not raw:
        return None
    try:
        cases = json.loads(raw)
        return cases if isinstance(cases, list) else None
    except json.JSONDecodeError:
        return None


class CLIModule:
    """Base class; concrete modules set name/provider_type and build_argv."""

    name: str = "cli"
    provider_type: str = ""
    # US-59.3: whether this CLI understands `--resume` (or an equivalent).
    # False by default — a module that cannot resume must not be handed a
    # resume id and silently ignore it, or treat it as a second prompt.
    RESUME_SUPPORTED: bool = False
    # US-43.7: every dispatchable kind. This was {code, plan, prd, breakdown}
    # and had not moved since it was written, so `test` (us-13.11), `release`
    # (us-13.12), `deploy` (us-13.13), `guidelines` (us-43.1) and `elaborate`
    # (us-44.1) were all refused. It is safe to widen only because the prompt
    # now speaks in hand-back shapes rather than guessing at each task.
    capabilities = set(HANDBACK_SHAPE)
    # us-96.8: kinds THIS module's agent hands back over factory MCP even
    # though the kind's default shape says otherwise. The interactive (ACP)
    # module sets {"code"}: its agent submits with submit_changeset, so the
    # harness must not commit the tree a second time — and the prompt must
    # speak with one voice about it.
    MCP_HANDBACK_KINDS: frozenset[str] = frozenset()

    def handback_shape_for(self, run_kind: str) -> str:
        """The transport actually in play for this module and kind — the
        kind's default shape unless the module declares an MCP hand-back."""
        if run_kind in self.MCP_HANDBACK_KINDS:
            return HANDBACK_MCP
        return handback_shape(run_kind)

    # US-32.4: what this module can be told. Subclasses declare their own; the
    # base declares nothing, so a module that forgets is honestly untunable
    # rather than accidentally Claude-shaped.
    settings: tuple[Knob, ...] = ()

    # US-31.9: whether this CLI can be handed an MCP server config. Claude Code
    # takes `--mcp-config` / `--strict-mcp-config`; Grok and OpenCode spell it
    # differently or not at all. A module that cannot be given MCP must SAY so
    # rather than fall back to a git path us-31.9 deleted — the us-27.9 rule.
    # US-32.4 turned that one-off flag into one entry in the declaration above,
    # so there is a single source of truth instead of two that can disagree.
    @property
    def supports_mcp(self) -> bool:
        return supports(self, "mcp")

    # US-31.9: a CLI module works in a real checkout of the project, which is
    # what makes MCP support load-bearing for it. `sim` fabricates its results
    # and touches no repository at all, so the requirement must not reach it —
    # blocking it would break the integration harness that proves the
    # supervisor→submit path without a model.
    needs_repo: bool = True

    def build_argv(
        self, prompt: str, run_kind: str, extra: list[str] | None = None
    ) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def mcp_argv(self, config_path: str) -> list[str]:
        """Flags that point this CLI at `config_path` and nothing else."""
        return []

    # ---------------------------------------------------------------- US-32.8
    # Everything above this line decided what a run SHOULD use. These three
    # make it true at the point it matters: the command line.
    #
    # The mapping comes from the module's own declaration (us-32.4), so a knob
    # is delivered by the same statement that advertises it — there is no second
    # table to forget to update.

    def settings_argv(self, resolved: dict[str, Any] | None) -> list[str]:
        """The resolved settings this module takes on its command line."""
        out: list[str] = []
        for knob in self.settings:
            if knob.delivery != "argv" or not knob.flag:
                continue
            # `mcp` is argv, but its value is a config path the harness writes,
            # not a tunable — `mcp_argv` owns it.
            if knob.name == "mcp":
                continue
            value = (resolved or {}).get(knob.name)
            if value is None or value == "":
                continue
            if knob.kind == "bool":
                if value:
                    out.append(knob.flag)
                continue
            out.extend([knob.flag, str(value)])
        return out

    def prompt_prefix(self, resolved: dict[str, Any] | None) -> str:
        """US-32.9: standing instructions for a module with no flag for them.

        Claude Code takes `--append-system-prompt`, which layers on top of the
        default and keeps the CLI's own tool guidance and safety instructions.
        Grok and OpenCode have no equivalent, so the text goes into the prompt
        instead — weaker than a system prompt, but real, and declared as such
        rather than silently dropped.
        """
        for knob in self.settings:
            if knob.name != "standing_instructions" or knob.delivery != "prompt":
                continue
            text = str((resolved or {}).get("standing_instructions") or "").strip()
            if text:
                return (
                    "## Standing instructions\n"
                    "How this agent is expected to approach the work, set by "
                    "the manager. These do not replace anything below.\n\n"
                    f"{text}"
                )
        return ""

    async def _run_cli(
        self,
        prim: Primitives,
        prompt: str,
        run_kind: str,
        cwd,
        timeout,
        mcp_config: str | None = None,
        ctx_settings: dict[str, Any] | None = None,
        resume_session_id: str | None = None,
    ):
        """Run the CLI, remembering how long it took.

        US-27.12: the duration is evidence. A command that exits non-zero in
        eight seconds did not time out, whatever anything downstream says
        about it.

        US-32.8/32.9: the resolved settings become argv here, and standing
        instructions become prompt text for a module with no flag for them.
        `RUNNER_*_ARGS` still applies and applies LAST — a machine-level escape
        hatch, deliberately able to override what the app configured, and
        deliberately not something anything the app manages depends on."""
        resolved = ctx_settings or {}
        # US-54.1: kept so a turn-budget exit can name the setting that ended it.
        self._last_max_turns = resolved.get("max_turns")
        prefix = self.prompt_prefix(resolved)
        if prefix:
            prompt = f"{prefix}\n\n{prompt}"
        argv = self.build_argv(prompt, run_kind, self.settings_argv(resolved))
        if mcp_config and self.supports_mcp:
            # US-31.9: the agent gets the factory's own tools.
            argv = [*argv, *self.mcp_argv(mcp_config)]
        if resume_session_id and self.RESUME_SUPPORTED:
            # US-59.3: continue the SAME conversation instead of starting a
            # fresh one — the server only ever hands this id back to the one
            # worker whose local workspace holds the matching transcript.
            argv = [*argv, "--resume", resume_session_id]
        started = time.monotonic()

        # US-39.1: a module that streams events reassembles its own stdout, so
        # everything downstream of here -- parse_stories, the PRD text, the plan
        # -- keeps receiving exactly what `--output-format text` produced. The
        # base class stays format-agnostic: it asks the module for a watcher and
        # gives the module back the raw result to reinterpret.
        watcher = self.stream_watcher(self._on_progress)
        if watcher is None:
            # Passed conditionally so any Primitives implementation that
            # predates US-39.1 keeps working unchanged when nothing streams.
            res = await prim.run_shell(argv, cwd=cwd, timeout=timeout)
        else:
            res = await prim.run_shell(
                argv, cwd=cwd, timeout=timeout, on_line=watcher.on_line
            )
            res = watcher.finalize(res)
        self._last_duration = time.monotonic() - started
        self._last_timeout = timeout
        # US-59.1: kept on `self` alongside `_last_duration`/`_last_timeout` —
        # the same pattern `_failed()` already reads from, so every caller of
        # `_run_cli` can attach it to whatever ModuleResult it returns without
        # threading it through every return path by hand.
        self._last_session_id = getattr(res, "claude_session_id", None)
        return res

    # US-39.1: set by the workloop before a run, so a module can report progress
    # without knowing anything about traces or sockets.
    _on_progress = None
    # US-59.1: the last `_run_cli` call's captured session id, read by
    # `_failed()` and by `execute()`'s own return paths.
    _last_session_id: str | None = None

    def set_progress_sink(self, sink) -> None:
        """`sink(kind, line)` is called for each progress event. Optional: a
        module with no sink still streams to its own logger."""
        self._on_progress = sink

    def _stage(self, name: str, elapsed_ms: int) -> None:
        """US-62.10: a run's time, broken into named stages -- timed HERE,
        by the supervisor's own control flow, rather than depending on the
        CLI agent to narrate a stage boundary it may never mention. Encoded
        in the existing `content` field as `stage:<name> <ms>ms` (no new
        `run_trace` column) -- `db.run_stage_durations` parses it back out.
        Best-effort: a run must never fail because a stage line could not
        be sent."""
        if not self._on_progress:
            return
        try:
            self._on_progress("step", f"stage:{name} {elapsed_ms}ms")
        except Exception:  # noqa: BLE001
            pass

    def stream_watcher(self, sink):
        """Return an object with `on_line(str)` and `finalize(ShellResult)`, or
        None for a module that does not stream. Default: does not stream."""
        return None

    def _failed(self, res, summary: str) -> ModuleResult:
        """A failure that leads with what the command actually said.

        The old text was "CLI failed or timed out" — a guess, stored in
        `runs.error` and then handed to the brain as the only thing it knew
        about the failure. Asked why a command "failed or timed out", it
        answered that it had timed out. Evidence first, always."""
        tail = (res.stdout or "").strip()[-1200:]
        if getattr(res, "end_subtype", None) == "error_max_turns":
            # US-54.1: the CLI's own verdict. This is a budget exit, not a
            # crash — the agent was still working when the turn cap ended the
            # session, and the exit code alone says none of that.
            cap = getattr(self, "_last_max_turns", None)
            turns = getattr(res, "num_turns", None)
            message = (
                f"the {self.name} agent hit its turn ceiling"
                + (f" (max_turns={cap})" if cap is not None else "")
                + (f" after {turns} turns" if turns is not None else "")
                + f" — the session was ended mid-work after "
                  f"{(getattr(self, '_last_duration', 0) or 0):.0f}s"
            )
        else:
            message = (
                f"the {self.name} CLI exited {res.exit_code} after "
                f"{(getattr(self, '_last_duration', 0) or 0):.0f}s"
            )
        if tail:
            # Fenced: the surfaces render `runs.error` as markdown, and the
            # command's own words must arrive verbatim rather than reflowed
            # into a paragraph of the harness's prose.
            message += ". Its last output:\n\n```\n" + tail + "\n```"
        else:
            message += " and printed nothing"
        if summary:
            message += f"\n({summary})"
        return ModuleResult(
            outcome="failed",
            stdout=(res.stdout or "")[-20000:],
            error=message,
            exit_code=res.exit_code,
            duration_seconds=getattr(self, "_last_duration", None),
            claude_session_id=getattr(self, "_last_session_id", None),
        )

    async def execute(self, ctx: RunContext, prim: Primitives) -> ModuleResult:
        kind = ctx.kind
        if kind == "prd":
            scratch = gitwork.workspace_root() / "prd-scratch"
            scratch.mkdir(parents=True, exist_ok=True)
            res = await self._run_cli(
                prim, _prd_prompt(ctx), kind, str(scratch), ctx.timeout_seconds,
                ctx_settings=ctx.settings,
            )
            if res.exit_code != 0 or not res.stdout.strip():
                return self._failed(res, "no PRD was produced")
            return ModuleResult(
                outcome="succeeded", stdout=res.stdout[-20000:], prd=res.stdout.strip(),
                claude_session_id=getattr(self, "_last_session_id", None),
            )

        if kind == "breakdown":
            scratch = gitwork.workspace_root() / "breakdown-scratch"
            scratch.mkdir(parents=True, exist_ok=True)
            res = await self._run_cli(
                prim, _breakdown_prompt(ctx), kind, str(scratch),
                ctx.timeout_seconds, ctx_settings=ctx.settings,
            )
            stories = parse_stories(res.stdout)
            if not stories:
                return self._failed(res, "no parseable stories in the output")
            return ModuleResult(
                outcome="succeeded", stdout=res.stdout[-20000:], stories=stories,
                claude_session_id=getattr(self, "_last_session_id", None),
            )

        if kind == "release_prep":
            if not self.supports_mcp:
                return ModuleResult(
                    outcome="failed",
                    error=(
                        f"the {self.name} module cannot be given an MCP server "
                        "config, and release-prep work happens entirely "
                        "through the factory's MCP tools"
                    ),
                )
            scratch = gitwork.workspace_root() / "release-prep-scratch"
            scratch.mkdir(parents=True, exist_ok=True)
            token = os.environ.get("FACTORY_WORKER_TOKEN", "")
            mcp_path: str | None = None
            from .. import mcpconfig

            written = mcpconfig.write(
                scratch,
                os.environ.get("FACTORY_API_URL", ""),
                token,
                (ctx.context or {}).get("project_id"),
                ctx.tool_servers,
            )
            mcp_path = str(written) if written else None
            try:
                res = await self._run_cli(
                    prim, _release_prep_prompt(ctx), kind, str(scratch),
                    ctx.timeout_seconds, mcp_config=mcp_path,
                    ctx_settings=ctx.settings,
                )
            finally:
                if mcp_path:
                    mcpconfig.remove(scratch)
            if res.exit_code != 0 or not mcp_path:
                return self._failed(
                    res,
                    "" if mcp_path else "no MCP config could be written for this run",
                )
            return ModuleResult(
                outcome="succeeded", stdout=res.stdout[-20000:],
                claude_session_id=getattr(self, "_last_session_id", None),
            )

        # code / plan: work the branch on the factory remote
        if not ctx.git_remote_url or not ctx.branch_name:
            return ModuleResult(outcome="failed", error="context is missing git_remote_url or branch_name")

        # US-89.1: the remote stays clean — auth rides prepare_checkout's
        # credential helper, which reads FACTORY_WORKER_TOKEN from this
        # process's environment at fetch/push time. No token in any URL.
        remote = ctx.git_remote_url
        issue = str((ctx.context or {}).get("issue_id") or ctx.run_id)
        # US-31.8: one workspace per project, kept between runs.
        project_id = (ctx.context or {}).get("project_id")
        mcp_path: str | None = None
        # US-59.2: set once the checkout succeeds, so the exception handler
        # below can tell "nothing to preserve" from "something might be".
        workdir: Path | None = None
        try:
            _stage_start = time.monotonic()
            workdir = await gitwork.prepare_checkout(
                prim, remote, issue, project_id=project_id
            )
            # US-31.8: mark it used, so reclamation can tell a workspace that
            # is still earning its dependencies from an abandoned one.
            from .. import mcpconfig, workspace as _ws

            _ws.touch(workdir)
            await gitwork.checkout_branch(prim, workdir, ctx.branch_name, ctx.default_branch)
            out_dir = workdir / OUT_DIR
            if out_dir.exists():
                shutil.rmtree(out_dir, ignore_errors=True)
            out_dir.mkdir(parents=True, exist_ok=True)
            self._stage("checkout", round((time.monotonic() - _stage_start) * 1000))

            # US-31.9: hand the agent the factory's own MCP server, so it can
            # read context, study the repo, ask a question and report progress
            # while it works instead of living off one frozen prompt.
            if self.supports_mcp:
                written = mcpconfig.write(
                    workdir,
                    os.environ.get("FACTORY_API_URL", ""),
                    os.environ.get("FACTORY_WORKER_TOKEN", ""),
                    project_id,
                    # US-34.2: the granted servers ride in beside the factory's.
                    ctx.tool_servers,
                )
                mcp_path = str(written) if written else None

            _stage_start = time.monotonic()
            res = await self._run_cli(
                prim,
                _sections(
                    ctx,
                    kind,
                    mcp=bool(mcp_path),
                    shape=self.handback_shape_for(kind),
                ),
                kind,
                str(workdir),
                ctx.timeout_seconds,
                mcp_config=mcp_path,
                ctx_settings=ctx.settings,
                # US-59.3: only `code`/`plan` runs work a real checkout, and
                # only Claude Code modules honour this (RESUME_SUPPORTED) —
                # a resume id handed to any other run is simply not appended.
                resume_session_id=ctx.resume_session_id,
            )
            self._stage("invoke_cli", round((time.monotonic() - _stage_start) * 1000))
            if res.exit_code != 0:
                result = self._failed(res, "")
                if kind == "code":
                    # US-59.2: preserve whatever the agent wrote instead of
                    # letting the NEXT attempt's checkout discard it — a
                    # resumed session picks the conversation back up
                    # expecting these exact files to still be here. Reuses
                    # the same commit+push the success path uses, tagged
                    # `wip(...)` so it reads as unfinished, not as a landing.
                    # Best-effort: a preservation failure must not mask the
                    # real failure this result is already reporting.
                    try:
                        result.wip_preserved = await gitwork.commit_all_and_push(
                            prim, workdir, ctx.branch_name,
                            f"wip({ctx.run_id}): paused mid-task",
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "could not preserve WIP changes for run %s: %s",
                            ctx.run_id, e,
                        )
                return result

            if kind == "plan":
                _stage_start = time.monotonic()
                plan = _read_out(workdir, "plan.md")
                if not plan:
                    return self._failed(res, f"no {OUT_DIR}/plan.md was written")
                test_plan = _read_out(workdir, "test_plan.md")
                test_cases = _read_test_cases(workdir)
                self._stage("collect_output", round((time.monotonic() - _stage_start) * 1000))
                return ModuleResult(
                    outcome="succeeded", stdout=res.stdout[-20000:], plan=plan,
                    test_plan=test_plan,
                    test_cases=test_cases,
                    claude_session_id=getattr(self, "_last_session_id", None),
                )

            # us-96.8: a module whose agent hands code back over factory MCP
            # (submit_changeset) already has its commit on the branch — the
            # factory built it. Committing the tree here would land the same
            # change a second time and race the changeset push. Instead the
            # harness SWEEPS: any locally-modified file whose change never
            # reached the remote branch is named in a progress line, so a
            # partial submit is a fact on the run rather than a silent gap
            # in the PR (AC5).
            if kind in self.MCP_HANDBACK_KINDS:
                _stage_start = time.monotonic()
                shutil.rmtree(out_dir, ignore_errors=True)  # never committed
                leftovers: list[str] = []
                try:
                    leftovers = await gitwork.unsubmitted_paths(
                        prim, workdir, ctx.branch_name
                    )
                except Exception as e:  # noqa: BLE001 — the sweep never fails a run
                    logger.warning(
                        "unsubmitted-paths sweep failed for run %s: %s",
                        ctx.run_id, e,
                    )
                if leftovers and self._on_progress:
                    self._on_progress(
                        "progress",
                        "modified but never submitted: " + ", ".join(leftovers),
                    )
                self._stage(
                    "handback_sweep",
                    round((time.monotonic() - _stage_start) * 1000),
                )
                return ModuleResult(
                    outcome="succeeded", stdout=res.stdout[-20000:],
                    branch_ref=ctx.branch_name,
                    claude_session_id=getattr(self, "_last_session_id", None),
                )

            _stage_start = time.monotonic()
            test_cases = _read_test_cases(workdir)
            shutil.rmtree(out_dir, ignore_errors=True)  # never committed
            self._stage("collect_output", round((time.monotonic() - _stage_start) * 1000))
            title = (ctx.context or {}).get("title", "factory change")
            _stage_start = time.monotonic()
            await gitwork.commit_all_and_push(prim, workdir, ctx.branch_name, f"{title} (factory)")
            self._stage("commit_and_push", round((time.monotonic() - _stage_start) * 1000))
            return ModuleResult(
                outcome="succeeded", stdout=res.stdout[-20000:],
                branch_ref=ctx.branch_name, test_cases=test_cases,
                claude_session_id=getattr(self, "_last_session_id", None),
            )
        except Exception as e:  # noqa: BLE001 — report, never vanish
            result = ModuleResult(
                outcome="failed", error=f"module error: {e}",
                claude_session_id=getattr(self, "_last_session_id", None),
            )
            # US-59.2: same preservation attempt as the ordinary failure path
            # above, for a Python-level crash (a dropped connection mid-call,
            # for instance) rather than a CLI exit — only when the checkout
            # got far enough to have a workdir at all.
            if kind == "code" and workdir is not None:
                try:
                    result.wip_preserved = await gitwork.commit_all_and_push(
                        prim, workdir, ctx.branch_name,
                        f"wip({ctx.run_id}): paused mid-task",
                    )
                except Exception:  # noqa: BLE001 — the crash is already reported
                    logger.warning(
                        "could not preserve WIP changes after a crash on run %s",
                        ctx.run_id,
                    )
            return result
        finally:
            # US-31.9: the config carries the worker token, so it never
            # outlives the run — on success, on failure, and on a crash.
            if mcp_path:
                from .. import mcpconfig

                mcpconfig.remove(workdir)
