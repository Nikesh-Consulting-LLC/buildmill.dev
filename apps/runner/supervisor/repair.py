"""Self-repair loop (US-10.8).

When a module fails, classify the failure and attempt a bounded recovery before
retrying: transient → wait; broken checkout → wipe the workdir so the next run
clones fresh; dependency error → run the project's install command (or fall back
to a fresh clone); otherwise unrecoverable → stop and let the run submit its
error. Repairs run through the audited `run_shell` primitive (US-10.7). An
optional `diagnose` callback (the server brain, US-10.3) refines the message.
Every path ends with a returned ModuleResult — the run never vanishes.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import shutil

from . import gitwork
from .modules.base import ModuleResult, Primitives, RunContext

logger = logging.getLogger("supervisor.repair")

# US-54.2: needles that pick a DESTRUCTIVE repair must be failure phrasings,
# never words an agent uses while working. On 2026-07-30 a healthy code run
# ended on its turn cap, the old bare needles ("checkout", "branch", "clone")
# matched the agent's own narration ("the repo is already checked out…"), and
# the reclone "repair" deleted 41 turns of written code before giving up.
_RECLONE = (
    # gitwork's own failure phrasings (GitError: "git <op> failed: …")
    "git clone failed", "git fetch failed", "git checkout failed",
    "git reset failed", "git pull failed",
    # git's own corruption/state phrasings
    "index.lock", "cannot lock ref", "unrelated histories", "detached head",
    "not a git repository", "did not match any file", "loose object",
    "object file", "reference broken", "fatal: could not read",
)
_REINSTALL = (
    "no module named", "modulenotfound", "command not found",
    "cannot find module", "npm err", "importerror", "enoent",
    "is not recognized as",
)
_WAIT = (
    "timeout", "timed out", "killed after", "connection", "network",
    "temporarily", "reset by peer", "rate limit",
)

# us-117.1: an HTTP 5xx from an upstream — including the factory's own git
# remote. On 2026-08-17 a connection-pool bug made the API answer 500, so
# `git ls-remote` failed with "The requested URL returned error: 500". That
# matched nothing above (`_WAIT` names transport words; git's phrasing for a
# server error is a status code), fell through to the `unrecoverable` default,
# and the run was abandoned without a single retry — of the most transient
# class there is.
#
# Deliberately specific phrasings rather than a bare "500": the text searched
# includes the agent's own narration, where a loose three-digit needle would
# match "500 lines" and buy an unearned wait.
_HTTP_5XX = (
    "returned error: 500", "returned error: 502",
    "returned error: 503", "returned error: 504",
    "http 500", "http 502", "http 503", "http 504",
    "internal server error", "bad gateway",
    "service unavailable", "gateway timeout",
)

# Not in _RECLONE: a remote that will not answer is not a broken checkout, and
# recloning cannot fix it. Named so the failure classifies as itself rather
# than defaulting to unrecoverable.
_GIT_REMOTE = ("git ls-remote failed",)

# US-63.x: a "wait" failure — a connection/transient error, the class a
# factory-api restart during a deploy produces (the CLI's model calls go over
# HTTP to factory-api's own gateway, the same process being restarted) — gets
# its own time-boxed retry budget, separate from reclone/reinstall's
# count-boxed `max_attempts`. A deploy is usually back within a minute or two;
# this rides it out instead of failing a run that would have succeeded on the
# very next attempt. Past the ten-minute ceiling, the run is left for the
# server's own stale-heartbeat sweep to pause it for resume (it already does
# this whenever a claim's heartbeat goes stale and a claude_session_id
# exists — see 207_session_resume.sql / phase-59) rather than being reported
# as a hard failure here.
_WAIT_RETRY_SECONDS = 60
_WAIT_MAX_SECONDS = 600

# US-52.4: a Claude subscription's usage cap (weekly, or the 5-hour session
# ceiling). Checked BEFORE the keyword loops because the phrasing can brush
# against `_WAIT`'s "rate limit" — and a capped run is the one failure a
# retry can never fix: the same prompt cannot succeed before the cap resets,
# and the factory never falls back to metered billing on its own (us-52.1).
_USAGE_CAP = (
    "usage limit reached",
    "claude ai usage limit",
    "you've reached your usage limit",
    "hit your usage limit",
)


# US-54.1: the turn budget ran out. Terminal by definition — re-running the
# same prompt under the same cap ends the same way, and the one repair that
# was ever tried for it (reclone) destroys the work it produced. The needle is
# the message cli_base._failed writes from the CLI's own result subtype.
_TURN_LIMIT = ("hit its turn ceiling", "error_max_turns")


def usage_cap_hit(result: ModuleResult) -> bool:
    text = f"{result.error or ''}\n{result.stdout or ''}".lower()
    return any(n in text for n in _USAGE_CAP)


def http_5xx_hit(result: ModuleResult) -> bool:
    """A server-side error from an upstream — transient by definition."""
    text = f"{result.error or ''}\n{result.stdout or ''}".lower()
    return any(n in text for n in _HTTP_5XX) or any(
        n in text for n in _GIT_REMOTE
    )


def turn_limit_hit(result: ModuleResult) -> bool:
    text = f"{result.error or ''}\n{result.stdout or ''}".lower()
    return any(n in text for n in _TURN_LIMIT)


def classify(result: ModuleResult) -> str:
    """Map a failed result to a recovery action."""
    # us-96.9: a manager stop is a decision, not a malfunction — checked
    # before any keyword loop, because the words "the session was
    # cancelled" match the transient list and on 2026-08-14 that bought a
    # 60-second wait, a reclone that invalidated the workspace's source
    # record, and a zombie boot on a dead claim. Terminal: no wait, no
    # reinstall, no reclone, no retry.
    if getattr(result, "stopped", False):
        return "unrecoverable"
    if usage_cap_hit(result) or turn_limit_hit(result):
        return "unrecoverable"
    # us-117.1: BEFORE the _RECLONE scan, not after. A server-side 5xx is never
    # fixed by deleting the workspace, and a message like "git fetch failed: …
    # returned error: 502" contains a _RECLONE needle — so checked later, the
    # destructive repair would win. US-54.2 is on record about what that costs:
    # on 2026-07-30 a reclone fired on the wrong needle and deleted 41 turns of
    # written code before giving up.
    if http_5xx_hit(result):
        return "wait"
    text = f"{result.error or ''}\n{result.stdout or ''}".lower()
    for kw in _RECLONE:
        if kw in text:
            return "reclone"
    for kw in _REINSTALL:
        if kw in text:
            return "reinstall"
    for kw in _WAIT:
        if kw in text:
            return "wait"
    return "unrecoverable"


def classify_fault(result: ModuleResult) -> str:
    """Tag a failure work-fault vs runner-fault (US-10.11). Environmental signals
    (bad checkout, missing dep/CLI, transient) are runner-faults — fix the box;
    everything else defaults to work-fault — fix the story."""
    # us-96.9: a stop is neither fault — the box did not break and the
    # story did not fail. The payload builder omits fault_class entirely
    # for stopped results; this guard only keeps the keyword logic out if
    # anything else ever asks.
    if getattr(result, "stopped", False):
        return "work-fault"
    # US-52.4: a capped subscription is an agent-side resource condition, not
    # a defect in the story — the same work succeeds after the reset.
    if usage_cap_hit(result):
        return "runner-fault"
    return "runner-fault" if classify(result) in ("reclone", "reinstall", "wait") else "work-fault"


# ---------------------------------------------------------------------------
# US-27.12: the evidence outranks the diagnosis
# ---------------------------------------------------------------------------

# Failures that are recognisable from the output. These get a fixed, accurate
# message pointing at the setting that causes them — no inference involved,
# because inference is what produced "the CLI timed out because the command
# exceeded the default execution limit" about a command that exited in eight
# seconds naming its own problem.
_NAMED_FAILURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("it may not exist or you may not have access",
         "model not found", "unknown model", "does not exist or you do not have access"),
        "the provider rejected the model this agent is routed to. This is a "
        "configuration problem, not a code problem: check the agent's model "
        "route on its runner console, and that the provider offering that "
        "model is the one answering (US-27.8)",
    ),
    (
        ("invalid api key", "unauthorized", "401", "authentication_error",
         "incorrect api key"),
        "the provider rejected the credential. Check the provider's key in "
        "Settings → LLM providers; the agent machine holds no model key of "
        "its own, so this is a server-side setting",
    ),
    (
        ("command not found", "no such file or directory", "is not recognized as"),
        "the agent CLI is not installed on this machine. Install it, or run "
        "Update from the host's page",
    ),
    # US-54.1: the turn budget, in step with _TURN_LIMIT above.
    (
        ("hit its turn ceiling", "error_max_turns"),
        "the run used every turn its settings allow and the session was ended "
        "mid-work. This is a budget problem, not a code problem: raise "
        "max_turns on the preset this agent's route uses (the agent's settings "
        "page names it), or split the story into smaller items. The run was "
        "not retried — the same work under the same cap ends the same way",
    ),
    # US-52.4: the subscription cap, in the CLI's own words. Kept in step with
    # _USAGE_CAP above — the same needles must name and refuse to retry.
    (
        (
            "usage limit reached",
            "claude ai usage limit",
            "you've reached your usage limit",
            "hit your usage limit",
        ),
        "the Claude subscription this agent bills has hit its usage cap. Caps "
        "reset on a fixed schedule (the CLI's message above names it when it "
        "can); the run was not retried because the same prompt cannot succeed "
        "before the reset, and the factory never falls back to metered API "
        "billing on its own. Dispatch again after the reset, or switch the "
        "agent's Claude billing to Claude Code — API in its settings",
    ),
)


def named_failure(result: ModuleResult) -> str | None:
    """A fixed, accurate reading of a failure that is recognisable by pattern.

    Returns None when the output is genuinely ambiguous — which is where a
    brain diagnosis earns its place."""
    text = f"{result.error or ''}\n{result.stdout or ''}".lower()
    for needles, message in _NAMED_FAILURES:
        if any(n in text for n in needles):
            return message
    return None


def diagnosis_contradicts(result: ModuleResult, note: str) -> str | None:
    """Why this diagnosis cannot be true given the command's own output.

    Cheap heuristics for the common inventions. A model's opinion is useful;
    it must never survive contact with evidence that refutes it."""
    claim = (note or "").lower()
    if not claim:
        return None
    output = f"{result.error or ''}\n{result.stdout or ''}".lower()
    duration = result.duration_seconds
    exited = result.exit_code

    if ("timed out" in claim or "timeout" in claim) and exited not in (None, 0):
        # A process that returned a status did not hit a wall clock. The
        # 2026-07-26 case: exit in 8s, two repair attempts spent on a timeout.
        if duration is not None and duration < 120:
            return (
                f"the command exited {exited} after {duration:.0f}s, so it did "
                "not time out"
            )
        if duration is None:
            return f"the command exited {exited} rather than being killed"
    if ("network" in claim or "connection" in claim or "offline" in claim) and not any(
        n in output
        for n in ("connection", "network", "resolve", "unreachable", "timed out", "dns")
    ):
        return "nothing in the command's output mentions the network"
    if ("permission" in claim or "denied" in claim or "sudo" in claim) and not any(
        n in output for n in ("permission", "denied", "eacces", "not permitted")
    ):
        return "nothing in the command's output mentions permissions"
    return None


def _workdir(ctx: RunContext):
    # US-31.8: the workspace is the project's, not the item's.
    from . import workspace

    return workspace.workspace_for(
        (ctx.context or {}).get("project_id"), ctx.run_id
    )


async def apply_repair(action: str, ctx: RunContext, prim: Primitives) -> str | None:
    """Do the repair. Returns what it changed, or None if it changed nothing.

    US-27.12: "nothing" is the important answer. Both attempts on 2026-07-26
    ran the identical `claude -p …` against the identical environment and got
    the identical rejection — two identical failures are not resilience, they
    are latency, and the caller uses this to stop rather than repeat itself."""
    if action == "wait":
        await asyncio.sleep(_WAIT_RETRY_SECONDS)
        # Waiting changes nothing about the command OR the environment — the
        # caller (execute_with_repair) is what decides how many times that is
        # worth doing, via the time budget above rather than a single attempt.
        return f"waited {_WAIT_RETRY_SECONDS}s before retrying"
    workdir = _workdir(ctx)
    from . import workspace

    if action == "reclone":
        # US-31.8: two tiers, cheapest first. Invalidating the source record
        # makes the next run refetch the tree and KEEP the dependencies; only
        # if that has already been tried does the folder go. Against a
        # persistent workspace, deleting first would throw away minutes of
        # install for what is usually a stale tree.
        changed = workspace.invalidate(workdir)
        if changed:
            return changed
        return workspace.wipe(workdir)
    if action == "reinstall":
        install = ((ctx.context or {}).get("build_config") or {}).get("install_command")
        if install and workdir.exists():
            await prim.run_shell(shlex.split(install), cwd=str(workdir), timeout=600)
            return f"ran the project's install command ({install})"
        changed = workspace.invalidate(workdir)
        if changed:
            return changed
        return workspace.wipe(workdir)
    return None


async def execute_with_repair(
    module,
    ctx: RunContext,
    prim: Primitives,
    *,
    max_attempts: int = 2,
    diagnose=None,
    on_attempt=None,
    preflight=None,
) -> ModuleResult:
    """Run the module, repairing and retrying up to `max_attempts` times.

    US-27.12: a repair that changes neither the command nor the environment is
    not attempted a second time, and the failure is reported with the evidence
    first — the command's own last output — with any brain-authored reading
    after it and labelled as one.

    US-63.x: a "wait" (connection/transient) failure is the one exception to
    `max_attempts` — it gets its own time budget (`_WAIT_RETRY_SECONDS` apart,
    up to `_WAIT_MAX_SECONDS` total) instead of sharing the count-boxed budget
    reclone/reinstall use, since the failure it's built for (a factory-api
    restart) resolves on its own on a clock, not after N tries.

    `on_attempt(n, action, changed, outcome)` receives each repair attempt so
    the caller can put it in the run trace. `n` is an int for reclone/
    reinstall/unrecoverable attempts, and a `"wait (Ns)"` string for wait
    attempts — on_attempt only ever interpolates it into a message.
    """
    # us-96.9 AC4: never boot a CLI on a claim that is no longer ours —
    # first attempt or retry. `preflight` raises to abort (the caller
    # owns what an abort means); a live claim returns quietly.
    if preflight is not None:
        await preflight()
    result = await module.execute(ctx, prim)
    attempts = 0
    wait_seconds = 0
    unchanged: str | None = None
    gave_up_waiting = False
    while result.outcome != "succeeded":
        # US-37.2: there is no spend-ceiling branch here any more. The gateway
        # no longer refuses a call for money, so nothing can produce the 402
        # this used to recognise — a classifier for an impossible error reads as
        # live protection while protecting nothing. Money is bounded per project
        # now, before the run is created.
        action = classify(result)
        if action == "wait":
            # Time-boxed, not count-boxed (US-63.x) — see the constants above.
            if wait_seconds >= _WAIT_MAX_SECONDS:
                gave_up_waiting = True
                if on_attempt:
                    on_attempt(
                        f"wait ({wait_seconds}s)", action, None,
                        "gave up — leaving the claim for the server to reclaim",
                    )
                break
            logger.info(
                "repair: wait (%ds elapsed of %ds budget)", wait_seconds, _WAIT_MAX_SECONDS
            )
            changed = await apply_repair(action, ctx, prim)
            wait_seconds += _WAIT_RETRY_SECONDS
            if preflight is not None:
                await preflight()
            result = await module.execute(ctx, prim)
            if on_attempt:
                on_attempt(f"wait ({wait_seconds}s)", action, changed, result.outcome)
            continue
        if attempts >= max_attempts:
            break
        attempts += 1
        logger.info("repair attempt %d: action=%s", attempts, action)
        if action == "unrecoverable":
            if on_attempt:
                on_attempt(attempts, action, None, "not attempted")
            break
        changed = await apply_repair(action, ctx, prim)
        if not changed:
            # Re-running an identical command against an identical environment
            # produces an identical failure. Say so instead of spending the
            # attempt and the wall clock on it.
            unchanged = action
            if on_attempt:
                on_attempt(attempts, action, None, "skipped — nothing to change")
            break
        if preflight is not None:
            await preflight()
        result = await module.execute(ctx, prim)
        if on_attempt:
            on_attempt(attempts, action, changed, result.outcome)

    if result.outcome != "succeeded":
        # 1. The evidence. `result.error` already leads with the exit code and
        #    the command's own output (US-27.12, cli_base._failed).
        parts = [result.error or "module failed"]

        # 2. A named cause, when the output makes one unambiguous. No
        #    inference is involved and none is wanted.
        named = named_failure(result)
        if named:
            parts.append(f"Cause: {named}.")

        if unchanged:
            parts.append(
                f"Repair had nothing to change (the {unchanged} repair would "
                "have re-run the same command against the same environment), "
                "so the run failed on the first error rather than repeating it."
            )
        elif gave_up_waiting:
            parts.append(
                f"Retried every {_WAIT_RETRY_SECONDS}s for {wait_seconds}s waiting "
                "for a connection/transient failure to clear (the class a "
                "factory-api restart during a deploy produces) and it did not. "
                "If the factory is reachable again by the time this is reported, "
                "it lands as this failure; if the factory is still down, the "
                "hand-back itself cannot land and the claim is left for the "
                "server's own stale-heartbeat sweep to pause for resume once it "
                "recovers, rather than losing the work done so far."
            )
        elif attempts:
            parts.append(f"Repair exhausted after {attempts} attempt(s).")

        # 3. The brain's reading, last, labelled, and dropped when the output
        #    contradicts it.
        note = None
        if diagnose is not None and not named:
            try:
                note = await diagnose(result)
            except Exception:  # noqa: BLE001 — diagnosis is best-effort
                note = None
        if note:
            conflict = diagnosis_contradicts(result, note)
            if conflict:
                logger.info("dropped diagnosis (%s): %s", conflict, note)
                parts.append(
                    f"(A diagnosis was discarded because {conflict}.)"
                )
            else:
                parts.append(f"Diagnosis (runner brain, unverified): {note}")
        result.error = "\n\n".join(p for p in parts if p)
    return result
