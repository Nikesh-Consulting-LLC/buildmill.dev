"""Factory MCP server (US-3.3) — the worker loop from inside any
MCP-capable tool.

Thin tools over the worker endpoints and direct reads, grouped by the
loop they serve: the pool (list_available_work, claim_work,
get_instructions), the claimed run (get_work_context, report_progress,
request_clarification / get_clarifications, add_comment, release_work),
the hand-back (submit_plan, submit_code_work, submit_prd) and its
follow-up (get_run_status, list_my_work), and standing project context
(get_project_guidelines, get_project_learnings / submit_learning,
list_project_documents / get_document). Every tool carries standard MCP
annotations (US-5.10): a title plus readOnlyHint on the pure reads so
clients can prefetch and poll freely, and accurate non-destructive,
non-idempotent hints on the mutating ones. Authentication reuses the
worker-token verification via the X-Worker-Token header — same
identities, same revocation, no second auth system. Errors come back as
actionable tool results, never stack traces.
"""

import asyncio
import contextvars
import json
import logging
from typing import Any

from fastapi import HTTPException
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from . import (
    build_config,
    changesets,
    db,
    deploy,
    documents,
    github,
    github_tokens,
    llm,
    project_env,
    release_prep,
    repo_browse,
    validation,
    wireframe_docs,
    wireframes,
)
from .config import get_settings

logger = logging.getLogger("uvicorn.error")

_current_worker: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "factory_mcp_worker", default=None
)
# US-3.14: the project id a project-scoped MCP url resolves to (or None for
# an org-wide / bare url). Set by the auth wrapper, read by the pool tools.
_scoped_project: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "factory_mcp_scoped_project", default=None
)


# US-5.10: standard MCP annotations on every tool, so clients can call
# the safe reads speculatively (prefetch context, poll status) and treat
# the mutating tools with care. The title doubles as the top-level tool
# title for clients that prefer it over annotations.title.
def _read(title: str) -> dict[str, Any]:
    """Annotation bundle for a pure read — safe to call repeatedly."""
    return {
        "title": title,
        "annotations": ToolAnnotations(
            title=title, readOnlyHint=True, openWorldHint=False
        ),
    }


def _write(title: str) -> dict[str, Any]:
    """Annotation bundle for a mutating tool: not read-only, nothing
    destructive, and not idempotent — claims race, submissions and
    heartbeats accumulate with every call."""
    return {
        "title": title,
        "annotations": ToolAnnotations(
            title=title,
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    }


mcp = FastMCP(
    "software-factory",
    # US-5.10: the final phase-5 instructions — the loop below names every
    # stage a worker touches; keep it in step with the tool surface.
    instructions=(
        "The Build Mill work pool. Typical loop: list_available_work "
        "(retries are flagged 'retry of run …'; list_factory_queue shows "
        "the whole pipeline — queued, running, held and paused — in the "
        "manager's order, so you can reason about your item as part of a "
        "sequence, but only `queued` items are claimable) → peek "
        "get_instructions on anything interesting before committing → "
        "claim_work → "
        "get_work_context — story, acceptance criteria, the manager's "
        "test cases, plan, run commands, linked documents, and the "
        "discussion so far — → study the repository with get_repo_tree "
        "and read_repo_file (plan runs especially) → do the work → hand "
        "back with submit_plan, submit_prd, or (code runs) one of two "
        "transports → get_run_status afterward to see the outcome: "
        "approval, the merge, or the manager's rejection feedback and "
        "the retry run to re-claim.\n\n"
        "Code hand-back, two transports into one review pipeline. "
        "MCP-only (no git tooling needed): get_workspace downloads the "
        "tree as a zip pinned to base_sha; work on it locally; "
        "submit_changeset hands back the changed files and the factory "
        "builds the commit and pushes the branch. What happens next follows "
        "get_work_context's submit_mode: `pr` opens a PR for review; "
        "`direct` (the project's main strategy) commits to the default "
        "branch with no PR. The commit is authored as you, and a stale "
        "base answers the current head instead of overwriting. Reference "
        "your work item by its readable id (get_work_context's "
        "work_item_id, e.g. US-1.4.1) in commits and PR titles; a code "
        "run also carries the project's build_config values to write into "
        "a .env before running the verify commands. Git-native (humans in IDEs, repos "
        "above the snapshot ceiling): clone the factory git remote from "
        "get_work_context (this worker token is the Basic-auth "
        "password), push the named branch, then submit_code_work. "
        "Either way: validate_submission dry-runs the gate's structural "
        "checks first, report_test_results records pass/fail against "
        "the manager's test cases (passes lift the unrun merge-override "
        "warning), and get_pr_status shows the GitHub side afterward — "
        "checks, mergeability, unresolved review comments.\n\n"
        "No claim needed to look at code: get_project_tree, "
        "read_project_file and get_project_workspace are the same reads "
        "as get_repo_tree/read_repo_file/get_workspace but keyed by "
        "project_id instead of a held run_id — for exploring a project, "
        "testing that you can check code out, or working outside the "
        "claim/work-item flow entirely. They default project_id to a "
        "project-scoped MCP url and ref to the project's default branch. "
        "Available whenever this worker has project access, unless a "
        "manager has turned it off for this worker specifically. Purely "
        "read-only — there is nothing to submit_changeset back to; "
        "claim_work a run first to hand code back.\n\n"
        "During long work, heartbeat with report_progress — it extends "
        "your lease and can carry a note the manager sees — so a "
        "slow-but-healthy run isn't re-pooled at lease expiry. When a "
        "story is ambiguous, request_clarification asks the manager "
        "mid-run (the answer lands on the work item's instruction set; "
        "poll get_clarifications) instead of guessing — and when the "
        "ambiguity has two or three concrete resolutions, pass them as "
        "`options` so the manager clicks instead of writing prose, one "
        "decision per question; add_comment posts "
        "to the item's shared thread. release_work hands a claim back; "
        "list_my_work recovers your claims and unsettled submissions "
        "after a restart. Call get_project_guidelines and "
        "get_project_learnings any time — before claiming, mid-run, or "
        "standalone — to read a project's conventions (the same markdown "
        "committed to AGENTS.md) and the gotchas earlier runs left "
        "behind; list_project_documents and get_document open the "
        "project's design material; when you discover something the hard "
        "way, submit_learning queues it for the manager's review — once "
        "approved it reaches every future run's context; and when the "
        "guidelines themselves are wrong or stale, "
        "recommend_guideline_change proposes a fix graded by severity "
        "(trivial/minor/major/severe) — the manager decides, nothing "
        "auto-applies.\n\n"
        "Scope: this server is deliberately narrow — the worker loop "
        "plus the context to work well, not a general project browser. "
        "It covers your own claims and submissions, any org run's "
        "status and PR state by id, repository reads (claim-holder or, "
        "for a project you have access to, with no claim at all), the "
        "work item's instructions, comments and clarifications, and the "
        "project's guidelines, learnings, and text documents. It does "
        "not expose: MCP resources or prompts (none are registered — "
        "tools only); other workers' activity or claims (list_my_work "
        "is yours alone; get_run_status needs a run id already in "
        "hand); the full backlog or run history (list_available_work "
        "returns only runs currently queued and claimable, and "
        "list_factory_queue only what is queued or running now, not "
        "finished history); workspace "
        "settings; or arbitrary story and document search (there is no "
        "search tool). Those views live in the Build Mill web app, not "
        "here."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    # the worker token is the gate; a Host allowlist would break every
    # deployment domain (and the factory serves no browser content here)
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    ),
)


def _worker() -> dict[str, Any]:
    worker = _current_worker.get()
    if not worker:  # unreachable behind the auth wrapper
        raise RuntimeError("no authenticated worker in context")
    return worker


def _err(message: str, hint: str = "") -> dict[str, Any]:
    out = {"error": message}
    if hint:
        out["hint"] = hint
    return out


def _parse_command_lines(text: str | None) -> list[str]:
    """US-5.23: the us-5.9 run-commands section as an ordered command
    list — bullets and backticks stripped, blank lines dropped."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*").strip().strip("`").strip()
        if line:
            out.append(line)
    return out


def _github_err(e: github.GitHubError) -> dict[str, Any]:
    """US-5.24 taxonomy → tool result: credential/config failures name the
    manager as the fix owner; everything else is worker-fixable."""
    if isinstance(e, github.GitHubPermissionError):
        return _err(
            e.message,
            "only a manager can fix this — ask them to grant the missing "
            "permission on the GitHub App (or reconnect in "
            "Settings → GitHub)",
        )
    if isinstance(e, (github.GitHubNotConfigured, github.GitHubCredentialError)):
        return _err(
            e.message,
            "only a manager can fix this — ask them to check "
            "Settings → GitHub",
        )
    return _err(e.message, "check the path/ref and retry")


def _next(out: dict[str, Any], *steps: tuple[str, str]) -> dict[str, Any]:
    """US-5.30: the loop is self-describing — every lifecycle response
    carries a structured `next` list plus its human-readable mirror
    appended to the markdown. Guidance, not a state machine: tools keep
    accepting valid calls in any order. Error responses use `hint`
    instead — never both."""
    out["next"] = [{"tool": t, "reason": r} for t, r in steps]
    if steps:
        sentence = "Next: " + ", then ".join(f"{t} — {r}" for t, r in steps)
        out["markdown"] = (
            (out.get("markdown") or "").rstrip() + "\n\n" + sentence + "."
        )
    return out


def _code_submit_steps(
    settings, run_id: str, worker_id: str
) -> list[tuple[str, str]]:
    """US-5.30: the two steps after a code submit, with the unreported
    test-case count called out — the step that lifts the unrun-tests
    merge gate is the one agents historically skipped."""
    try:
        unreported = db.count_unreported_test_cases(
            settings, run_id, worker_id
        )
    except Exception:
        unreported = 0
    reason = (
        "record pass/fail against the manager's test cases — "
        "agent-verified passes lift the unrun-tests merge gate"
    )
    if unreported > 0:
        reason = (
            f"{unreported} test case(s) have no reported result yet; "
            + reason
        )
    return [
        ("report_test_results", reason),
        (
            "get_pr_status",
            "the GitHub side: checks, mergeability, review comments",
        ),
    ]


def _test_cases_md(ic: dict[str, Any]) -> str:
    """US-13.5: the issue's test cases as a compact id + title list — the
    brief carries what report_test_results needs; steps and expected
    results are one get_context_detail call away. Empty string when the
    issue has none or the run predates the bundling."""
    cases = ic.get("test_cases") or []
    if not cases:
        return ""
    md = (
        "\n\n## Test cases\n\n"
        "The manager verifies these in UAT — make each one pass, and "
        "automate them as tests where practical. Record only outcomes you "
        "observed with report_test_results(run_id, results) addressed to "
        "the test_case_id. Full steps and expected results: "
        'get_context_detail(run_id, "test_cases").\n'
    )
    for case in cases:
        md += (
            f"\n- `{case.get('id') or 'no-id'}` — "
            f"{case.get('title') or 'Untitled test case'}"
        )
    return md


def _test_cases_full_md(cases: list[dict[str, Any]]) -> str:
    """The pre-us-13.5 full rendering — title, steps, expected result —
    now served on demand by get_context_detail."""
    md = "## Test cases (full)"
    for case in cases:
        md += f"\n\n### {case.get('title') or 'Untitled test case'}"
        if case.get("id"):
            md += f"\n\n`test_case_id: {case['id']}`"
        if (case.get("steps") or "").strip():
            md += f"\n\nSteps:\n\n{case['steps']}"
        if (case.get("expected_result") or "").strip():
            md += f"\n\nExpected result:\n\n{case['expected_result']}"
    return md


def _omitted_manifest(
    entries: list[tuple[str, str]],
) -> tuple[list[dict[str, str]], str]:
    """US-13.5: the brief names what it left out and exactly how to get
    it — an agent must never have to guess that a plan or PRD exists."""
    if not entries:
        return [], ""
    items = [{"section": s, "how": h} for s, h in entries]
    md = "\n\n## Not inlined — pull on demand\n\n" + "\n".join(
        f"- **{s}** — {h}" for s, h in entries
    )
    return items, md


@mcp.tool(**_read("List available work"))
async def list_available_work() -> dict[str, Any]:
    """List the org's claimable factory work — plan and code runs waiting
    in the pool, first-come-first-served."""
    settings = get_settings()
    worker = _worker()
    runs = db.list_worker_pool(settings, worker, project_id=_scoped_project.get())
    items = [
        {
            "run_id": str(r["id"]),
            "kind": r["kind"],
            "issue_id": str(r["issue_id"]),
            "issue_title": r["issue_title"],
            "issue_type": r["issue_type"],
            "project": r["project_name"],
            "repo": r["repo_full_name"],
            # US-5.5: a retry names the run it follows, so an agent whose
            # submission was rejected can spot (and re-claim) its own
            # follow-up instead of scanning titles.
            "retry_of_run_id": (
                str(r["retry_of_run_id"]) if r.get("retry_of_run_id") else None
            ),
        }
        for r in runs
    ]
    if items:
        md = (
            "Claimable factory work:\n"
            + "\n".join(
                f"- **{i['issue_title']}** — {i['kind']} run `{i['run_id']}` "
                f"({i['project']}, {i['repo']})"
                + (
                    f" — retry of run `{i['retry_of_run_id']}`"
                    if i["retry_of_run_id"]
                    else ""
                )
                for i in items
            )
            + "\n\nget_instructions shows what each item expects before "
            "you claim."
        )
    else:
        md = "No claimable work in the pool right now."
    return {"markdown": md, "runs": items}


# US-15.2: plain-words state for each run in the ordered factory queue.
_QUEUE_STATE_LABELS = {
    "running": "running",
    "paused": "paused by the manager",
    "held": "held — waiting on sibling stories to be approved",
    "queued": "queued",
}


@mcp.tool(**_read("List the factory queue"))
async def list_factory_queue() -> dict[str, Any]:
    """The whole factory queue in the manager's execution order — every queued
    and running run, not just what you can claim next. Use it to understand the
    pipeline you are part of: what is lined up, in what order, and what is held
    or paused. Only runs whose state is `queued` are claimable; `held`,
    `paused` and `running` runs are shown for context and must not be claimed.
    Claim the next available item with claim_work (it already returns work in
    this order)."""
    settings = get_settings()
    worker = _worker()
    rows = db.list_factory_queue(
        settings, str(worker["org_id"]), project_id=_scoped_project.get()
    )
    items = []
    for r in rows:
        if r["status"] == "running":
            state = "running"
        elif r.get("paused_at") is not None:
            state = "paused"
        elif r.get("hold_reason"):
            state = "held"
        else:
            state = "queued"
        items.append(
            {
                "run_id": str(r["id"]),
                "kind": r["kind"],
                "state": state,
                "claimable": state == "queued",
                "issue_id": str(r["issue_id"]) if r.get("issue_id") else None,
                "issue_title": r.get("issue_title"),
                "display_id": db.work_item_display_id(
                    r.get("issue_type"),
                    r.get("epic_number"),
                    r.get("item_no"),
                    r.get("sub_no"),
                ),
                "epic": (
                    f"Epic {r['epic_number']} · {r.get('epic_title') or ''}".strip()
                    if r.get("epic_number") is not None
                    else None
                ),
                "project": r["project_name"],
                "hold_reason": r.get("hold_reason"),
                "holder": r.get("worker_name") if state == "running" else None,
            }
        )
    if items:
        lines = []
        for i in items:
            head = f"- {i['display_id'] + ' — ' if i['display_id'] else ''}" \
                f"**{i['issue_title'] or i['kind']}** ({i['project']}) · " \
                f"{i['kind']} run · {_QUEUE_STATE_LABELS[i['state']]}"
            if i["state"] == "running" and i["holder"]:
                head += f" · held by {i['holder']}"
            if i["state"] == "held" and i["hold_reason"]:
                head += f" — {i['hold_reason']}"
            lines.append(head)
        md = (
            "The factory queue, in the order the manager set (top = next):\n"
            + "\n".join(lines)
            + "\n\nOnly `queued` items are claimable. Claim the next with "
            "claim_work."
        )
    else:
        md = "The factory queue is empty — nothing queued or running."
    return {"markdown": md, "queue": items}


# US-5.1: friendly labels for the unsettled-submission states.
_SUBMITTED_STATUS_LABELS = {
    "prd-review": "PRD in review",
    "plan-review": "plan in review",
    "in-review": "in review",
    "needs-fixes": "rejected — needs fixes",
}


@mcp.tool(**_read("List my work"))
async def list_my_work() -> dict[str, Any]:
    """The runs you currently hold (with lease expiry), plus your recent
    submissions whose outcome isn't settled yet — the recovery view after
    a session restart. Resume claimed work with get_work_context."""
    settings = get_settings()
    worker = _worker()
    work = db.list_worker_runs(
        settings, worker, project_id=_scoped_project.get()
    )
    claimed = [
        {
            "run_id": str(r["id"]),
            "kind": r["kind"],
            "issue_title": r["issue_title"],
            "project": r["project_name"],
            "claim_expires_at": str(r["claim_expires_at"]),
        }
        for r in work["claimed"]
    ]
    submitted = [
        {
            "run_id": str(r["id"]),
            "kind": r["kind"],
            "issue_title": r["issue_title"],
            "project": r["project_name"],
            "status": r["issue_status"],
        }
        for r in work["submitted"]
    ]
    if claimed:
        md = (
            "Runs you hold:\n"
            + "\n".join(
                f"- **{c['issue_title']}** — {c['kind']} run `{c['run_id']}` "
                f"({c['project']}), lease expires {c['claim_expires_at']}"
                for c in claimed
            )
            + "\n\nResume with get_work_context."
        )
    else:
        md = "You hold no claimed work."
    if submitted:
        md += "\n\nRecent submissions awaiting an outcome:\n" + "\n".join(
            f"- **{s['issue_title']}** — {s['kind']} run `{s['run_id']}` "
            f"({s['project']}) — "
            f"{_SUBMITTED_STATUS_LABELS.get(s['status'], s['status'])}"
            for s in submitted
        )
    else:
        md += "\n\nNo recent submissions."
    return {"markdown": md, "claimed": claimed, "submitted": submitted}


@mcp.tool(**_read("Get work item instructions"))
async def get_instructions(run_id: str) -> dict[str, Any]:
    """A work item's current instruction set — the manager's living work
    plan — for any queued or claimed run in your org. No claim required:
    peek before claiming, and re-read mid-run for the latest direction."""
    settings = get_settings()
    worker = _worker()
    row = db.get_run_instructions(settings, run_id, str(worker["org_id"]))
    if not row:
        return _err("run not found", "list_available_work shows valid run ids")
    instructions = row.get("instruction_set")
    if not (instructions or "").strip():
        # Pre-dispatch items are always seeded; an empty set means the
        # manager cleared it deliberately.
        md = (
            f"# {row['issue_title']}\n\nNo instruction set on this work "
            "item yet."
        )
    else:
        md = f"# {row['issue_title']} — instruction set\n\n{instructions}"
    # US-13.3: the hand-back notes channel is part of the contract — say so
    # where every worker reads before working.
    if row.get("kind") == "code":
        # US-27.1: the story-by-story protocol, stated where an agent reads
        # before it claims. The docstring used to promise iteration that
        # submit_changeset could not deliver — the first commit closed the run.
        members = db.run_members(settings, str(row["id"]))
        if len(members) > 1:
            md += (
                f"\n\n---\n\n**This run covers {len(members)} stories.** "
                "Commit each one as you finish it — `submit_changeset(..., "
                'issue_ids=["' + members[0]["display_id"] + '"], '
                "final=false)`, declaring the sha the previous call returned "
                "as the next `base_sha` — and close the run with a single "
                "call carrying `final=true`. Only stories with a landed "
                "commit are moved to review, so nothing you commit is lost "
                "if you die halfway, and nothing you did not commit is "
                "reported as done.\n\n"
                # US-40.2: the same requirement stated for the other path,
                # because an agent that pushes a branch used to satisfy none
                # of the above and have its whole batch returned to the pool.
                "**If you hand back by pushing a branch instead**, every "
                "commit must carry a `Factory-Story: <id>` trailer naming the "
                "story it implements — that trailer is the only thing that "
                "tells the factory which story your commit landed, and a "
                "branch carrying none of them is refused.\n\n"
                + "\n".join(
                    f"{m['position']}. `{m['display_id']}` — {m['title']}"
                    for m in members
                )
            )
    # US-27.2: run 11c564b0 worked for ~30 minutes on a 15-minute lease and
    # never called report_progress, so its claim lapsed while it was still
    # working and the run sat claimable for 14 minutes. The heartbeat works;
    # nothing told the agent it had to use it.
    lease = db.lease_for_worker_type(str(worker.get("type") or "autonomous"))
    md += (
        f"\n\n---\n\n**Your claim expires.** The lease on this run is "
        f"**{lease}** from your last call. `report_progress` extends it (so "
        "does any tool that touches the run), and an expired claim goes back "
        "to the pool where another agent can take it — while you are still "
        "working on it. On a job you expect to run past the lease, call "
        "`report_progress` every few minutes with a one-line account of "
        "where you are. It is not optional bookkeeping: it is what keeps the "
        "work yours."
    )
    md += (
        "\n\n---\n\n**Hand-back notes:** every submit_* tool takes notes for "
        "the manager (`notes_for_manager`; `notes` on the code tools). "
        "Anything you flag rides the submission itself onto the review "
        "surface and the item's thread — flagging concerns is part of "
        "finishing the work, not optional politeness, and no denied "
        "side-channel tool can silence it."
    )
    return {
        "markdown": md,
        "run_id": str(row["id"]),
        "issue_id": str(row["issue_id"]),
        "kind": row["kind"],
        "instruction_set": instructions,
    }


@mcp.tool(**_write("Comment on work item"))
async def add_comment(run_id: str, body: str) -> dict[str, Any]:
    """Post a comment on your claimed run's work item — the shared thread
    org members and agents both read and write. Posting extends your
    lease like a heartbeat. For a blocking question, still release or
    keep working; comments never pause the run."""
    from fastapi import HTTPException

    from .routers.worker import perform_add_comment

    settings = get_settings()
    worker = _worker()
    try:
        result = await perform_add_comment(settings, worker, run_id, body)
    except HTTPException as e:
        hint = (
            "claim_work it first" if e.status_code in (404, 409) else ""
        )
        return _err(str(e.detail), hint)
    return {
        "markdown": "Comment posted to the work item's thread.",
        "comment_id": result["comment_id"],
    }


@mcp.tool(**_write("Claim work"))
async def claim_work(run_id: str) -> dict[str, Any]:
    """Atomically claim a run from the pool. Losing a race answers with
    guidance to list again, not an error."""
    settings = get_settings()
    worker = _worker()
    # US-3.14: a project-scoped MCP url only claims that project's runs.
    scoped = _scoped_project.get()
    if scoped and not db.run_in_project(settings, run_id, scoped):
        return _err(
            "that run isn't in this project-scoped MCP url",
            "use the org-wide MCP url, or claim a run from this project",
        )
    # US-31.3: fail-closed capability gate — the refusal names which half is
    # missing (project access vs an unchecked kind), not just that one is.
    refusal = db.worker_run_refusal(settings, str(worker["id"]), run_id)
    if refusal:
        return _err(
            refusal,
            "claim a different item, or ask the manager to adjust this "
            "agent on its Team page",
        )
    run = db.claim_run(settings, run_id, worker)
    if run:
        # US-14.8: seed the trace, so a run that is claimed and then thinks
        # for a long while still reads as "picked it up at HH:MM" rather
        # than as a blank.
        db.record_run_activity(settings, run_id, "claim_work")
        return _next(
            {
                "markdown": (
                    f"Claimed {run['kind']} run `{run_id}`. Lease expires "
                    f"{run['claim_expires_at']}."
                ),
                "run_id": str(run["id"]),
                "kind": run["kind"],
                "claim_expires_at": str(run["claim_expires_at"]),
            },
            (
                "get_work_context",
                "everything needed to do the work, in one call",
            ),
        )
    existing = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if existing:
        return _err(
            "someone else took it",
            "list_available_work again and claim a different item",
        )
    return _err("run not found", "list_available_work shows valid run ids")


@mcp.tool(**_read("Get work context"))
async def get_work_context(run_id: str, ctx: Context) -> dict[str, Any]:
    """A compact brief: what the run is, the story and acceptance
    criteria, branch and hand-back mechanics, test case ids, the approved
    plan (code runs), and named pointers to everything else. Nothing is
    unavailable — the `omitted` list says exactly how to pull each
    section (get_context_detail, get_project_guidelines,
    get_project_learnings)."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    db.extend_claim(settings, run_id, str(worker["id"]), tool="get_work_context")

    ic = run.get("input_context") or {}
    # US-9.9: the intended assignee (person or agent) is informational — it
    # flows through as context and never gates the claim.
    _assignee = db.get_issue_assignee(settings, str(run.get("issue_id") or ""))
    if _assignee:
        ic = {**ic, "assignee": _assignee}
    # US-5.14: the project's editable behavioral template, read live — the
    # mechanics lines around it stay code-generated.
    template = db.get_worker_instruction(
        settings,
        str(run.get("project_id") or ""),
        run["kind"],
        issue_id=str(run.get("issue_id") or "") or None,
    )
    # US-5.12: the work item's comment thread — the whole prior discussion,
    # visible to any claimer including one picking up a retry.
    comments = db.list_issue_comments_for_run(
        settings, run_id, str(worker["org_id"])
    )
    comments_out = [
        {
            "author": c["author"],
            "author_kind": c["author_kind"],
            "body": c["body"],
            "created_at": str(c["created_at"]),
        }
        for c in comments
    ]
    # US-13.5: the brief carries the tail of the thread, not all of it.
    discussion_md = ""
    if comments_out:
        discussion_md = "\n\n## Discussion\n\n"
        if len(comments_out) > 3:
            discussion_md += (
                f"_{len(comments_out) - 3} earlier comment(s) not shown — "
                'get_context_detail(run_id, "discussion") for the full '
                "thread._\n\n"
            )
        discussion_md += "\n".join(
            f"- **{c['author']}** ({c['author_kind']}, {c['created_at']}): "
            f"{c['body']}"
            for c in comments_out[-3:]
        )
    comments_out = comments_out[-5:]
    # US-5.8: linked documents as id/title pointers — context stays lean,
    # the agent fetches what it needs with get_document.
    linked_docs = db.list_run_documents(settings, run)
    docs_out = [
        {
            "document_id": str(d["id"]),
            "name": d["name"],
            "kind": d["attached_to"],
            "mime_type": d["mime_type"],
        }
        for d in linked_docs
    ]
    docs_md = ""
    if docs_out:
        docs_md = (
            "\n\n## Linked documents\n\n"
            + "\n".join(
                f"- {d['name']} (`{d['document_id']}`, {d['kind']})"
                for d in docs_out
            )
            + "\n\nFetch content with get_document(document_id)."
        )
    # US-13.2: prd and breakdown runs read the repository too — the repo
    # resolves from the project row so briefs dispatched before the repo
    # keys existed still name it.
    ctx_repo_full = (
        ic.get("repo_full_name") or run.get("project_repo_full_name") or ""
    )
    ctx_repo_branch = (
        ic.get("default_branch") or run.get("default_branch") or "main"
    )
    repo_section_md = ""
    if "/" in ctx_repo_full:
        repo_section_md = (
            f"\n\n## Repository\n\n"
            f"- Repo: `{ctx_repo_full}` (default branch `{ctx_repo_branch}`)\n"
            "- Readable over MCP: get_repo_tree / read_repo_file — study the "
            "existing modules, naming, and prior art before writing, so the "
            "output fits the actual codebase."
        )

    if run["kind"] == "prd":
        md = (
            f"# {run.get('issue_title') or ic.get('title', 'Work item')}\n\n"
            f"- Kind: **prd** (no branch — submit markdown with submit_prd)\n\n"
            f"## Raw idea\n\n{ic.get('story') or ic.get('body') or '(no body)'}\n"
        )
        md += repo_section_md
        if template:
            md += f"\n\n## Instructions\n\n{template}"
        # US-5.11: the item's living instruction set — read live, so a
        # claim-holder always works from the manager's latest version.
        if (run.get("instruction_set") or "").strip():
            md += f"\n\n## Instruction set\n\n{run['instruction_set']}"
        if ic.get("feedback"):
            md += (
                "\n\n## Send-back feedback (this is a retry)\n\n"
                f"{ic['feedback']}"
            )
        # US-13.5: the brief points at the rest instead of inlining it.
        prd_omitted: list[tuple[str, str]] = []
        if ic.get("previous_prd"):
            prd_omitted.append(
                (
                    "previous_prd",
                    'the sent-back draft you are revising — '
                    'get_context_detail(run_id, "previous_prd")',
                )
            )
        if ic.get("guidelines"):
            prd_omitted.append(
                ("guidelines", "get_project_guidelines() — read before writing")
            )
        if ic.get("learnings"):
            prd_omitted.append(
                ("learnings", "get_project_learnings() — mistakes already made once")
            )
        om_items, om_md = _omitted_manifest(prd_omitted)
        md += om_md + docs_md + discussion_md
        out_prd: dict[str, Any] = {
            "markdown": md,
            "kind": "prd",
            "instructions": template,
            "instruction_set": run.get("instruction_set"),
            "documents": docs_out,
            "comments": comments_out,
            "omitted": om_items,
        }
        prd_steps: list[tuple[str, str]] = []
        if "/" in ctx_repo_full:
            out_prd["repo_full_name"] = ctx_repo_full
            out_prd["default_branch"] = ctx_repo_branch
            prd_steps.append(
                (
                    "get_repo_tree",
                    "study the existing codebase before writing requirements",
                )
            )
        prd_steps.append(
            (
                "submit_prd",
                "hand back the four PRD sections when done "
                "(validate_submission dry-runs them first)",
            )
        )
        return _next(out_prd, *prd_steps)

    if run["kind"] == "breakdown":
        # US-2.33: split the approved PRD into stories. No branch — study the
        # repo over MCP (get_repo_tree / read_repo_file) to ground the split,
        # then hand it back with submit_stories.
        md = (
            f"# {run.get('issue_title') or ic.get('title', 'Work item')}\n\n"
            "- Kind: **breakdown** (no branch — study the repo over MCP, "
            "then submit the split with submit_stories)\n\n"
            f"## Feature\n\n{ic.get('story') or ic.get('body') or '(no body)'}\n"
            f"\n\n## Breakdown mode\n\n{ic.get('breakdown_mode') or 'automatic'}"
        )
        md += repo_section_md
        if (ic.get("breakdown_instructions") or "").strip():
            md += f"\n\n## Manager instructions\n\n{ic['breakdown_instructions']}"
        if template:
            md += f"\n\n## Instructions\n\n{template}"
        if (run.get("instruction_set") or "").strip():
            md += f"\n\n## Instruction set\n\n{run['instruction_set']}"
        # US-13.5: the PRD is what a breakdown splits — it stays inline.
        # Guidelines and learnings become pointers.
        if ic.get("prd"):
            md += f"\n\n## Approved PRD\n\n{ic['prd']}"
        bd_omitted: list[tuple[str, str]] = []
        if ic.get("guidelines"):
            bd_omitted.append(
                ("guidelines", "get_project_guidelines() — read before splitting")
            )
        if ic.get("learnings"):
            bd_omitted.append(
                ("learnings", "get_project_learnings() — mistakes already made once")
            )
        om_items, om_md = _omitted_manifest(bd_omitted)
        md += om_md + docs_md + discussion_md
        out_bd: dict[str, Any] = {
            "markdown": md,
            "kind": "breakdown",
            "instructions": template,
            "instruction_set": run.get("instruction_set"),
            "documents": docs_out,
            "comments": comments_out,
            "omitted": om_items,
        }
        if "/" in ctx_repo_full:
            out_bd["repo_full_name"] = ctx_repo_full
            out_bd["default_branch"] = ctx_repo_branch
        return _next(
            out_bd,
            (
                "get_repo_tree",
                "study the repo to ground the split (read_repo_file for detail)",
            ),
            (
                "submit_stories",
                "hand back the story split as {title, body, acceptance_criteria} "
                "(validate_submission dry-runs it first)",
            ),
        )

    # US-43.8: a guidelines refresh is project-scoped — no work item, so the
    # generic work-item brief below has nothing to describe. It gets its own
    # branch for the same reason release and deploy do.
    #
    # This branch is load-bearing rather than cosmetic. US-43.7 deliberately
    # stripped the task description out of the runner prompt and pointed the
    # agent here as its source of truth; falling through meant the agent got
    # no instruction, no guidelines and no work-item digest, and improvised
    # from AGENTS.md on disk — which is why a pass over a project whose
    # guidelines are empty placeholders reported "nothing to propose".
    if run["kind"] == "guidelines":
        sections = ic.get("current_guidelines") or []
        filled = [s for s in sections if (s.get("content") or "").strip()]
        items = ic.get("work_items") or []
        lines = [
            f"# Guidelines refresh — {ic.get('project_name') or 'this project'}",
            "",
            template or "",
            "",
            "## Scope",
            ic.get("scope_instruction")
            or "Cover every section the repository supports.",
        ]
        if (ic.get("focus") or "").strip():
            lines += ["", "## Focus from the manager", ic["focus"].strip()]
        lines += [
            "",
            "## The guidelines as they stand",
            (
                f"{len(sections)} section(s), {len(filled)} with real content. "
                "These are THE guidelines — the factory's own, which is what "
                "you are proposing against. Do not judge them by AGENTS.md in "
                "the workspace: that file is generated FROM these, so reading "
                "it back tells you nothing about whether they are any good. A "
                "section whose content is a placeholder needs writing."
                if sections
                else "This project has NO guideline sections at all. "
                "Everything you propose will be new."
            ),
            "",
        ]
        for sec in sections:
            body = (sec.get("content") or "").strip()
            lines.append(
                f"### {sec.get('title')} (`{sec.get('section_key')}`)\n"
                + (body if body else "_empty — needs writing_")
            )
        lines += [
            "",
            "## Delivery history",
            f"{len(items)} work item(s) in your context under `work_items` — "
            "what was built, what broke, what was abandoned. The footguns "
            "come from here.",
            "",
            "## Handing back",
            "One call to `submit_guidelines_refresh(run_id, summary, "
            "sections)`. Write no project file and commit nothing. An empty "
            "`sections` list is legal but means the guidelines are already "
            "right — say so only if you checked them, not the generated file.",
        ]
        return _next(
            {
                "markdown": "\n".join(lines),
                "run_id": run_id,
                "kind": "guidelines",
                "project_id": str(run.get("project_id") or ""),
                "scope": ic.get("scope"),
                "focus": ic.get("focus"),
                "current_guidelines": sections,
                "work_items": items,
            },
            (
                "get_repo_tree",
                "study the repository before proposing anything",
            ),
        )

    if run["kind"] == "deploy":
        # US-13.13: the agent orchestrates; the server executes. The
        # context carries the definition, target ref and rollback
        # authorization — never credentials or secret values.
        dep = ic.get("deployment") or {}
        auto_rb = bool(ic.get("auto_rollback"))
        # US-50.3: an external deployment has no machine — no health step and
        # no rollback, and the merge landing is the whole verdict. Saying so
        # here stops an agent retrying tools that will keep refusing.
        external = (dep.get("kind") or "factory") == "external"
        rb_line = (
            "NOT available — an external deployment has no rollback; "
            "recovery means merging a fix"
            if external
            else "PRE-AUTHORIZED (once, on failed health checks only)"
            if auto_rb
            else "NOT authorized — on failure, report "
            "deployed-but-unhealthy and stop"
        )
        auto_rb = auto_rb and not external
        md = (
            f"# Deploy — {dep.get('name')} "
            f"({ic.get('project_name') or 'project'})\n\n"
            "- Kind: **deploy** (trigger, observe, verify, report — the "
            "server does the deploying; you never see credentials)\n"
            + (
                f"- Environment: **{dep.get('environment') or 'dev'}** · "
                "**external** (nothing is copied anywhere: the deployment "
                f"IS a merge of `{ic.get('ref') or dep.get('branch')}` into "
                f"`{dep.get('target_branch')}`, and the team's own pipeline "
                "takes it from there)\n"
                if external
                else f"- Environment: **{dep.get('environment') or 'dev'}** · "
                f"server: {dep.get('server_name') or '?'} · branch: "
                f"`{ic.get('ref') or dep.get('branch')}`\n"
            )
            + (
                f"- Website: {dep.get('website_url')}\n"
                if dep.get("website_url")
                else ""
            )
            + f"- Auto-rollback: **{rb_line}**\n\n"
            "## The loop\n\n"
            "1. `trigger_deployment` — starts the real deployment run "
            "(the rails are re-checked server-side)\n"
            "2. `get_deployment_run_status` — poll until it finishes; "
            "the log tail is included\n"
            + (
                "3. `submit_deploy_run` — the honest verdict. There is no "
                "health check to run and nothing to roll back: the merge "
                "landing on the target branch is the whole verdict, and "
                "what the other system does with it is not yours to claim"
                if external
                else "3. `get_deployment_health` — verify before declaring "
                "anything; never claim an outcome you did not observe\n"
                + (
                    "4. `trigger_deployment_rollback` — only on failed "
                    "health checks, only once\n"
                    if auto_rb
                    else ""
                )
                + f"{'5' if auto_rb else '4'}. `submit_deploy_run` — the "
                "honest verdict: deployed / deployed-unhealthy / rolled-back"
            )
        )
        if template:
            md += f"\n\n## Instructions\n\n{template}"
        md += docs_md + discussion_md
        return _next(
            {
                "markdown": md,
                "kind": "deploy",
                "deployment": dep,
                "ref": ic.get("ref"),
                "auto_rollback": auto_rb,
                "instructions": template,
                "documents": docs_out,
                "comments": comments_out,
            },
            (
                "trigger_deployment",
                "start the deployment — the server executes it",
            ),
        )

    if run["kind"] == "release":
        # US-13.12: an agent-prepared release cut — project-scoped, no
        # issue. The worker reads the unreleased ledger and the repo, then
        # hands back notes titled with the computed version (and the
        # promotion PR where the strategy calls for one). Completion
        # changes no environment; the manager stays the gate.
        records = ic.get("unreleased_records") or []
        next_version = ic.get("next_version") or ""
        records_md = "\n".join(
            f"- **{r.get('display_id') or r.get('issue_id')}** — "
            f"{r.get('title')} ({r.get('type')}, merge "
            f"`{(r.get('merge_commit_sha') or '')[:9]}`)"
            for r in records
        )
        uat = (ic.get("uat_branch") or "").strip()
        md = (
            f"# Release cut — {ic.get('project_name') or 'project'}\n\n"
            "- Kind: **release** (prepare the cut — notes + promotion PR; "
            "nothing deploys)\n"
            f"- Next version: **{next_version}** (system-computed — "
            "transcribe it, never invent one)\n"
            f"- Default branch: `{ic.get('default_branch')}`"
            + (f" · UAT branch: `{uat}`" if uat else " · no UAT branch — "
               "notes are the whole deliverable")
            + "\n\n## Unreleased work\n\n"
            + (records_md or "_none_")
            + "\n\n## Deliverables\n\n"
            f"1. **Release notes** titled with {next_version}: a changelog "
            "of user-facing changes first, then fixes and internal "
            "changes, listing included items by display id. Read the repo "
            "(get_repo_tree / read_repo_file) to describe changes "
            "accurately.\n"
            "2. Where a UAT branch exists and the default branch is ahead, "
            "ask for the promotion PR by submitting with "
            "open_promotion_pr=true — the factory opens the "
            "default→UAT PR itself; you never push.\n\n"
            "Finish with submit_release_run(notes_markdown, "
            "open_promotion_pr)."
        )
        if template:
            md += f"\n\n## Instructions\n\n{template}"
        md += docs_md + discussion_md
        return _next(
            {
                "markdown": md,
                "kind": "release",
                "next_version": next_version,
                "repo_full_name": ic.get("repo_full_name"),
                "default_branch": ic.get("default_branch"),
                "uat_branch": ic.get("uat_branch"),
                "production_branch": ic.get("production_branch"),
                "unreleased_records": records,
                "instructions": template,
                "documents": docs_out,
                "comments": comments_out,
            },
            (
                "get_repo_tree",
                "read what actually changed before writing the notes",
            ),
            (
                "submit_release_run",
                f"hand back the {next_version} notes (and request the "
                "promotion PR where one applies)",
            ),
        )

    if run["kind"] == "test":
        # US-13.11: a staffed verification pass. The branch is the one the
        # code run submitted (frozen at dispatch); the worker checks it out
        # read-only, runs the declared commands, and reports per-case
        # results. Build config IS served — a test worker needs the env.
        t_request = ctx.request_context.request if ctx.request_context else None
        t_base = str(t_request.base_url).rstrip("/") if t_request else ""
        t_remote = (
            f"{t_base}/git/{run['org_shortname']}/{run['project_slug']}.git"
        )
        branch_ref = ic.get("branch_ref") or ""
        t_run_commands = db.get_run_commands_section(
            settings, str(run.get("project_id") or "")
        )
        t_environment = db.get_project_environment(
            settings, str(run.get("project_id") or "")
        )
        t_bc = await build_config.fetch_build_config_values(
            settings, str(run["org_id"]), str(run.get("project_id") or "")
        )
        cases = ic.get("test_cases") or []
        manager_instructions = (ic.get("instructions") or "").strip() or None
        md = (
            f"# {run.get('issue_title') or ic.get('title', 'Work item')}\n\n"
            "- Kind: **test** (staffed verification — execution IS the "
            "work)\n\n"
            "## Verification target\n\n"
            f"- Branch: `{branch_ref}` (the submitted code run's branch)\n"
            + (f"- PR: {ic.get('pr_url')}\n" if ic.get("pr_url") else "")
            + f"- Factory git remote: `{t_remote}` (HTTP Basic — password "
            "is this worker token). **Read-only: clone/fetch and check "
            "out the branch; a test run never pushes.**\n"
        )
        if t_environment:
            md += f"\n## Environment\n\n{t_environment['markdown']}\n"
        if t_run_commands:
            md += (
                f"\n## Run commands\n\n{t_run_commands}\n\n"
                "Run these against the checked-out branch.\n"
            )
        if t_bc:
            md += (
                "\n## Build configuration\n\n"
                "Apply these as environment variables before running "
                "anything (values in the `build_config` field; do not "
                "print or commit them).\n\n"
                + "\n".join(f"- `{k}`" for k in sorted(t_bc))
                + "\n"
            )
        md += f"\n## Story under verification\n\n{ic.get('story') or '(no body)'}\n"
        if manager_instructions:
            md += f"\n## Manager's verification instructions\n\n{manager_instructions}\n"
        if cases:
            md += "\n" + _test_cases_full_md(cases)
        if template:
            md += f"\n\n## Instructions\n\n{template}"
        if (run.get("instruction_set") or "").strip():
            md += f"\n\n## Instruction set\n\n{run['instruction_set']}"
        md += docs_md + discussion_md
        return _next(
            {
                "markdown": md,
                "kind": "test",
                "branch_name": branch_ref,
                "git_remote_url": t_remote,
                "repo_full_name": ic.get("repo_full_name")
                or run.get("project_repo_full_name"),
                "default_branch": ic.get("default_branch")
                or run.get("default_branch"),
                "build_config": t_bc,
                "run_commands": t_run_commands,
                "instructions": template,
                "instruction_set": run.get("instruction_set"),
                "manager_instructions": manager_instructions,
                "documents": docs_out,
                "comments": comments_out,
                "test_cases": [
                    {
                        "test_case_id": str(c.get("id") or ""),
                        "title": c.get("title"),
                        "steps": c.get("steps"),
                        "expected_result": c.get("expected_result"),
                    }
                    for c in cases
                ],
            },
            (
                "report_test_results",
                "record passed / failed / blocked per case — only what "
                "you actually observed",
            ),
            (
                "submit_test_run",
                "complete with a summary once results are reported "
                "(release_work with a note if you could not execute "
                "anything)",
            ),
        )

    request = ctx.request_context.request if ctx.request_context else None
    base = str(request.base_url).rstrip("/") if request is not None else ""
    # US-7.3: the working branch derives from the project's dev branching
    # strategy (story / work_item / main), not a hardcoded issue id. It is
    # stored on the code run the first time it is computed so it stays stable
    # and the git proxy can match a push to it on hand-back.
    branch, dev_strategy, submit_mode = db.resolve_working_branch(settings, run)
    if run["kind"] == "code":
        db.set_run_branch_ref(settings, run_id, branch)
    # US-7.10 / US-7.15: the epic-scoped, type-prefixed work-item id so commits,
    # branch names, and PR titles reference it rather than the UUID.
    display_id = db.work_item_display_id(
        run.get("issue_type"),
        run.get("epic_number"),
        run.get("item_no"),
        run.get("sub_no"),
    )
    remote = f"{base}/git/{run['org_shortname']}/{run['project_slug']}.git"
    # US-5.9: the project's declared verify commands, read live — shown
    # right by the branch/remote block so the agent runs them before
    # submitting instead of guessing at the toolchain.
    run_commands = db.get_run_commands_section(
        settings, str(run.get("project_id") or "")
    )
    # US-5.23: the declared environment, read live and placed with the
    # branch/remote block — read before work starts, plan and code alike.
    environment = db.get_project_environment(
        settings, str(run.get("project_id") or "")
    )
    # US-7.2: the reachable Website of each UAT/Production environment, so an
    # agent testing an environment is told where to reach it.
    env_websites = db.get_project_environment_websites(
        settings, str(run.get("project_id") or "")
    )
    # US-7.9: the write-only build/test config values — delivered ONLY to a
    # code run of the owning project, never PRD/plan runs, never the browser.
    bc_values: dict[str, str] = {}
    if run["kind"] == "code":
        bc_values = await build_config.fetch_build_config_values(
            settings, str(run["org_id"]), str(run.get("project_id") or "")
        )
    md = (
        f"# {run.get('issue_title') or ic.get('title', 'Work item')}\n\n"
        + (f"- ID: **{display_id}** (use this in commits/PR titles)\n" if display_id else "")
        + f"- Kind: **{run['kind']}**\n\n"
    )
    # US-5.28: the transport guidance is rendered fresh here on every run —
    # the one layer that reaches every project, including those whose
    # copied guidelines predate the git-free loop. Code runs lead with the
    # MCP-only hand-back; git-native stays as the labeled alternative.
    if run["kind"] == "code":
        # US-7.3 / US-7.15: what "submit" does depends on the project's dev
        # branching strategy. In `direct` mode the factory commits to the
        # default branch and opens no PR (the review gate is bypassed).
        submit_line = (
            "3. `submit_changeset` — declare that same `base_sha` and your "
            "changed files; the factory commits and pushes to the default "
            f"branch `{branch}` — **no PR** (this project commits directly "
            "to main)\n"
            if submit_mode == "direct"
            else "3. `submit_changeset` — declare that same `base_sha` and "
            "your changed files; the factory commits, pushes the branch, "
            "and opens the PR server-side\n"
        )
        # US-27.1: a multi-story run hands back story by story. Run 11c564b0
        # split its hand-back because 79 files do not fit in one turn, and the
        # first call closed the run — so the protocol is stated here, where
        # every code worker reads before it starts.
        multi_story = bool(
            isinstance(ic.get("stories"), list) and len(ic["stories"]) > 1
        )
        if multi_story:
            submit_line += (
                "   - This run covers "
                f"**{len(ic['stories'])} stories**. Work them in the order "
                "listed below and commit each one as you finish it: "
                "`submit_changeset(..., issue_ids=[\"US-…\"], final=false)`, "
                "declaring the sha the previous call returned as the next "
                "`base_sha`.\n"
                "   - Close the run with one last call carrying "
                "`final=true`. Only stories with a landed commit move to "
                "review; closing with one uncommitted is refused unless you "
                "pass `allow_partial=true`.\n"
                "   - Do not hoard six stories for one giant call. If you "
                "die halfway, the factory keeps every story you committed.\n"
                # US-40.2
                "   - If you push a branch instead of calling "
                "`submit_changeset`, every commit must carry a "
                "`Factory-Story: <id>` trailer naming the story it "
                "implements. It is the only signal that says which story a "
                "commit landed; a branch with none is refused.\n"
            )
        md += (
            "## Hand-back (MCP-only — no git tooling required)\n\n"
            "1. `get_workspace` — the repo as a zip, pinned to a "
            "`base_sha`\n"
            "2. Work on the extracted tree locally, following the "
            "environment block below\n"
            + submit_line
            + "4. `report_test_results` — **only for tests you actually "
            "ran.** Writing tests is part of the work; running them "
            "depends on your environment. If you have a shell and can "
            "execute the suite, do, and record pass/fail against the test "
            "case ids listed below — agent-verified passes lift the "
            "unrun-tests merge gate. If you cannot run them, submit "
            "anyway and report nothing: unreported cases stay unrun and "
            "the manager sees that honestly. Never report a result you "
            "did not observe, and never hold the run waiting for an "
            "ability you do not have. Use `blocked` (with evidence) only "
            "for a case someone looked at and could not run.\n\n"
            "Git-native alternative (only if you have git tooling): clone "
            "the factory remote, work on the branch, push, then "
            "submit_code_work.\n"
            f"- Branch: `{branch}` (strategy: {dev_strategy}, submit mode: "
            f"{submit_mode})\n"
            f"- Factory git remote: `{remote}` (HTTP Basic — password is "
            f"this same worker token; no GitHub credentials"
            + (", no PR to open)\n\n" if submit_mode == "pr" else ")\n\n")
        )
    else:
        md += (
            "Study the repository over MCP: `get_repo_tree` lists any "
            "path, `read_repo_file` reads any file — no clone or checkout "
            "is needed for planning.\n"
            f"- Branch: `{branch}` (for reference — the code run that "
            f"follows will work here)\n"
            f"- Factory git remote: `{remote}`\n\n"
        )
    if environment:
        md += f"## Environment\n\n{environment['markdown']}\n\n"
    if env_websites:
        md += (
            "## Environment websites\n\n"
            + "\n".join(
                f"- **{env.upper()}**: {url}" for env, url in env_websites.items()
            )
            + "\n\n"
        )
    if run_commands:
        md += (
            "## Run commands\n\n"
            f"{run_commands}\n\n"
            "Run these to verify your work before submitting.\n\n"
        )
    if bc_values:
        md += (
            "## Build configuration\n\n"
            "Sandbox/test config values for building and verifying this "
            "project — apply them as environment variables (e.g. write a "
            "`.env`). Do not print or commit them; they are non-production "
            "and scoped to this run.\n\n"
            + "\n".join(f"- `{k}`" for k in sorted(bc_values))
            + "\n\nThe values are in the `build_config` field of this "
            "response.\n\n"
        )
    if (run.get("project_summary") or "").strip():
        md += f"## Project summary\n\n{run['project_summary'].strip()}\n\n"
    # US-22.9: a feature-level code run carries several stories. One section
    # each, in build order, with the acceptance criteria inline — they are the
    # contract the agent is judged against. The approved plans are pulled per
    # story instead (see `omitted` below): five inlined plans would blow the
    # brief and bury the criteria.
    stories = ic.get("stories") if isinstance(ic.get("stories"), list) else None
    if stories:
        md += (
            f"## The {len(stories)} stories in this build\n\n"
            "You are building this whole feature as ONE change: one branch, "
            "one PR, one review. Read all of it before designing — the seams "
            "between these stories are the point, and they are yours to get "
            "right. Each story's approved implementation plan is pulled "
            "separately; see Omitted below.\n\n"
            "**Commit story by story** (US-27.1): one `submit_changeset` per "
            "story as you finish it, naming it in `issue_ids`, then a final "
            "call to close the run. Intermediate commits need not be green — "
            "the PR is reviewed at head — so there is no reason to hold work "
            "back, and every reason not to: a commit that lands is a story "
            "that survives you.\n"
        )
        for s in stories:
            md += (
                f"\n### {s.get('display_id')} — {s.get('title')}\n\n"
                f"{(s.get('story') or '(no body)')}\n\n"
                "**Acceptance criteria**\n\n"
                + "\n".join(f"- {c}" for c in s.get("acceptance_criteria") or [])
                + "\n"
            )
            if s.get("feedback"):
                md += (
                    "\n**Rejection feedback (this is a retry)**\n\n"
                    f"{s['feedback']}\n"
                )
            md += _test_cases_md(s)
    else:
        md += (
            f"## Story\n\n{ic.get('story') or ic.get('body') or '(no body)'}\n\n"
            f"## Acceptance criteria\n\n"
            + "\n".join(f"- {c}" for c in ic.get("acceptance_criteria") or [])
        )
        md += _test_cases_md(ic)
    if template:
        md += f"\n\n## Instructions\n\n{template}"
    # US-5.11: the item's living instruction set — read live, so a
    # claim-holder always works from the manager's latest version.
    if (run.get("instruction_set") or "").strip():
        md += f"\n\n## Instruction set\n\n{run['instruction_set']}"
    # US-13.5: the plan is the contract a code run is judged against — it
    # stays inline (with its test plan). Rejection feedback stays too.
    # Everything else becomes a named pointer.
    inline_keys: list[tuple[str, str]] = [
        ("feedback", "Rejection feedback (this is a retry)"),
    ]
    if run["kind"] == "code" and not stories:
        # A multi-story run has no single plan to inline — each story's plan
        # is a separate pull (US-22.9).
        inline_keys = [
            ("plan", "Approved implementation plan"),
            ("test_plan", "Approved test plan"),
        ] + inline_keys
    for key, title in inline_keys:
        if ic.get(key):
            md += f"\n\n## {title}\n\n{ic[key]}"

    # US-48.4: the screen this story was drawn as, if it has one.
    #
    # Read LIVE rather than from input_context: a wireframe can be redrawn
    # after the plan run was dispatched, and the agent should build to the
    # current one. Only the DECLARED SCREENS travel — Phase 38 measured that a
    # plan run already averages 1.4M input tokens, and a 40 KB page in every
    # plan context would make that worse for no gain. The committed path is
    # named so an agent that wants the rendering reads one file deliberately.
    md += _wireframe_section(settings, run, stories)

    pc_omitted: list[tuple[str, str]] = []
    for s in stories or []:
        # US-22.9: one pull per story's approved plan, named by display id so
        # the agent asks for the plan it means.
        pc_omitted.append(
            (
                f"plan:{s.get('display_id')}",
                f"{s.get('display_id')}'s approved implementation and test "
                f'plan — get_context_detail(run_id, "plan:{s.get("display_id")}")',
            )
        )
    if run["kind"] == "plan" and ic.get("previous_plan"):
        pc_omitted.append(
            (
                "previous_plan",
                'the superseded plan this re-plan replaces — '
                'get_context_detail(run_id, "previous_plan")',
            )
        )
    if ic.get("prd"):
        pc_omitted.append(
            (
                "prd",
                'the approved PRD governing this work — '
                'get_context_detail(run_id, "prd")',
            )
        )
    if ic.get("guidelines"):
        pc_omitted.append(
            ("guidelines", "get_project_guidelines() — read before writing code")
        )
    if ic.get("learnings"):
        pc_omitted.append(
            ("learnings", "get_project_learnings() — mistakes already made once")
        )
    if run["kind"] == "code":
        # US-7.5: the Versioning & Release block is reference material on
        # code runs — pulled on demand since us-13.5.
        pc_omitted.append(
            (
                "release_reference",
                "how releases and versions work here — "
                'get_context_detail(run_id, "release_reference")',
            )
        )
    if run.get("docs_tree_enabled"):
        # US-22.8: name index.json and say the tree ships inside the
        # workspace. Agents that don't know that spend MCP round trips on
        # get_repo_tree / read_repo_file reading files they already have.
        pc_omitted.append(
            (
                "repo docs tree",
                "every PRD, story and plan lives in the repo under "
                "docs/factory/ — already on disk in your workspace, no tool "
                "call needed. Read docs/factory/index.json for what exists "
                "and in what order, then the stories before yours in this "
                "feature before designing",
            )
        )
    om_items, om_md = _omitted_manifest(pc_omitted)
    md += om_md + docs_md + discussion_md
    out = {
        "markdown": md,
        "omitted": om_items,
        "branch_name": branch,
        "git_remote_url": remote,
        "repo_full_name": ic.get("repo_full_name"),
        "default_branch": run.get("default_branch") or ic.get("default_branch"),
        # US-7.3 / US-7.15: the branching strategy + submit mode so the agent
        # knows whether submit opens a PR (`pr`) or commits to main (`direct`).
        "dev_branch_strategy": dev_strategy,
        "submit_mode": submit_mode,
        # US-7.10 / US-7.15: the epic-scoped display id and the project summary.
        "work_item_id": display_id,
        "project_summary": run.get("project_summary") or None,
        # US-7.9 / US-7.15: write-only build config values, present only for a
        # code run of the owning project.
        "build_config": bc_values,
        "kind": run["kind"],
        "instructions": template,
        "instruction_set": run.get("instruction_set"),
        "run_commands": run_commands,
        "documents": docs_out,
        "comments": comments_out,
        # US-5.19 / US-13.5: structured cases stay addressable by
        # report_test_results but compact — steps and expected results via
        # get_context_detail(run_id, "test_cases").
        "test_cases": [
            {
                "test_case_id": str(c.get("id") or ""),
                "title": c.get("title"),
            }
            for c in ic.get("test_cases") or []
        ],
    }
    # US-81.5: the project's declared automated suites plus the authoring
    # contract — delivered in the rendered context (not the baked template)
    # so project-level instruction overrides can never shadow it.
    if run["kind"] in ("plan", "code"):
        suites = db.list_project_suites(settings, str(run.get("project_id") or ""))
        if suites:
            out["test_suites"] = suites
            out["automation_contract"] = (
                "This project runs automated suites against its deployed UAT "
                "instance. In a test plan's JSON, a case may carry "
                "execution:'automated' (plus layer:'api'|'browser') when its "
                "acceptance is machine-checkable — approval stores it. On a "
                "code run, an automated-marked case means the spec file is "
                "part of THIS change, written under the suite's convention so "
                "its JUnit identity is stable, and you report the links with "
                "report_spec_map({test_case_id, suite_id, spec_ref}) before "
                "submitting. specs run with SF_BASE_URL pointing at the "
                "deployed instance."
            )
    # US-81.6: the project's declared fast pre-submit test command. In the
    # rendered context for the same shadowing reason as above.
    if run["kind"] == "code" and (run.get("presubmit_test_command") or "").strip():
        out["presubmit_gate"] = {
            "command": run["presubmit_test_command"],
            "note": (
                "Run this fast test command in your workspace after your "
                "final change and report the outcome with "
                "report_test_evidence before submitting — it is the "
                "project's declared gate, and 'tests pass' in a hand-back "
                "means this passed."
            ),
        }
    # US-5.23: structured environment — the us-5.9 run commands fold in
    # as an ordered list; absent entirely when nothing is configured.
    if environment or run_commands or env_websites:
        out["environment"] = {
            "runtime": (environment or {}).get("runtime"),
            "setup_commands": (environment or {}).get("setup_commands") or [],
            "notes": (environment or {}).get("notes"),
            "run_commands": _parse_command_lines(run_commands),
            # US-7.2: reachable Website per environment (uat/production);
            # absent entirely when none is configured.
            "websites": env_websites,
        }
    if run["kind"] == "code":
        return _next(
            out,
            (
                "get_workspace",
                "the repo as a zip pinned to a base_sha — the git-free "
                "hand-back starts here",
            ),
        )
    return _next(
        out,
        (
            "submit_plan",
            "hand back the implementation plan + test plan when done "
            "(validate_submission dry-runs them first)",
        ),
    )


# US-13.5: the pull side of the compact brief — one explicit call per
# omitted section, so nothing get_work_context stopped inlining became
# unavailable.

_DETAIL_IC_SECTIONS = {
    "prd": "the approved PRD governing this work",
    "previous_prd": "the sent-back PRD draft being revised",
    "plan": "the approved implementation plan",
    "test_plan": "the approved test plan",
    "previous_plan": "the superseded plan this re-plan replaces",
    "feedback": "the manager's send-back / rejection feedback",
}


@mcp.tool(**_read("Get context detail"))
async def get_context_detail(run_id: str, section: str) -> dict[str, Any]:
    """Pull one full section the compact get_work_context brief omitted.
    Sections: prd, previous_prd, plan, test_plan, previous_plan, feedback,
    test_cases (full steps + expected results), discussion (the whole
    comment thread), release_reference (how releases and versions work).
    Guidelines and learnings have their own standing tools
    (get_project_guidelines, get_project_learnings)."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    db.extend_claim(settings, run_id, str(worker["id"]), tool="get_context_detail")
    ic = run.get("input_context") or {}
    key = (section or "").strip().lower()
    if key in ("guidelines", "learnings"):
        return _err(
            f"{key} has its own tool",
            "call get_project_guidelines / get_project_learnings — they "
            "work any time, claim or not",
        )
    # US-22.9: `plan:<display-id>` pulls one story's approved plan out of a
    # multi-story code run. Refuses a display id outside the run's membership,
    # so an agent cannot read a plan it is not building.
    if key.startswith("plan:"):
        wanted = (section or "").split(":", 1)[1].strip()
        stories = ic.get("stories") if isinstance(ic.get("stories"), list) else []
        if not stories:
            return _err(
                "this run carries one story, not several",
                'use get_context_detail(run_id, "plan")',
            )
        known = [str(s.get("display_id") or "") for s in stories]
        match = next(
            (s for s in stories if str(s.get("display_id") or "").lower() == wanted.lower()),
            None,
        )
        if match is None:
            return _err(
                f"{wanted} is not in this run",
                f"this run builds {', '.join(known)}",
            )
        plans = db.get_approved_plans(
            settings, str(match.get("issue_id") or ""), str(worker["org_id"])
        )
        if not plans.get("plan"):
            return _err(
                f"{match.get('display_id')} has no approved implementation plan",
                "tell the manager — a story in a build must have one",
            )
        md = (
            f"# {match.get('display_id')} — {match.get('title')}\n\n"
            f"## Approved implementation plan\n\n{plans['plan']}\n"
        )
        if plans.get("test_plan"):
            md += f"\n## Approved test plan\n\n{plans['test_plan']}\n"
        return {
            "markdown": md,
            "section": f"plan:{match.get('display_id')}",
            "display_id": match.get("display_id"),
            "plan": plans.get("plan"),
            "test_plan": plans.get("test_plan"),
        }
    if key == "test_cases":
        cases = ic.get("test_cases") or []
        if not cases:
            return _err(
                "this run's context carries no test cases",
                "get_work_context lists what exists",
            )
        return {
            "markdown": _test_cases_full_md(cases),
            "section": key,
            "test_cases": [
                {
                    "test_case_id": str(c.get("id") or ""),
                    "title": c.get("title"),
                    "steps": c.get("steps"),
                    "expected_result": c.get("expected_result"),
                }
                for c in cases
            ],
        }
    if key == "discussion":
        comments = db.list_issue_comments_for_run(
            settings, run_id, str(worker["org_id"])
        )
        if not comments:
            return {
                "markdown": "No comments on this work item yet.",
                "section": key,
                "comments": [],
            }
        rows = [
            {
                "author": c["author"],
                "author_kind": c["author_kind"],
                "body": c["body"],
                "created_at": str(c["created_at"]),
            }
            for c in comments
        ]
        return {
            "markdown": "## Discussion (full thread)\n\n"
            + "\n".join(
                f"- **{c['author']}** ({c['author_kind']}, "
                f"{c['created_at']}): {c['body']}"
                for c in rows
            ),
            "section": key,
            "comments": rows,
        }
    if key == "release_reference":
        text = db.get_worker_instruction(
            settings, str(run.get("project_id") or ""), "release"
        )
        if not (text or "").strip():
            return _err(
                "no release reference on this project",
                "the project has no release instruction configured",
            )
        return {
            "markdown": f"## Versioning & Release (reference)\n\n{text}",
            "section": key,
            "content": text,
        }
    if key in _DETAIL_IC_SECTIONS:
        text = ic.get(key)
        if not (text or "").strip():
            return _err(
                f"this run's context has no {key}",
                "get_work_context's `omitted` list names what exists",
            )
        return {
            "markdown": f"## {_DETAIL_IC_SECTIONS[key]}\n\n{text}",
            "section": key,
            "content": text,
        }
    return _err(
        f"unknown section '{section}'",
        "one of: prd, previous_prd, plan, test_plan, previous_plan, "
        "feedback, test_cases, discussion, release_reference",
    )


# US-5.20: read-only repo access for claim holders — a plan run can
# genuinely study the repository, a code-run retry can inspect the branch
# as it stands, all without cloning. Claim required; the org's GitHub
# credential stays server-side and never appears in any response.


async def _held_run_and_token(
    run_id: str,
    tool: str = "repo_read",
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """(run, token, error): loads the claimed run, extends the lease, and
    resolves the org's GitHub token. Exactly one of (run+token) / error."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return None, None, _err(
            "run not found", "list_available_work shows valid run ids"
        )
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return None, None, _err("you do not hold this run", "claim_work it first")
    db.extend_claim(settings, run_id, str(worker["id"]), tool=tool)
    ic = run.get("input_context") or {}
    # US-13.2: the repo resolves from the project row when the run's frozen
    # context has no repo keys (the prd/breakdown dispatch RPCs never wrote
    # them) — resolved at tool-call time so the fix cannot drift out of a
    # context builder again. Runs dispatched before this change keep working.
    repo_full = (
        ic.get("repo_full_name") or run.get("project_repo_full_name") or ""
    )
    if "/" not in repo_full:
        return None, None, _err(
            "this project has no linked GitHub repository",
            "repository access needs a GitHub-connected project",
        )
    run["_repo_full_name"] = repo_full
    run["_repo_ic"] = {
        **ic,
        "repo_full_name": repo_full,
        "default_branch": ic.get("default_branch")
        or run.get("default_branch")
        or "main",
    }
    try:
        token = await github_tokens.token_for_org(
            settings, str(run["org_id"]), repo_full
        )
    except github.GitHubError as e:
        return None, None, _github_err(e)
    return run, token, None


async def _project_repo_and_token(
    project_id: str,
    tool: str = "repo_read",
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
    """(project, token, error) — no claim needed. Two gates, both on by
    default: the worker's own opt-in (workers.no_claim_checkout) and a
    project-access grant (worker_capabilities via worker_allowed_for_project
    — the exact predicate the git proxy's clone/fetch gate uses with no
    claim in play, US-3.12/31.3). Exactly one of (project+token) / error."""
    settings = get_settings()
    worker = _worker()
    pid = project_id or _scoped_project.get()
    if not pid:
        return None, None, _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    if not worker.get("no_claim_checkout", True):
        return None, None, _err(
            "no-claim checkout is turned off for this worker",
            "turn it on from the worker's Team page, or claim_work a run "
            "on this project first",
        )
    if not db.worker_allowed_for_project(settings, str(worker["id"]), pid):
        return None, None, _err(
            "this worker does not have access to that project",
            "give it access on its Team page",
        )
    project = db.get_project_repo_by_id(settings, pid, str(worker["org_id"]))
    if not project or "/" not in (project.get("repo_full_name") or ""):
        return None, None, _err(
            "this project has no linked GitHub repository",
            "repository access needs a GitHub-connected project",
        )
    try:
        token = await github_tokens.token_for_org(
            settings, str(worker["org_id"]), project["repo_full_name"]
        )
    except github.GitHubError as e:
        return None, None, _github_err(e)
    return project, token, None


async def _tree_error_triage(
    token: str, owner: str, repo: str, use_ref: str
) -> dict[str, Any]:
    """US-13.2: a failed tree read is three different states — an empty
    repository (normal for a new project), a ref that doesn't exist, or a
    repository that isn't reachable at all — and the answer says which."""
    try:
        await github.get_repo(token, owner, repo)
    except github.GitHubError:
        return _err(
            f"the repository {owner}/{repo} is not reachable",
            "only a manager can fix this — ask them to check the project's "
            "GitHub connection (Settings → GitHub)",
        )
    try:
        branches = await github.list_branches(token, owner, repo)
    except github.GitHubError:
        branches = None
    if branches is not None and len(branches) == 0:
        return {
            "markdown": (
                f"# Repository tree — `{owner}/{repo}` @ `{use_ref}`\n\n"
                "_The repository has no files yet (no commits) — a normal "
                "state for a new project. Nothing to read until the first "
                "push._"
            ),
            "ref": use_ref,
            "entries": [],
            "truncated": False,
            "empty_repo": True,
        }
    return _err(
        f"ref '{use_ref}' not found in this repo",
        "the repository exists — get_work_context names the default branch; "
        "pass ref to read a specific branch",
    )


def _format_tree_result(
    data: dict[str, Any], repo_full: str, use_ref: str, path: str
) -> dict[str, Any]:
    """Shared tail of get_repo_tree / get_project_tree once the raw GitHub
    tree response is in hand: path-filter, truncate, render markdown."""
    entries = data.get("tree") or []
    prefix = path.strip().strip("/")
    if prefix:
        entries = [
            t
            for t in entries
            if t.get("path") == prefix
            or (t.get("path") or "").startswith(prefix + "/")
        ]
        if not entries:
            return _err(
                f"no entries under '{prefix}' at '{use_ref}'",
                "without path lists from the repository root",
            )
    total = len(entries)
    truncated = bool(data.get("truncated")) or total > repo_browse.MAX_TREE_ENTRIES
    entries = entries[: repo_browse.MAX_TREE_ENTRIES]
    out = [
        {"path": t.get("path"), "type": t.get("type"), "size": t.get("size")}
        for t in entries
    ]
    lines = "\n".join(
        f"- `{e['path']}{'/' if e['type'] == 'tree' else ''}`"
        + (f" ({e['size']} bytes)" if e.get("size") is not None else "")
        for e in out
    )
    md = f"# Repository tree — `{repo_full}` @ `{use_ref}`\n\n{lines}"
    if truncated:
        md += (
            f"\n\n_Truncated at {repo_browse.MAX_TREE_ENTRIES} of {total} "
            "entries — narrow with path to see the rest._"
        )
    return {
        "markdown": md,
        "ref": use_ref,
        "entries": out,
        "truncated": truncated,
    }


@mcp.tool(**_read("Browse repository tree"))
async def get_repo_tree(
    run_id: str, path: str = "", ref: str = ""
) -> dict[str, Any]:
    """List your claimed run's repository tree — paths, types, sizes —
    without cloning anything. path narrows to a subtree; ref defaults to
    the run's work branch when it exists on GitHub (retries), otherwise
    the default branch. Plan runs: study the repository with this before
    writing the plan."""
    run, token, err = await _held_run_and_token(run_id, "get_repo_tree")
    if err:
        return err
    ic = run["_repo_ic"]
    repo_full = run["_repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    settings = get_settings()
    try:
        use_ref = await repo_browse.resolve_ref(
            token, ic, str(run["issue_id"]), ref,
            run_branch=db.resolve_working_branch(settings, run)[0],
        )
    except github.GitHubError as e:
        return _github_err(e)
    try:
        data = await github.get_tree(token, owner, repo, use_ref)
    except github.GitHubCredentialError as e:
        return _github_err(e)
    except github.GitHubError:
        return await _tree_error_triage(token, owner, repo, use_ref)
    return _format_tree_result(data, repo_full, use_ref, path)


@mcp.tool(**_read("Browse a project's repository tree with no claim"))
async def get_project_tree(
    project_id: str = "", path: str = "", ref: str = ""
) -> dict[str, Any]:
    """List a project's repository tree — paths, types, sizes — without
    claiming or holding any run. For exploring a project, checking it out
    for testing, or deciding what to work on. ref defaults to the
    project's default branch. Defaults project_id to the MCP url's scoped
    project; pass it explicitly on an org-wide url. Needs project access
    and this worker's no-claim checkout left on (both on by default —
    see get_project_workspace)."""
    project, token, err = await _project_repo_and_token(
        project_id, "get_project_tree"
    )
    if err:
        return err
    repo_full = project["repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    use_ref = ref.strip() or project["default_branch"] or "main"
    try:
        data = await github.get_tree(token, owner, repo, use_ref)
    except github.GitHubCredentialError as e:
        return _github_err(e)
    except github.GitHubError:
        return await _tree_error_triage(token, owner, repo, use_ref)
    return _format_tree_result(data, repo_full, use_ref, path)


"""US-21.2: how many changed files one page of get_release_changes carries.
A release spanning hundreds of commits cannot be returned whole; the cap is
reported rather than applied silently, because a range that claims to be
complete and isn't is exactly the failure the tool exists to prevent."""
RELEASE_FILES_PAGE = 300


@mcp.tool(**_read("Get release changes"))
async def get_release_changes(
    prep_id: str, path_prefix: str = "", cursor: int = 0
) -> dict[str, Any]:
    """What actually changed in the release your claimed prep job is
    describing — the commits and the changed file paths between the previous
    release and this one, plus the work items it includes. Read this BEFORE
    writing the notes: it is the only way to know which migrations ran and
    which modules moved. path_prefix narrows the file list (e.g.
    'infra/supabase/migrations/'); follow `cursor` when `truncated` is true,
    and if you cannot read the whole range, SAY SO in the notes."""
    settings = get_settings()
    worker = _worker()
    prep = db.get_release_prep(settings, prep_id, str(worker["org_id"]))
    if not prep:
        return _err("release prep not found", "list_release_prep_work shows valid ids")
    if str(prep.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this release prep", "claim_release_prep_work it first")
    db.heartbeat_release_prep(settings, prep_id, str(worker["id"]))

    release = db.get_release(settings, str(prep["release_id"]))
    if not release:
        return _err("release not found", "report and stop")

    repo_full = prep.get("repo_full_name") or ""
    if "/" not in repo_full:
        return _err(
            "this project has no linked GitHub repository",
            "the change range cannot be read without one",
        )
    owner, repo = repo_full.split("/", 1)
    try:
        token = await github_tokens.token_for_org(
            settings, str(worker["org_id"]), repo_full
        )
    except github.GitHubError as e:
        return _github_err(e)

    prev = (
        db.get_release(settings, str(release["previous_release_id"]))
        if release.get("previous_release_id")
        else None
    )

    first_release = prev is None
    try:
        if prev:
            compare = await github.compare_commits(
                token, owner, repo, prev["commit_sha"], release["commit_sha"]
            )
            commits = compare.get("commits") or []
            files = compare.get("files") or []
            # GitHub's compare caps `files` at 300 and says so on the payload.
            range_truncated = len(files) >= 300
        else:
            # No previous release: the range is the branch's own history, and
            # GitHub's compare API has nothing to compare against. The files
            # list is not available for that shape, so say what is known —
            # commits and work items — rather than inventing a file list.
            commits = await github.list_branch_commits(
                token, owner, repo, release["commit_sha"], limit=250
            )
            files = []
            range_truncated = len(commits) >= 250
    except github.GitHubError as e:
        return _github_err(e)

    prefix = (path_prefix or "").strip().lstrip("/")
    if prefix:
        files = [f for f in files if (f.get("filename") or "").startswith(prefix)]

    start = max(0, int(cursor or 0))
    page = files[start : start + RELEASE_FILES_PAGE]
    next_cursor = start + RELEASE_FILES_PAGE if start + RELEASE_FILES_PAGE < len(files) else None

    return {
        "version": release["version"],
        "commit_sha": release["commit_sha"],
        "previous_version": prev["version"] if prev else None,
        "previous_commit_sha": prev["commit_sha"] if prev else None,
        "first_release": first_release,
        "commits": [
            {
                "sha": c.get("sha"),
                "message": ((c.get("commit") or {}).get("message") or "")
                .strip()
                .splitlines()[:1],
                "author": ((c.get("commit") or {}).get("author") or {}).get("name"),
                "date": ((c.get("commit") or {}).get("author") or {}).get("date"),
            }
            for c in commits
        ],
        "commit_count": len(commits),
        "files": [
            {
                "path": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
            }
            for f in page
        ],
        "file_count": len(files),
        "work_items": release.get("included_items") or [],
        # Loud on purpose: an agent that receives a partial range and believes
        # it complete writes notes that claim coverage they do not have.
        "truncated": bool(next_cursor) or range_truncated,
        "cursor": next_cursor,
        "note": (
            "This is the project's FIRST release — there is no previous "
            "release to compare against, so the file list is unavailable and "
            "the commit history is capped. Say so in the notes."
            if first_release
            else (
                "The change range is larger than one page. Follow `cursor`, "
                "and if you stop early, say so in the notes."
                if (next_cursor or range_truncated)
                else ""
            )
        ),
    }


def _format_file_result(
    data: dict[str, Any] | list[Any], clean: str, use_ref: str
) -> dict[str, Any]:
    """Shared tail of read_repo_file / read_project_file once the raw
    GitHub content response is in hand: type/size/binary checks, decode."""
    import base64 as _b64

    if isinstance(data, list) or data.get("type") != "file":
        return _err(
            f"'{clean}' is not a file",
            "get_repo_tree lists files under a directory",
        )
    size = int(data.get("size") or 0)
    if size > repo_browse.MAX_FILE_BYTES:
        return _err(
            f"file is {size} bytes — above the "
            f"{repo_browse.MAX_FILE_BYTES}-byte cap",
            "read a targeted smaller file, or work from a full checkout "
            "via the factory git remote",
        )
    raw = _b64.b64decode(data.get("content") or "")
    if b"\x00" in raw:
        return _err(
            f"'{clean}' is binary — not served",
            "only text files are readable over MCP",
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return _err(
            f"'{clean}' is binary — not served",
            "only text files are readable over MCP",
        )
    return {
        "markdown": f"# `{clean}` @ `{use_ref}`\n\n````\n{text}\n````",
        "path": clean,
        "ref": use_ref,
        "size": size,
        "content": text,
    }


@mcp.tool(**_read("Read repository file"))
async def read_repo_file(
    run_id: str, path: str, ref: str = ""
) -> dict[str, Any]:
    """Read one file's text content from your claimed run's repository.
    ref defaults like get_repo_tree (work branch when it exists, else the
    default branch). Size-capped; binary files answer an explicit error
    instead of garbage."""
    run, token, err = await _held_run_and_token(run_id, "read_repo_file")
    if err:
        return err
    ic = run["_repo_ic"]
    repo_full = run["_repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    clean = path.strip().strip("/")
    if not clean:
        return _err("path is required", "get_repo_tree lists valid paths")
    settings = get_settings()
    try:
        use_ref = await repo_browse.resolve_ref(
            token, ic, str(run["issue_id"]), ref,
            run_branch=db.resolve_working_branch(settings, run)[0],
        )
        data = await github.get_content(token, owner, repo, clean, use_ref)
    except github.GitHubError as e:
        return _github_err(e)
    return _format_file_result(data, clean, use_ref)


@mcp.tool(**_read("Read a project's repository file with no claim"))
async def read_project_file(
    project_id: str = "", path: str = "", ref: str = ""
) -> dict[str, Any]:
    """Read one file's text content from a project's repository without
    claiming or holding any run. ref defaults to the project's default
    branch. Defaults project_id to the MCP url's scoped project. Size-
    capped; binary files answer an explicit error instead of garbage."""
    clean = path.strip().strip("/")
    if not clean:
        return _err("path is required", "get_project_tree lists valid paths")
    project, token, err = await _project_repo_and_token(
        project_id, "read_project_file"
    )
    if err:
        return err
    repo_full = project["repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    use_ref = ref.strip() or project["default_branch"] or "main"
    try:
        data = await github.get_content(token, owner, repo, clean, use_ref)
    except github.GitHubError as e:
        return _github_err(e)
    return _format_file_result(data, clean, use_ref)


async def _repo_ignore_context(
    token: str,
    repo_full: str,
    base_sha: str,
    files: list[dict[str, Any]],
) -> tuple[dict[str, str], set[str]]:
    """US-31.7: the repository's own ignore rules at `base_sha`, plus the set
    of paths it already tracks.

    Only the `.gitignore` files that could govern a path in this changeset are
    fetched — the root one, and one per ancestor directory of a changed path —
    so the guard costs a handful of reads rather than a walk of the repo.
    """
    import base64 as _b64

    owner, repo = repo_full.split("/", 1)
    tree = await github.get_tree(token, owner, repo, base_sha)
    entries = tree.get("tree") or []
    tracked = {
        e["path"] for e in entries if e.get("type") == "blob" and e.get("path")
    }
    # Directories that matter: every ancestor of every changed path.
    dirs: set[str] = {""}
    for f in files:
        path = str(f.get("path") or "").replace("\\", "/").strip("/")
        parts = path.split("/")[:-1]
        for i in range(len(parts)):
            dirs.add("/".join(parts[: i + 1]))
    wanted = {
        (f"{d}/.gitignore" if d else ".gitignore")
        for d in dirs
    } & tracked

    out: dict[str, str] = {}
    for path in sorted(wanted):
        got = await github.get_content(token, owner, repo, path, base_sha)
        raw = got.get("content") or ""
        if (got.get("encoding") or "") == "base64":
            try:
                out[path] = _b64.b64decode(raw).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 — unreadable rules are no rules
                continue
        else:
            out[path] = raw
    return out, tracked


async def _workspace_delta(
    token: str,
    owner: str,
    repo: str,
    since_sha: str,
    base_sha: str,
    held_paths: list[str],
) -> dict[str, Any] | None:
    """US-31.6: what changed between what the agent holds and the new base.

    Returns {add, update, delete, files} or None when a delta cannot be
    trusted — an unknown or rewritten `since_sha`, a rename GitHub reports in
    a shape we would have to guess at, or anything else ambiguous. None means
    the caller serves a full tree: a slow correct answer beats a fast one that
    leaves a deleted file on disk.
    """
    try:
        cmp = await github.compare_commits(token, owner, repo, since_sha, base_sha)
    except github.GitHubError:
        return None  # unrelated histories / force-push / unknown sha
    if cmp.get("status") not in ("identical", "ahead"):
        # behind or diverged: the agent holds something that is not an
        # ancestor of the new base. Re-establish from scratch.
        return None

    add: list[str] = []
    update: list[str] = []
    delete: list[str] = []
    files: list[dict[str, Any]] = []
    held = set(held_paths)

    for f in cmp.get("files") or []:
        path = f.get("filename") or ""
        status = f.get("status") or ""
        if not path:
            return None
        if status == "removed":
            delete.append(path)
            continue
        if status == "renamed":
            # Two operations, because a workspace with no .git cannot apply a
            # rename as one. The old path is named explicitly so it is
            # deleted rather than left behind as a duplicate.
            prev = f.get("previous_filename")
            if not prev:
                return None
            delete.append(prev)
            add.append(path)
        elif status == "added":
            add.append(path)
        elif status in ("modified", "changed"):
            (update if path in held else add).append(path)
        else:
            return None  # unknown status: do not guess
        files.append({"path": path, "status": status})
    return {"add": add, "update": update, "delete": delete, "files": files}


@mcp.tool(**_read("Get workspace snapshot"))
async def get_workspace(run_id: str) -> dict[str, Any]:
    """Get your claimed run's working tree — with zero git tooling and zero
    GitHub access.

    The factory remembers what it last served you for this project, so this
    answers one of two ways (`mode`):

    * `full` — the whole tree as a base64 zip in `zip_base64` (one top-level
      folder, no `.git`). The first call, or whenever a delta cannot be
      trusted.
    * `delta` — only what changed since then: `add`, `update` and, crucially,
      `delete`. **Apply the deletes.** A file removed upstream that stays on
      disk keeps compiling, and that is the whole reason this mode exists.
      Each entry in `files` carries its content, so no further fetch is
      needed.

    `base_sha` is the commit the answer is pinned to and what submit_changeset
    later declares as its base. Bases follow the get_repo_tree rules (work
    branch when it exists, else the default branch), so a continuing branch is
    never reset to main. Calling again is always safe.
    """
    import base64 as _b64

    run, token, err = await _held_run_and_token(run_id, "get_workspace")
    if err:
        return err
    settings = get_settings()
    ic = run["_repo_ic"]
    repo_full = run["_repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    cap = settings.workspace_zip_max_bytes
    worker = _worker()
    project_id = str(run.get("project_id") or "")
    try:
        base_ref = await repo_browse.resolve_ref(
            token, ic, str(run["issue_id"]), "",
            run_branch=db.resolve_working_branch(settings, run)[0],
        )
        commit = await github.get_commit(token, owner, repo, base_ref)
        base_sha = commit["sha"]
    except github.GitHubError as e:
        return _github_err(e)

    # US-31.6: try the incremental answer first.
    prior = (
        db.get_workspace_delivery(settings, str(worker["id"]), project_id)
        if project_id
        else None
    )
    if prior and prior["base_sha"]:
        delta = None
        if prior["base_sha"] == base_sha:
            # Nothing moved: an empty delta, not a whole tree.
            delta = {"add": [], "update": [], "delete": [], "files": []}
        else:
            delta = await _workspace_delta(
                token, owner, repo, prior["base_sha"], base_sha, prior["paths"]
            )
        if delta is not None:
            contents: list[dict[str, Any]] = []
            too_big = False
            total = 0
            try:
                for entry in delta["files"]:
                    got = await github.get_content(
                        token, owner, repo, entry["path"], base_sha
                    )
                    raw = got.get("content") or ""
                    total += len(raw)
                    if total > cap:
                        too_big = True
                        break
                    contents.append(
                        {
                            "path": entry["path"],
                            "op": "update" if entry["path"] in delta["update"] else "add",
                            "content_base64": raw.replace("\n", ""),
                            "encoding": "base64",
                        }
                    )
            except github.GitHubError:
                too_big = True  # fall back rather than serve a partial delta
            if not too_big:
                held = [
                    p for p in prior["paths"] if p not in set(delta["delete"])
                ] + [p for p in delta["add"] if p not in prior["paths"]]
                if project_id:
                    db.record_workspace_delivery(
                        settings,
                        str(worker["id"]),
                        project_id,
                        str(run["org_id"]),
                        base_sha,
                        sorted(set(held)),
                    )
                bc: dict[str, str] = {}
                if run["kind"] == "code":
                    bc = await build_config.fetch_build_config_values(
                        settings, str(run["org_id"]), project_id
                    )
                n = len(contents)
                return _next(
                    {
                        "markdown": (
                            f"# Workspace delta — `{repo_full}`\n\n"
                            f"- Base: `{base_ref}` @ `{base_sha}`\n"
                            f"- Since: `{prior['base_sha']}`\n"
                            f"- {n} file(s) to write, "
                            f"{len(delta['delete'])} to DELETE\n"
                            + (
                                "- Nothing changed since your last copy — "
                                "your workspace is already current.\n"
                                if not n and not delta["delete"]
                                else ""
                            )
                            + "\nApply the deletes as well as the writes: a "
                            "file removed upstream that stays on disk keeps "
                            "being compiled."
                        ),
                        "mode": "delta",
                        "base_sha": base_sha,
                        "base_ref": base_ref,
                        "since_sha": prior["base_sha"],
                        "files": contents,
                        "add": delta["add"],
                        "update": delta["update"],
                        "delete": delta["delete"],
                        "repo_full_name": repo_full,
                        "build_config": bc,
                    },
                    (
                        "submit_changeset",
                        f"apply the delta, do the work, then hand back your "
                        f"changed files declaring base_sha `{base_sha}`",
                    ),
                )

    try:
        data = await github.download_zipball(token, owner, repo, base_sha, cap)
    except github.GitHubError as e:
        return _github_err(e)
    if data is None:
        return _err(
            f"workspace archive exceeds the {cap}-byte ceiling",
            "large repos use the factory git remote instead — clone the "
            "remote from get_work_context with your worker token as the "
            "HTTP Basic password",
        )
    # US-31.6: remember the full tree we just served, so the NEXT call can be
    # a delta. The path list comes from the tree at this sha, not from the zip
    # — the zip is opaque here and the tree is one API call.
    if project_id:
        try:
            tree = await github.get_tree(token, owner, repo, base_sha)
            paths = [
                e["path"]
                for e in (tree.get("tree") or [])
                if e.get("type") == "blob" and e.get("path")
            ]
            if not tree.get("truncated"):
                db.record_workspace_delivery(
                    settings,
                    str(worker["id"]),
                    project_id,
                    str(run["org_id"]),
                    base_sha,
                    paths,
                )
        except github.GitHubError:
            # No manifest means the next call serves full again. Correct,
            # just not incremental — never a wrong delta.
            pass
    # US-7.9: build config travels with the workspace for a code run, so the
    # agent can write a .env before running the verify commands.
    bc_values: dict[str, str] = {}
    if run["kind"] == "code":
        bc_values = await build_config.fetch_build_config_values(
            settings, str(run["org_id"]), str(run.get("project_id") or "")
        )
    return _next(
        {
            "markdown": (
                f"# Workspace snapshot — `{repo_full}`\n\n"
                f"- Base: `{base_ref}` @ `{base_sha}`\n"
                f"- Size: {len(data)} bytes (zip, base64-encoded in "
                "`zip_base64`; one top-level folder, no `.git`)"
                + (
                    f"\n- Build config: {len(bc_values)} value(s) in "
                    "`build_config` — apply as env before verifying"
                    if bc_values
                    else ""
                )
            ),
            "mode": "full",
            "zip_base64": _b64.b64encode(data).decode(),
            "archive_format": "zip",
            "base_sha": base_sha,
            "base_ref": base_ref,
            "repo_full_name": repo_full,
            "size_bytes": len(data),
            "build_config": bc_values,
        },
        (
            "submit_changeset",
            f"extract, do the work, then hand back your changed files "
            f"declaring base_sha `{base_sha}`",
        ),
    )


@mcp.tool(**_read("Get a project's workspace with no claim"))
async def get_project_workspace(project_id: str = "", ref: str = "") -> dict[str, Any]:
    """Download a project's working tree as a zip — zero git tooling, zero
    GitHub access, and no run to claim first. For testing an agent's
    ability to check code out, exploring a project, or working outside the
    claim/work-item flow entirely.

    Always a full zip (`zip_base64`, one top-level folder, no `.git`) at
    `base_sha` — never a delta, so it never disturbs the incremental cache
    a real claimed run's get_workspace relies on. ref defaults to the
    project's default branch. There is nothing to submit_changeset back to
    — this is read-only; claim_work a run first to hand code back.
    """
    import base64 as _b64

    project, token, err = await _project_repo_and_token(
        project_id, "get_project_workspace"
    )
    if err:
        return err
    settings = get_settings()
    repo_full = project["repo_full_name"]
    owner, repo = repo_full.split("/", 1)
    cap = settings.workspace_zip_max_bytes
    use_ref = ref.strip() or project["default_branch"] or "main"
    try:
        commit = await github.get_commit(token, owner, repo, use_ref)
        base_sha = commit["sha"]
        data = await github.download_zipball(token, owner, repo, base_sha, cap)
    except github.GitHubError as e:
        return _github_err(e)
    if data is None:
        return _err(
            f"workspace archive exceeds the {cap}-byte ceiling",
            "large repos use the factory git remote instead — clone it "
            "with this worker's token as the HTTP Basic password",
        )
    return {
        "markdown": (
            f"# Workspace snapshot — `{repo_full}` (no claim)\n\n"
            f"- Ref: `{use_ref}` @ `{base_sha}`\n"
            f"- Size: {len(data)} bytes (zip, base64-encoded in "
            "`zip_base64`; one top-level folder, no `.git`)\n\n"
            "Read-only — there is no run to hand this back to. "
            "claim_work first if you want to submit changes."
        ),
        "mode": "full",
        "zip_base64": _b64.b64encode(data).decode(),
        "archive_format": "zip",
        "base_sha": base_sha,
        "ref": use_ref,
        "repo_full_name": repo_full,
        "size_bytes": len(data),
    }


@mcp.tool(**_read("Validate a submission"))
async def validate_submission(
    run_id: str,
    prd: str = "",
    plan: str = "",
    test_plan: str = "",
    stories: list[dict[str, Any]] | None = None,
    branch_ref: str = "",
    base_sha: str = "",
    message: str = "",
    files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Dry-run your hand-back against the gate's structural expectations
    before submitting — the same checks the submit tools run and return
    as warnings. Pass the artifact matching your claimed run's kind:
    prd (four required sections), plan + test_plan (the test plan must
    parse into ≥1 structured case — the exact parser approval uses), or
    for code runs one transport: branch_ref (git-native — exists on
    GitHub and is ahead of the default branch) OR base_sha + message +
    files (git-free — the exact submit_changeset checks: file caps,
    path sanitization, op/content consistency, plus a read-only base
    freshness check that reports the current head when stale). Findings
    are fixable feedback, never a rejection; nothing touches GitHub
    refs."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    db.extend_claim(settings, run_id, str(worker["id"]), tool="validate_submission")

    kind = run["kind"]
    current_head: str | None = None
    if kind == "release":
        # US-13.12: a release run carries no changeset — its deliverable
        # is the notes document (title = the computed version) and,
        # where a UAT branch applies, the factory-opened promotion PR.
        version = (run.get("input_context") or {}).get("next_version") or ""
        return {
            "ok": True,
            "findings": [],
            "markdown": (
                "# Validation findings\n\n- release runs carry no "
                f"changeset. Title the notes with **{version}** and hand "
                "back with submit_release_run(notes_markdown, "
                "open_promotion_pr)."
            ),
        }
    if kind == "test":
        # US-13.11: a test run carries no changeset — its deliverable is
        # per-case results plus a summary.
        reported = db.count_run_test_results(settings, run_id)
        findings = (
            []
            if reported
            else [
                "no test results reported yet — report_test_results for "
                "the cases you executed before submit_test_run, or "
                "release_work with a note if you could not execute anything"
            ]
        )
        return {
            "ok": not findings,
            "findings": findings,
            "markdown": (
                "# Validation findings\n\n"
                + (
                    f"- {reported} per-case result(s) reported — finish "
                    "with submit_test_run(summary)"
                    if reported
                    else "- " + findings[0]
                )
            ),
        }
    if kind == "prd":
        if not prd.strip():
            return _err(
                "nothing to validate", "pass prd= for this prd run"
            )
        findings = validation.validate_prd(prd)
    elif kind == "plan":
        if not plan.strip() and not test_plan.strip():
            return _err(
                "nothing to validate",
                "pass plan= (and test_plan=) for this plan run",
            )
        findings = validation.validate_plan(plan, test_plan)
    elif kind == "breakdown":
        # US-2.33: at least one story, each with a non-empty title.
        findings = validation.validate_stories(stories)
    elif files is not None or base_sha.strip() or message.strip():
        # US-5.29: git-free dry run — the same checks submit_changeset
        # runs, surfaced before the submit instead of as its rejection.
        if branch_ref.strip():
            return {
                "markdown": (
                    "# Validation findings\n\n- both branch_ref and a "
                    "changeset were passed — validate one transport per "
                    "call (branch_ref for git-native, files for git-free)"
                ),
                "ok": False,
                "findings": [
                    "both branch_ref and a changeset were passed — "
                    "validate one transport per call (branch_ref for "
                    "git-native, files for git-free)"
                ],
            }
        # Shared code path with submit_changeset — same function, same
        # findings, same ordering (base_sha first, then message).
        # us-96.8: the same scratch filter the real submit applies, echoed
        # here so the dry run and the submit can never disagree about it.
        kept_files, dropped_scratch = changesets.split_scratch(files or [])
        if dropped_scratch:
            findings_note = (
                "factory scratch is dropped, not committed: "
                + ", ".join(dropped_scratch)
            )
        else:
            findings_note = None
        files = kept_files
        findings = changesets.validate_changeset(files or [])
        if findings_note:
            findings = [findings_note] + findings
        if not (message or "").strip():
            findings = ["commit message is empty"] + findings
        if not (base_sha or "").strip():
            findings = [
                "base_sha is required — get_workspace answers it"
            ] + findings
        elif not findings:
            # Structurally clean — the read-only base freshness check
            # (submit only reaches GitHub once structure passes; the dry
            # run mirrors that).
            ic = run.get("input_context") or {}
            repo_full = ic.get("repo_full_name") or ""
            if "/" not in repo_full:
                return _err("run has no linked repo", "")
            owner, repo = repo_full.split("/", 1)
            # US-7.3: validate against the strategy-resolved branch.
            branch, _dev_strategy, _submit_mode = db.resolve_working_branch(
                settings, run
            )
            try:
                token = await github_tokens.token_for_org(
                    settings, str(run["org_id"]), repo_full
                )
                try:
                    await github.get_commit(
                        token, owner, repo, base_sha.strip()
                    )
                except github.GitHubError as e:
                    if "not found" in e.message:
                        findings.append(
                            f"base_sha '{base_sha.strip()}' is not a "
                            "commit in this repo — get_workspace answers "
                            "the current base"
                        )
                    else:
                        raise
                ref = await github.get_ref(token, owner, repo, branch)
                if ref is not None:
                    head = ref["object"]["sha"]
                    if head != base_sha.strip():
                        current_head = head
                        findings.append(
                            f"stale base: the branch head is {head}, not "
                            f"{base_sha.strip()} — refetch with "
                            "get_workspace and reapply"
                        )
            except github.GitHubError as e:
                return _github_err(e)
    else:
        if not branch_ref.strip():
            return _err(
                "nothing to validate",
                "pass branch_ref= (git-native) or base_sha=, message=, "
                "files= (git-free) for this code run",
            )
        ic = run.get("input_context") or {}
        repo_full = ic.get("repo_full_name") or ""
        if "/" not in repo_full:
            return _err("run has no linked repo", "")
        try:
            token = await github_tokens.token_for_org(
                settings, str(run["org_id"]), repo_full
            )
        except github.GitHubError as e:
            return _github_err(e)
        findings = await validation.validate_code_branch(
            token,
            repo_full,
            ic.get("default_branch") or "main",
            branch_ref.strip(),
        )
    # US-81.6: advisory, never a finding — the soft-gate philosophy (US-2.6)
    # stands. Missing test evidence and test-less code changes are worth
    # saying out loud, not worth flipping ok to False over.
    advisories: list[str] = []
    if kind == "code":
        if not run.get("test_evidence"):
            advisories.append(
                "no test evidence reported — run the project's pre-submit "
                "test command in your workspace and report_test_evidence "
                "before submitting, so the review shows tests ran"
            )
        if files:
            paths = [str((f or {}).get("path") or "").lower() for f in files]
            touches_tests = any(
                "test" in p or "spec" in p for p in paths
            )
            if paths and not touches_tests:
                advisories.append(
                    "this change touches no test or spec files — new "
                    "behavior should carry tests (writing them is part of "
                    "the work)"
                )
    if findings:
        md = "# Validation findings\n\n" + "\n".join(
            f"- {f}" for f in findings
        )
    else:
        md = "Structurally sound — the gate's parsers accept this as-is."
    if advisories:
        md += "\n\n## Advisories (do not block)\n\n" + "\n".join(
            f"- {a}" for a in advisories
        )
    out = {"markdown": md, "ok": not findings, "findings": findings}
    if advisories:
        out["advisories"] = advisories
    if current_head:
        out["current_head"] = current_head
    return out


@mcp.tool(**_read("Get project guidelines"))
async def get_project_guidelines(project_id: str = "") -> dict[str, Any]:
    """Fetch a project's application guidelines — the same assembled
    markdown committed to AGENTS.md (conventions, architecture, gotchas).
    Use this to learn how a project works, any time, not just while
    holding a claim. Defaults to the project this MCP url is scoped to;
    pass project_id explicitly when connected via an org-wide url."""
    settings = get_settings()
    worker = _worker()
    pid = project_id or _scoped_project.get()
    if not pid:
        return _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    project = db.get_project_guidelines_md(settings, pid, str(worker["org_id"]))
    if project is None:
        return _err(
            "project not found",
            "check the project_id, or list_available_work to find one",
        )
    guidelines = project["guidelines"] or ""
    if guidelines.strip():
        md = f"# {project['name']} — guidelines\n\n{guidelines}"
    else:
        md = f"{project['name']} has no guidelines configured yet."
    return {"markdown": md, "guidelines": guidelines, "project": project["name"]}


# US-5.8: markdown/text documents only over MCP — binary files can come
# later; work-item attachments have the claimed-run byte-fetch already.
_TEXT_DOCUMENT_MIMES = {"application/json"}


def _is_text_document(mime: str) -> bool:
    return mime.startswith("text/") or mime in _TEXT_DOCUMENT_MIMES


@mcp.tool(**_read("List project documents"))
async def list_project_documents(project_id: str = "") -> dict[str, Any]:
    """List a project's documents — design docs, PRD material, work-item
    attachments — with ids to read via get_document. Documents linked to a
    work item you currently hold a claim on are marked. Defaults to the
    project this MCP url is scoped to; pass project_id explicitly when
    connected via an org-wide url."""
    settings = get_settings()
    worker = _worker()
    pid = project_id or _scoped_project.get()
    if not pid:
        return _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    docs = db.list_project_documents(
        settings, pid, str(worker["org_id"]), str(worker["id"])
    )
    if docs is None:
        return _err(
            "project not found",
            "check the project_id, or list_available_work to find one",
        )
    items = [
        {
            "document_id": str(d["id"]),
            "name": d["name"],
            "kind": d["attached_to"],
            "mime_type": d["mime_type"],
            "source": d["source"],
            "updated_at": str(d["updated_at"]),
            "linked_to_your_claim": bool(d["linked_to_claim"]),
        }
        for d in docs
    ]
    if items:
        md = (
            "Project documents (fetch content with get_document):\n"
            + "\n".join(
                f"- **{i['name']}** (`{i['document_id']}`) — {i['kind']}, "
                f"updated {i['updated_at']}"
                + (
                    " — linked to a work item you hold"
                    if i["linked_to_your_claim"]
                    else ""
                )
                for i in items
            )
        )
    else:
        md = "No documents in this project yet."
    return {"markdown": md, "documents": items}


@mcp.tool(**_read("Read project document"))
async def get_document(document_id: str) -> dict[str, Any]:
    """Read one project document's content — the underlying design
    material when the frozen work-context snapshot isn't enough. Markdown
    and text documents only; list_project_documents shows the ids."""
    settings = get_settings()
    worker = _worker()
    doc = documents.get_document(settings, document_id)
    if not doc or str(doc["org_id"]) != str(worker["org_id"]):
        return _err(
            "document not found", "list_project_documents shows valid ids"
        )
    mime = doc["mime_type"] or ""
    if not _is_text_document(mime):
        return _err(
            f"'{doc['name']}' is {mime or 'binary'} — only markdown/text "
            "documents are served over MCP",
            "work-item attachments are byte-fetchable on a claimed run via "
            "the worker documents endpoint",
        )
    data = await documents.read_bytes(settings, doc)
    if data is None:
        return _err(
            "document not found", "list_project_documents shows valid ids"
        )
    text = data.decode("utf-8", errors="replace")
    return {
        "markdown": f"# {doc['name']}\n\n{text}",
        "document_id": str(doc["id"]),
        "name": doc["name"],
        "mime_type": mime,
        "content": text,
    }


@mcp.tool(**_read("Get project learnings"))
async def get_project_learnings(project_id: str = "") -> dict[str, Any]:
    """Fetch a project's accumulated learnings — the gotchas and
    discoveries earlier runs left behind (US-1.21's living lessons-learned
    document). Read it any time, not just while holding a claim, so you
    benefit from what previous workers learned before repeating their
    mistakes. Defaults to the project this MCP url is scoped to; pass
    project_id explicitly when connected via an org-wide url."""
    settings = get_settings()
    worker = _worker()
    pid = project_id or _scoped_project.get()
    if not pid:
        return _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    project = db.get_project_learnings_md(settings, pid, str(worker["org_id"]))
    if project is None:
        return _err(
            "project not found",
            "check the project_id, or list_available_work to find one",
        )
    learnings = project["learnings"] or ""
    if learnings.strip():
        md = f"# {project['name']} — learnings\n\n{learnings}"
    else:
        md = f"{project['name']} has no learnings recorded yet."
    return {"markdown": md, "learnings": learnings, "project": project["name"]}


@mcp.tool(**_read("Get the project environment"))
async def get_environment(run_id: str = "", project_id: str = "") -> dict[str, Any]:
    """US-89.2: "what access do I have?" — the project's defined environment.

    Every entry's name, kind (plain | secret), scope and description, plus
    the factory built-ins (MCP, git remote, LLM gateway) you already hold.
    Pass the run_id you claimed to ALSO receive the secret values — they are
    disclosed only to the worker holding a claim on that project, and the
    same values are already present in your process environment. Never echo
    a secret value into a note, a commit, or a file: the changeset guard
    refuses files containing one.
    """
    settings = get_settings()
    worker = _worker()

    run = None
    if run_id:
        run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
        if not run:
            return _err("run not found", "list_available_work shows valid run ids")
        if str(run.get("worker_id") or "") != str(worker["id"]):
            return _err("you do not hold this run", "claim_work it first")
        pid = str(run.get("project_id") or "")
    else:
        pid = project_id or _scoped_project.get() or ""
    if not pid:
        return _err(
            "no project specified",
            "pass run_id (for values) or project_id (for the catalog)",
        )

    values, catalog = await project_env.effective_env(
        settings, pid, str(worker["id"])
    )
    include_values = run is not None and str(run.get("project_id") or "") == pid

    lines = [
        "# Project environment",
        "",
        "Built-ins (always yours on this project): the factory MCP server, "
        "the factory git remote (credential handled for you), and the LLM "
        "gateway.",
        "",
    ]
    if not catalog:
        lines.append("_No environment entries are defined for this project._")
    for c in sorted(catalog, key=lambda x: x["name"]):
        marker = "secret" if c["kind"] == "secret" else "plain"
        state = "" if c["set"] else " — NOT SET"
        desc = f" — {c['description']}" if c["description"] else ""
        shown = ""
        if include_values and c["set"] and c["name"] in values:
            shown = (
                f" = `{values[c['name']]}`"
                if c["kind"] == "plain"
                else " (value in your process env and in `values` below)"
            )
        lines.append(f"- `{c['name']}` ({marker}, {c['scope']}){state}{desc}{shown}")

    out: dict[str, Any] = {
        "markdown": "\n".join(lines),
        "entries": catalog,
        "builtins": ["factory-mcp", "factory-git", "llm-gateway"],
    }
    if include_values:
        out["values"] = values
    return out


# US-5.6: a sanity cap, not a quota — a learning is a distilled note, not
# a transcript dump.
_MAX_LEARNING_CHARS = 4000


@mcp.tool(**_write("Submit a learning"))
async def submit_learning(text: str, project_id: str = "") -> dict[str, Any]:
    """Contribute a discovery to the project's learnings ("the build fails
    unless X", "this module is deprecated, use Y") so the next run starts
    smarter. US-5.31: submissions queue for the manager's review — the
    curated LLM merge runs at approval, so get_project_learnings shows
    your contribution only after the manager approves it. Same project
    scoping as get_project_guidelines."""
    settings = get_settings()
    worker = _worker()
    if not (text or "").strip():
        return _err(
            "text is required",
            "describe the discovery — what surprised you and what to do "
            "instead",
        )
    if len(text) > _MAX_LEARNING_CHARS:
        return _err(
            f"learning is too long (over {_MAX_LEARNING_CHARS} characters)",
            "distill it to the durable point — the learnings document is "
            "curated, not a transcript",
        )
    pid = project_id or _scoped_project.get()
    if not pid:
        return _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    project = db.get_project_learnings_md(settings, pid, str(worker["org_id"]))
    if project is None:
        return _err(
            "project not found",
            "check the project_id, or list_available_work to find one",
        )
    # US-5.31: no merge at submit time — the manager gates on the
    # Learnings tab, and the curated LLM merge runs at approval.
    submission_id = db.record_learning_submission(
        settings, worker, str(worker["org_id"]), pid, text.strip()
    )
    return {
        "markdown": (
            "Learning queued for the manager's review — it reaches the "
            "curated document (and future run contexts) once approved on "
            "the project's Learnings page. Read the curated document any "
            "time with get_project_learnings."
        ),
        "project": project["name"],
        "submission_id": submission_id,
        "status": "pending",
        "ok": True,
    }


# US-5.32: agent-declared severity, advisory only — the manager's
# judgment is the gate; nothing auto-applies at any level.
_SEVERITY_DEFINITIONS = {
    "trivial": "wording or typo fix, no behavior change",
    "minor": "clarification or small addition",
    "major": "incorrect or missing guidance that affects how work gets done",
    "severe": "actively harmful or blocking instruction",
}

_MAX_RECOMMENDATION_CHARS = 8000


def _as_string_list(value: Any) -> list[str]:
    """US-42.1: accept the shapes agents actually send.

    `AgentTestCase.steps` was a `str` and the agents wrote a list; every one
    of fifteen runs had its hand-back refused with a 422, and because a
    body-validation error discards the whole payload each refusal cost a full
    re-submit. The lesson generalises: coerce a field's shape, never reject
    the payload over it. A single string becomes a one-element list (split on
    newlines when it is obviously a list typed as prose); anything else is
    stringified per element and blanks are dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        lines = [
            line.strip().lstrip("-*").strip()
            for line in text.splitlines()
            if line.strip()
        ]
        return lines if len(lines) > 1 else [text]
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            text = (item if isinstance(item, str) else str(item)).strip()
            if text:
                out.append(text)
        return out
    text = str(value).strip()
    return [text] if text else []

# US-43.1: the guidelines catalog, mirroring
# apps/web/src/lib/project-guidelines-catalog.ts. A refresh may target a
# catalog key the project has not filled in yet — that is most of the point —
# so "does this section exist" is not the test for a legal section_key.
# Duplicated rather than imported because the API does not read the web app's
# source; the enumeration test in tests/test_guidelines_refresh.py is what
# keeps the two honest.
_CATALOG_SECTION_KEYS = {
    "overview",
    "tech-stack",
    "commands",
    "run-commands",
    "code-style",
    "things-to-avoid",
    "architecture",
    "file-structure",
    "testing",
    "environment",
    "git-pr",
    "monorepo",
    "doc-links",
    "known-issues",
    "boundaries",
    "preferred-libs",
    "good-patterns",
    "agent-workflows",
    "release",
    "deployment",
    "buildmill-workflow",
}


def _proposed_section_title(proposed_text: str) -> str:
    """A display title for a proposed NEW section: its first markdown
    heading, else a generic label the manager can rename."""
    for line in proposed_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:80]
    return "Agent-recommended section"


@mcp.tool(**_write("Recommend a guideline change"))
async def recommend_guideline_change(
    proposed_text: str,
    rationale: str,
    severity: str,
    section_key: str = "",
    project_id: str = "",
) -> dict[str, Any]:
    """Propose a change to the project's guidelines instead of silently
    working around wrong or stale guidance. The manager reviews it in
    Things to Do, graded by your declared severity — trivial: wording or
    typo fix, no behavior change; minor: clarification or small
    addition; major: incorrect or missing guidance that affects how work
    gets done; severe: actively harmful or blocking instruction.
    proposed_text is the section's full replacement text; pass the
    section_key it targets, or leave section_key empty to propose a NEW
    section. No claim required — guidelines are project-wide. Severity
    is advisory: nothing auto-applies; the manager decides."""
    settings = get_settings()
    worker = _worker()
    if severity not in _SEVERITY_DEFINITIONS:
        return _err(
            f"unknown severity {severity!r}",
            "use one of: "
            + "; ".join(
                f"{k} = {v}" for k, v in _SEVERITY_DEFINITIONS.items()
            ),
        )
    if not (proposed_text or "").strip():
        return _err(
            "proposed_text is required",
            "send the section's full proposed replacement text",
        )
    if not (rationale or "").strip():
        return _err(
            "rationale is required",
            "say what's wrong today and why the change helps",
        )
    if len(proposed_text) > _MAX_RECOMMENDATION_CHARS:
        return _err(
            f"proposed_text is too long (over "
            f"{_MAX_RECOMMENDATION_CHARS} characters)",
            "propose one section's text, not a whole document",
        )
    pid = project_id or _scoped_project.get()
    if not pid:
        return _err(
            "no project specified",
            "pass project_id, or connect via a project-scoped MCP url "
            "(/mcp/<org-shortname>/<project-slug>)",
        )
    project = db.get_project_guidelines_md(settings, pid, str(worker["org_id"]))
    if project is None:
        return _err(
            "project not found",
            "check the project_id, or list_available_work to find one",
        )
    section = None
    key = (section_key or "").strip()
    if key:
        section = db.get_guideline_section(settings, pid, key)
        if section is None:
            keys = db.list_guideline_section_keys(settings, pid)
            return _err(
                f"no guideline section '{key}' on this project",
                (
                    "existing section keys: " + ", ".join(keys) + " — "
                    if keys
                    else ""
                )
                + "or pass section_key='' to propose a new section",
            )
    title = (
        section["title"] if section else _proposed_section_title(proposed_text)
    )
    result = db.record_guideline_recommendation(
        settings,
        worker,
        str(worker["org_id"]),
        pid,
        section,
        key,
        title,
        severity,
        proposed_text.strip(),
        rationale.strip(),
    )
    definitions = "; ".join(
        f"{k} = {v}" for k, v in _SEVERITY_DEFINITIONS.items()
    )
    if result["duplicate"]:
        md = (
            "You already have an identical pending recommendation for "
            "this section — answering it instead of queuing a duplicate. "
            "The manager sees it in Things to Do."
        )
    else:
        target = (
            f"section '{title}'" if section else f"a new section '{title}'"
        )
        md = (
            f"Recommendation queued for {target} at severity "
            f"**{severity}** — the manager reviews it in Things to Do, "
            "sorted by severity. Severity definitions to self-calibrate: "
            f"{definitions}."
        )
    return {
        "markdown": md,
        "recommendation_id": result["id"],
        "status": "pending",
        "severity": severity,
        "section_title": title,
        "new_section": section is None,
        "duplicate": result["duplicate"],
        "severity_definitions": _SEVERITY_DEFINITIONS,
        "ok": True,
    }


def _wireframe_brief(settings, issue_id: str, display_id: str | None) -> str:
    """One story's drawn screens, as a few lines of markdown. Empty when the
    story has no wireframe or was answered `no UI surface` — in which case
    there is nothing for a plan to be consistent with, and saying so would be
    noise on every backend story."""
    artifact = db.get_current_wireframe(settings, issue_id)
    if not artifact:
        return ""
    declaration = wireframe_docs.declaration_of(artifact)
    if declaration.get("no_ui_surface"):
        return ""
    screens = wireframes.declared_screens(declaration)
    if not screens:
        return ""
    lines = []
    for screen in screens:
        head = f"- **{screen['name']}**"
        if screen.get("route"):
            head += f" (`{screen['route']}`)"
        lines.append(head)
        detail = []
        if screen.get("states"):
            detail.append("states: " + ", ".join(screen["states"]))
        if screen.get("components"):
            detail.append("components: " + ", ".join(screen["components"]))
        if screen.get("acceptance_criteria"):
            detail.append("covers AC " + ", ".join(screen["acceptance_criteria"]))
        if detail:
            lines.append("  - " + " · ".join(detail))
        if screen.get("note"):
            lines.append(f"  - note: {screen['note']}")
    path = wireframes.page_path(display_id) if display_id else None
    tail = (
        f"\n\nThe rendered wireframe is at `{path}` — already in your "
        "workspace on a code run, and readable with read_repo_file on a plan "
        "run. Read it before deciding what the surface is."
        if path
        else ""
    )
    return "\n".join(lines) + tail


def _wireframe_section(settings, run: dict[str, Any], stories: Any) -> str:
    """The Wireframe section of a plan or code brief — one story's screens, or
    one block per story on a feature-level code run."""
    if run["kind"] not in ("plan", "code"):
        return ""
    blocks: list[str] = []
    if stories:
        for s in stories:
            brief = _wireframe_brief(
                settings, str(s.get("issue_id") or ""), s.get("display_id")
            )
            if brief:
                blocks.append(f"### {s.get('display_id')}\n\n{brief}")
    else:
        brief = _wireframe_brief(
            settings,
            str(run.get("issue_id") or ""),
            db.work_item_display_id(
                run.get("issue_type"),
                run.get("epic_number"),
                run.get("item_no"),
                run.get("sub_no"),
            ),
        )
        if brief:
            blocks.append(brief)
    if not blocks:
        return ""
    lead = (
        "This story was drawn before it was planned. Your **Surfaces "
        "touched** must be consistent with these screens; if you think the "
        "screen is wrong, say so under **Risks** rather than quietly "
        "designing around it."
        if run["kind"] == "plan"
        else "This story was drawn before it was planned. Build to it, and "
        "name any departure in your hand-back notes."
    )
    return "\n\n## Wireframe\n\n" + lead + "\n\n" + "\n\n".join(blocks)


def _as_declaration(value: Any) -> tuple[dict[str, Any], list[str]]:
    """US-42.1 applied to a wireframe declaration: `(declaration, findings)`.

    Every shape an agent plausibly sends is coerced rather than refused —
    a JSON string instead of an object, a bare list of screens instead of
    `{"screens": [...]}`, one screen instead of a list. Findings are reported
    back as advice; none of them is a rejection. A hand-back refused over
    shape costs a full re-run, and the agent has already done the thinking."""
    findings: list[str] = []
    if value is None:
        # Not a shape complaint: an omitted declaration is how the no-UI
        # verdict arrives, and saying "not an object" about it would put a
        # finding on the one hand-back that did nothing wrong.
        return {}, findings
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}, findings
        try:
            value = json.loads(text)
            findings.append("the declaration arrived as a JSON string; parsed it")
        except ValueError:
            return {}, ["the declaration was a string but not valid JSON"]
    if isinstance(value, list):
        value = {"screens": value}
        findings.append("a bare list of screens was wrapped into {\"screens\": …}")
    if not isinstance(value, dict):
        return {}, ["the declaration was not an object"]

    doc = dict(value)
    screens = doc.get("screens")
    if isinstance(screens, dict):
        doc["screens"] = [screens]
        findings.append("one screen was given as an object; wrapped it in a list")
    elif screens is None and ("regions" in doc or "name" in doc):
        # The agent declared a single screen at the top level.
        doc = {"screens": [doc]}
        findings.append("a single top-level screen was wrapped into \"screens\"")
    elif not isinstance(screens, list):
        doc["screens"] = []
        findings.append("\"screens\" was not a list; treated as empty")

    known = set(wireframes.COMPONENTS)
    unknown: set[str] = set()

    def scan(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                scan(item)
            return
        if not isinstance(node, dict):
            return
        name = node.get("component") or node.get("type")
        if isinstance(name, str) and name and name not in known:
            unknown.add(name)
        for item in node.values():
            if isinstance(item, (dict, list)):
                scan(item)

    scan(doc.get("screens") or [])
    if unknown:
        # Advice, not a refusal: kit.js maps the common near-misses and renders
        # a visible error for the rest, so the manager sees the mistake on the
        # page rather than the run failing over it.
        findings.append(
            "components the kit does not know: "
            + ", ".join(sorted(unknown))
            + " — it will alias what it can and show an error for the rest"
        )
    return doc, findings


@mcp.tool(**_write("Submit wireframe"))
async def submit_wireframe(
    run_id: str,
    declaration: Any = None,
    no_ui_surface: bool = False,
    reason: str = "",
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Complete a claimed wireframe run by handing back the screen you drew.

    `declaration` is the JSON the wireframe kit renders — see the run's
    instructions for the component vocabulary. Do not send HTML or CSS.

    `no_ui_surface=True` with a `reason` is the right answer for a story that
    changes nothing a user sees (a migration, a capability gate, a metering
    fix). It completes the run successfully and writes no file. Do NOT invent
    a screen to avoid it.

    There is no approval gate: what you hand back is what the manager reads
    and what the repository gets. One call per run; this completes it."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    if run["kind"] != "wireframe":
        return _err(
            f"this is a {run['kind']} run — submit_wireframe completes "
            "wireframe runs only",
            "use the submit tool matching the run's kind",
        )

    doc, findings = _as_declaration(declaration)
    screens = doc.get("screens") or []

    if no_ui_surface:
        stored = {
            "no_ui_surface": True,
            "reason": (reason or "").strip()
            or "the agent reported no user-visible surface and gave no reason",
        }
    elif not screens:
        # An empty hand-back that did not claim "no UI surface" is almost
        # always a shape problem, not a verdict. Record it as the verdict it
        # effectively is, say so plainly, and let the manager redo it — which
        # is cheaper than failing the run and losing the context.
        stored = {
            "no_ui_surface": True,
            "reason": (reason or "").strip()
            or "no screens were declared and no reason was given",
            "empty_submission": True,
        }
        findings.append(
            "no screens were declared, so this was recorded as 'no UI surface'"
        )
    else:
        stored = doc

    result = db.record_wireframe(settings, run, stored)
    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        None,
        None,
        None,
        None,
        None,
        worker_name=worker["name"],
        trigger="submit",
    )
    if not ok:
        return _err(
            "run is not claimed — claim it and retry",
            "list_my_work shows what you hold",
        )

    # Best-effort, exactly as US-22.7's instruction write is: the artifact is
    # stored and the run is complete either way, and US-48.5's sync is the
    # retry. A repository problem must never cost the drawing.
    written = await wireframe_docs.write_wireframe(
        settings, str(run["issue_id"]), trigger="hand-back"
    )

    from .routers.worker import _store_handback_notes

    if stored.get("no_ui_surface"):
        combined = f"No UI surface: {stored['reason']}"
    else:
        combined = f"Drew {wireframes.summarize(stored)}."
    if (notes_for_manager or "").strip():
        combined += "\n\n" + notes_for_manager.strip()
    _store_handback_notes(settings, run, worker, run_id, combined)

    return {
        "markdown": (
            "Recorded that this story has no user-visible surface. Nothing "
            "was written to the repository, and the manager sees that as the "
            "answer."
            if stored.get("no_ui_surface")
            else "Wireframe handed back. It is live immediately — there is no "
            "approval gate — and it is what the plan and code runs will "
            "build to."
        ),
        "ok": True,
        "artifact_id": result["id"],
        "version": result["version"],
        "no_ui_surface": bool(stored.get("no_ui_surface")),
        "summary": wireframes.summarize(stored),
        "repository": written,
        "findings": findings,
    }


@mcp.tool(**_write("Submit elaboration"))
async def submit_elaboration(
    run_id: str,
    story: str = "",
    acceptance_criteria: Any = None,
    open_questions: Any = None,
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Complete a claimed elaborate run by proposing a rewritten story.
    `story` is the full replacement body; `acceptance_criteria` the full
    replacement list, one independently checkable outcome per entry.
    `open_questions` is anything you could not settle from the repository —
    put it here rather than guessing inside the text.

    Proposing NOTHING is a legal, useful answer: leave `story` and
    `acceptance_criteria` empty and the run records that the story reads
    fine as written. Do not invent a rewrite to avoid an empty submit.

    Nothing is applied — the manager reads your proposal beside the current
    text and decides. One call per run; this completes it."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    if run["kind"] != "elaborate":
        return _err(
            f"this is a {run['kind']} run — submit_elaboration completes "
            "elaborate runs only",
            "use the submit tool matching the run's kind",
        )

    # US-42.1: a hand-back is not refused over a field's shape. Fifteen runs
    # each paid a full re-submit because a body-validation error discards the
    # WHOLE payload — so a criteria list given as one string is coerced, not
    # rejected, and the same for open_questions.
    criteria = _as_string_list(acceptance_criteria)
    questions = _as_string_list(open_questions)
    body = (story or "").strip()
    proposes_change = bool(body or criteria)

    result = db.record_elaboration(
        settings, run, body, criteria, questions, proposes_change
    )
    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        None,
        None,
        None,
        None,
        None,
        worker_name=worker["name"],
        trigger="submit",
    )
    if not ok:
        return _err(
            "run is not claimed — claim it and retry",
            "list_my_work shows what you hold",
        )
    from .routers.worker import _store_handback_notes

    combined = (
        f"Proposed a rewrite with {len(criteria)} acceptance criteria."
        if proposes_change
        else "Read the story against the repository and proposed no change."
    )
    if questions:
        combined += "\n\nOpen questions:\n" + "\n".join(f"- {q}" for q in questions)
    if (notes_for_manager or "").strip():
        combined += "\n\n" + notes_for_manager.strip()
    _store_handback_notes(settings, run, worker, run_id, combined)

    return {
        "markdown": (
            "Proposal handed back — the manager reviews it beside the "
            "current story text and decides. Nothing is applied until they do."
            if proposes_change
            else "Recorded that this story reads fine as written. Nothing is "
            "proposed, and the manager sees that as the answer."
        ),
        "ok": True,
        "artifact_id": result["id"],
        "version": result["version"],
        "proposes_change": proposes_change,
        "acceptance_criteria_count": len(criteria),
        "open_questions": questions,
    }


@mcp.tool(**_write("Submit guidelines refresh"))
async def submit_guidelines_refresh(
    run_id: str,
    summary: str,
    sections: list[dict[str, Any]],
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Complete a claimed guidelines run by handing back the WHOLE pass at
    once. sections is a list of {section_key, title, proposed_text,
    rationale, severity} — one entry per section you are proposing. Use the
    catalog key for a section that exists (or should); leave section_key
    empty to propose an entirely new one, and give it a title. severity is
    trivial/minor/major/severe, same definitions as
    recommend_guideline_change. summary is the one line the manager reads
    before opening the bundle.

    An EMPTY sections list is a legal answer — "I read the repository and
    have nothing to propose" is worth saying, and it closes the chore. Do not
    invent sections to avoid it.

    Nothing you send here is applied: the manager reviews the bundle as one
    document and accepts or skips each section. One call per run — this
    completes it."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    if run["kind"] != "guidelines":
        return _err(
            f"this is a {run['kind']} run — submit_guidelines_refresh "
            "completes guidelines runs only",
            "use the submit tool matching the run's kind",
        )
    if not (summary or "").strip():
        return _err(
            "summary is required",
            "one line on what you read and what you are proposing — the "
            "manager reads it before opening the bundle",
        )
    if not isinstance(sections, list):
        return _err(
            "sections must be a list",
            "send a list of {section_key, title, proposed_text, rationale, "
            "severity} objects, or [] if you have nothing to propose",
        )
    if len(sections) > db.MAX_REFRESH_SECTIONS:
        return _err(
            f"too many sections ({len(sections)}) — the cap is "
            f"{db.MAX_REFRESH_SECTIONS}",
            "propose the sections the repository actually supports, not one "
            "per idea",
        )

    known = set(db.list_guideline_section_keys(settings, str(run["project_id"])))
    cleaned: list[dict[str, Any]] = []
    for i, raw in enumerate(sections):
        if not isinstance(raw, dict):
            return _err(
                f"sections[{i}] is not an object",
                "each entry is {section_key, title, proposed_text, "
                "rationale, severity}",
            )
        key = (raw.get("section_key") or "").strip()
        text = (raw.get("proposed_text") or "").strip()
        rationale = (raw.get("rationale") or "").strip()
        severity = (raw.get("severity") or "").strip() or "minor"
        title = (raw.get("title") or "").strip()
        if not text:
            return _err(
                f"sections[{i}] has no proposed_text",
                "every proposed section needs its full text; drop the entry "
                "instead of sending an empty one",
            )
        if len(text) > _MAX_RECOMMENDATION_CHARS:
            return _err(
                f"sections[{i}] proposed_text is too long (over "
                f"{_MAX_RECOMMENDATION_CHARS} characters)",
                "one section's text per entry, not a whole document",
            )
        if not rationale:
            return _err(
                f"sections[{i}] has no rationale",
                "say what the section says today and why yours is better — "
                "the manager decides on the rationale, not the diff",
            )
        if severity not in _SEVERITY_DEFINITIONS:
            return _err(
                f"sections[{i}] has unknown severity {severity!r}",
                "use one of: "
                + "; ".join(
                    f"{k} = {v}" for k, v in _SEVERITY_DEFINITIONS.items()
                ),
            )
        if not key and not title:
            return _err(
                f"sections[{i}] proposes a new section with no title",
                "a new section (empty section_key) needs a title the manager "
                "can read in the review",
            )
        # A key that names neither an existing section nor a catalog entry is
        # refused rather than silently coerced into a new section: the agent
        # meant to target something, and creating a stray section instead
        # hides the mistake behind a plausible-looking proposal.
        if key and key not in known and key not in _CATALOG_SECTION_KEYS:
            return _err(
                f"sections[{i}] names unknown section_key {key!r}",
                "existing keys: "
                + (", ".join(sorted(known)) if known else "(none yet)")
                + " — catalog keys: "
                + ", ".join(sorted(_CATALOG_SECTION_KEYS))
                + " — or leave section_key empty and give a title to propose "
                "a new section",
            )
        cleaned.append(
            {
                "section_key": key,
                "title": title,
                "proposed_text": text,
                "rationale": rationale,
                "severity": severity,
            }
        )

    result = db.record_guidelines_refresh(
        settings, worker, run, summary.strip(), cleaned
    )
    if not result["ok"]:
        return _err(
            result["reason"],
            "this run's refresh is already decided — release_work if you "
            "have nothing else to hand back",
        )

    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        None,
        None,
        None,
        None,
        None,
        worker_name=worker["name"],
        trigger="submit",
    )
    if not ok:
        return _err(
            "run is not claimed — claim it and retry",
            "list_my_work shows what you hold",
        )
    from .routers.worker import _store_handback_notes

    combined = summary.strip()
    if (notes_for_manager or "").strip():
        combined += "\n\n" + notes_for_manager.strip()
    _store_handback_notes(settings, run, worker, run_id, combined)

    md = (
        f"Guidelines pass handed back — {len(cleaned)} section(s) proposed. "
        "The manager reviews the bundle as one document and accepts section "
        "by section; nothing is applied until they do."
        if cleaned
        else "Guidelines pass handed back with nothing to propose — recorded "
        "as read, and the work item is closed."
    )
    return {
        "markdown": md,
        "ok": True,
        "refresh_id": result["refresh_id"],
        "sections_proposed": len(cleaned),
        "severity_definitions": _SEVERITY_DEFINITIONS,
    }


@mcp.tool(**_write("Submit plan"))
async def submit_plan(
    run_id: str,
    plan: str,
    test_plan: str = "",
    stdout: str = "",
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Hand back a plan run: implementation plan + test plan markdown,
    flowing into the manager's plan-review gate. notes_for_manager carries
    anything the manager should know at the gate (risks, deferred
    decisions, scope questions) — flagging concerns is part of finishing
    the work; the notes ride the submission itself and land on the review
    surface and the item's thread."""
    from .routers.worker import Submit, perform_submit

    settings = get_settings()
    worker = _worker()
    try:
        result = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(
                plan=plan,
                test_plan=test_plan or None,
                stdout=stdout or None,
                notes=notes_for_manager or None,
            ),
        )
    except HTTPException as e:
        return _err(
            str(e.detail),
            getattr(e, "hint", "check claim_work / the run id and retry"),
        )
    return _next(
        {
            "markdown": (
                "Plan submitted — it now sits in the plan-review gate."
            ),
            **result,
        },
        (
            "get_run_status",
            "the review outcome; a rejected run names the retry that "
            "carries the manager's feedback",
        ),
    )


@mcp.tool(**_write("Submit code work"))
async def submit_code_work(
    run_id: str,
    branch_ref: str,
    notes: str = "",
    stdout: str = "",
) -> dict[str, Any]:
    """Hand back a code run: the branch you pushed to the factory remote.
    The factory verifies it on GitHub, opens the PR itself, and moves the
    item to review. notes reaches the manager at the review gate (and the
    item's thread) — flag risks, deferred decisions, and scope questions
    there; it is part of finishing the work."""
    from .routers.worker import Submit, perform_submit

    settings = get_settings()
    worker = _worker()
    try:
        result = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(
                branch_ref=branch_ref,
                notes=notes or None,
                stdout=stdout or None,
            ),
        )
    except HTTPException as e:
        # US-5.24: the submit endpoint attaches a taxonomy-correct hint
        # (who owns the fix); only fall back when it didn't.
        return _err(
            str(e.detail),
            getattr(e, "hint", "check claim_work / the run id and retry"),
        )
    return _next(
        {
            "markdown": (
                f"Submitted — PR {result.get('pr_url')} is in review."
            ),
            **result,
        },
        *_code_submit_steps(settings, run_id, str(worker["id"])),
    )


def _resolve_member_ids(
    members: list[dict[str, Any]], given: list[str]
) -> tuple[list[str], list[str]]:
    """US-27.1, moved to `db` by US-40.2 so the branch hand-back resolves
    membership the same way this does. Kept as a name here because the module
    reads better for it."""
    return db.resolve_member_ids(members, given)


def _coverage_md(members: list[dict[str, Any]]) -> str:
    landed = [m for m in members if m["landed"]]
    lines = "\n".join(
        f"- {'✅' if m['landed'] else '⬜'} `{m['display_id']}` — {m['title']}"
        for m in members
    )
    return (
        f"\n\n**Coverage: {len(landed)} of {len(members)} stories have a "
        f"landed commit.**\n\n{lines}"
    )


@mcp.tool(**_write("Submit changeset"))
async def submit_changeset(
    run_id: str,
    base_sha: str,
    message: str,
    files: list[dict[str, Any]],
    issue_ids: list[str] | None = None,
    final: bool | None = None,
    allow_partial: bool = False,
    notes: str = "",
    stdout: str = "",
    test_cases: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Hand back code as changed files — the factory does all the git:
    builds the commit from base_sha, pushes the work branch with the
    org's credential, and (on the final call) opens the PR and moves the
    item to review. No git binary, no GitHub account, no credential
    beyond this token. Each file is {path, op: add|update|delete,
    content?, encoding?: text|base64}. First commit: base_sha is the
    get_workspace base; each later commit declares the head this one
    returned — a stale base answers the current head so you can refetch
    and reapply, never a silent overwrite.

    Committing and finishing are two different acts. On a run covering
    several stories, commit each story as you finish it, naming it in
    `issue_ids` (display id or uuid), with `final=false`; call once more
    with `final=true` when the whole run is done. Only stories with a
    landed commit are moved to review — `final=true` while one has none
    is refused unless you set `allow_partial=true` to say so
    deliberately. A single-story run needs none of this: omit all three.
    notes reaches the manager at the review gate (and the item's
    thread) — flag risks and open questions there; it is part of
    finishing the work."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    if run["kind"] != "code":
        return _err(
            f"this is a {run['kind']} run — changesets are for code runs",
            "hand back with "
            + ("submit_plan" if run["kind"] == "plan" else "submit_prd"),
        )

    # US-27.1: attribution and finality are settled BEFORE anything touches
    # GitHub. A commit that cannot be attributed to a story in this run is a
    # commit whose coverage nobody can read afterwards.
    members = db.run_members(settings, run_id)
    multi = len(members) > 1
    covered_ids: list[str] = []
    if multi:
        if not issue_ids:
            return _err(
                "this run covers "
                f"{len(members)} stories — say which of them this commit "
                "implements in issue_ids",
                "issue_ids takes display ids or uuids: "
                + ", ".join(f"`{m['display_id']}`" for m in members),
            )
        covered_ids, unknown = _resolve_member_ids(members, issue_ids)
        if unknown:
            return _err(
                "not in this run: " + ", ".join(f"`{u}`" for u in unknown),
                "this run covers "
                + ", ".join(f"`{m['display_id']}`" for m in members),
            )
        if final is None:
            return _err(
                "say whether this is the last commit of the run: "
                "final=false to keep working, final=true to close it",
                "commit each story as you finish it with final=false, then "
                "call once more with final=true when the run is done",
            )
    else:
        if issue_ids:
            return _err(
                "issue_ids applies to a run covering several stories; this "
                "run covers one",
                "resubmit without issue_ids",
            )
        # Single-story runs behave exactly as they did before this story.
        final = True if final is None else final

    # US-31.7 → us-96.8: the factory's own scratch never lands, regardless
    # of any .gitignore — but it is now FILTERED, not fatal. The dropped
    # paths are named in the response so the agent knows; only a changeset
    # that is nothing but scratch is refused (there is nothing to commit).
    files, dropped_scratch = changesets.split_scratch(files)
    if dropped_scratch and not files:
        return _err(
            "changeset rejected — every file was factory scratch; there is "
            "nothing to commit",
            "factory scratch never lands: " + ", ".join(dropped_scratch),
        )
    findings = changesets.validate_changeset(files)
    if not (message or "").strip():
        findings = ["commit message is empty"] + findings
    if not (base_sha or "").strip():
        findings = ["base_sha is required — get_workspace answers it"] + findings
    # US-89.2 AC5: a delivered SECRET value never rides a commit — the exact
    # failure the worker token had on 2026-08-13, closed for the project's
    # own credentials before the first one leaks. Exact-match sweep over the
    # changeset's file contents; best-effort (an unreadable environment must
    # not block a hand-back that carries no secrets).
    try:
        _env_pid = str(run.get("project_id") or "")
        if _env_pid:
            _env_values, _env_catalog = await project_env.effective_env(
                get_settings(), _env_pid, str(worker["id"])
            )
            _secrets = project_env.secret_values_for_run(_env_values, _env_catalog)
            if _secrets:
                for f in files:
                    content = str(f.get("content") or "")
                    if any(s in content for s in _secrets):
                        findings.append(
                            f"{f.get('path')}: contains a value from the "
                            "project's SECRET environment — credentials are "
                            "delivered to your process env, never committed"
                        )
    except Exception:  # noqa: BLE001
        logger.warning("env secret sweep skipped for run %s", run_id)
    if findings:
        return {
            "error": "changeset rejected — nothing touched GitHub",
            "findings": findings,
            "hint": "fix the findings and resubmit",
            "markdown": "# Changeset findings\n\n"
            + "\n".join(f"- {f}" for f in findings),
        }
    ic = run.get("input_context") or {}
    repo_full = ic.get("repo_full_name") or ""
    if "/" not in repo_full:
        return _err("run has no linked repo", "")
    # US-7.3: the strategy-resolved working branch (stored on the run), not a
    # hardcoded issue-id branch. `direct` mode targets the default branch.
    branch, _dev_strategy, submit_mode = db.resolve_working_branch(settings, run)
    db.set_run_branch_ref(settings, run_id, branch)
    try:
        token = await github_tokens.token_for_org(
            settings, str(run["org_id"]), repo_full
        )
        # US-31.7: the factory has the repository, so it reads the project's
        # own .gitignore rather than trusting the agent's file list. With a
        # persistent workspace (us-31.8) the folder holds node_modules and
        # friends, and one bad judgement call is otherwise a 300MB pull
        # request. Best-effort: a repo whose ignore rules cannot be read
        # behaves exactly as before rather than blocking the hand-back.
        ignored: list[str] = []
        try:
            ignore_files, tracked = await _repo_ignore_context(
                token, repo_full, base_sha.strip(), files
            )
            ignored = changesets.ignored_paths(files, ignore_files, tracked)
        except github.GitHubError:
            logger.warning(
                "could not read ignore rules for %s@%s; skipping the "
                "gitignore guard on run %s",
                repo_full, base_sha, run_id,
            )
        if ignored:
            findings = [
                f"{p}: the project's .gitignore excludes this — it is a build "
                "artifact, not source"
                for p in ignored
            ]
            return {
                "error": "changeset rejected — nothing touched GitHub",
                "findings": findings,
                "hint": (
                    "drop these paths and resubmit. Dependencies and build "
                    "output live in the workspace, never in a commit."
                ),
                "markdown": "# Changeset findings\n\n"
                + "\n".join(f"- {f}" for f in findings),
            }
        result = await changesets.apply_changeset(
            token,
            repo_full,
            branch,
            base_sha.strip(),
            f"{message.strip()}\n\nFactory-Run: {run_id}",
            files,
            author_name=worker["name"],
        )
    except github.GitHubError as e:
        return _github_err(e)
    if result.get("stale"):
        head = result["current_head"]
        return {
            "error": (
                f"stale base: the branch head is {head}, not {base_sha} — "
                "the branch moved since your snapshot"
            ),
            "current_head": head,
            "hint": (
                "refetch with get_workspace (it re-pins to the current "
                "head), reapply your changes, and resubmit with "
                "base_sha set to that head",
            )[0],
            "markdown": f"Stale base — current head is `{head}`.",
        }
    commit_sha = result["commit_sha"]
    # US-27.1: the landed record, written before anything else can read it.
    # Everything downstream — the fan-out, the review surface, expiry — asks
    # this table what happened rather than asking the run's status.
    if multi:
        db.record_changeset_coverage(
            settings,
            run_id,
            str(run["org_id"]),
            covered_ids,
            commit_sha,
            message.strip(),
            files_changed=len(files),
        )
        members = db.run_members(settings, run_id)
    # Audit: paths/ops/sizes and the resulting sha — never file content.
    try:
        db.record_issue_event(
            settings,
            str(run["org_id"]),
            str(run["issue_id"]),
            "changeset-submitted",
            {
                "run_id": str(run_id),
                "commit_sha": commit_sha,
                "branch_ref": branch,
                "worker": worker["name"],
                "files": changesets.summarize(files),
            },
        )
    except Exception:
        pass
    # US-27.1: the commit has landed and the run is still open. This is the
    # path run 11c564b0 never had — its first hand-back finalized the run and
    # the two parts still to come had nowhere to go.
    if not final:
        remaining = [m for m in members if not m["landed"]]
        return _next(
            {
                "markdown": (
                    f"Committed `{commit_sha}` on `{branch}` for "
                    + ", ".join(
                        f"`{m['display_id']}`"
                        for m in members
                        if m["issue_id"] in covered_ids
                    )
                    + ". The run is still yours."
                    + _coverage_md(members)
                ),
                "commit_sha": commit_sha,
                "branch_ref": branch,
                # us-96.8 AC4: the submit answers with exactly what it took,
                # so a partial hand-back is visible the moment it happens.
                "received": [
                    {"path": f.get("path"), "op": f.get("op")} for f in files
                ],
                "received_count": len(files),
                "dropped": [
                    {"path": p, "reason": "factory scratch never lands"}
                    for p in dropped_scratch
                ],
                "final": False,
                "coverage": members,
                "next_base_sha": commit_sha,
            },
            (
                "submit_changeset",
                (
                    f"{len(remaining)} story(ies) still to commit — declare "
                    f"base_sha `{commit_sha}` on the next one"
                    if remaining
                    else "call once more with final=true to close the run"
                ),
            ),
        )

    # US-27.1: closing a run while a member story has no landed commit is
    # refused. The commit above is kept — the work is on the branch and the
    # run stays claimable by its holder; only the closing act is refused.
    if multi and not allow_partial:
        missing = [m for m in members if not m["landed"]]
        if missing:
            return _err(
                "cannot close the run: no commit has landed for "
                + ", ".join(f"`{m['display_id']}`" for m in missing),
                "commit their work (final=false), or call again with "
                "allow_partial=true to hand back deliberately partial work",
            )

    # From here the run follows the exact submit_code_work path — one
    # review pipeline regardless of transport.
    from .routers.worker import Submit, perform_submit

    try:
        sub = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(
                branch_ref=branch,
                notes=notes or None,
                stdout=stdout or None,
                # us-96.8 AC2: the MCP transport's ONE route for test cases —
                # a structured field on the submit, never a scratch file the
                # changeset would have refused (and now names as dropped).
                # Shape-tolerant like the runner's own submit: a case with no
                # title is dropped rather than failing the hand-back.
                test_cases=[
                    t
                    for t in (test_cases or [])
                    if isinstance(t, dict) and str(t.get("title") or "").strip()
                ]
                or None,
            ),
        )
    except HTTPException as e:
        return _err(
            str(e.detail),
            getattr(e, "hint", "the commit landed — retry the submit"),
        )
    landed_md = (
        f"Changeset committed as `{commit_sha}` on `{branch}` — landed "
        "directly on the default branch (no PR; this project commits to main)."
        if submit_mode == "direct"
        else f"Changeset committed as `{commit_sha}` on `{branch}` and "
        f"submitted — PR {sub.get('pr_url')} is in review."
    )
    # us-96.8 AC4: state the count plainly and name every path taken and
    # dropped — the agent's instruction says to check this echo against its
    # own list of changed files before reporting done.
    landed_md += f" Received {len(files)} file(s)."
    if dropped_scratch:
        landed_md += (
            " Dropped factory scratch (never lands): "
            + ", ".join(f"`{p}`" for p in dropped_scratch)
            + "."
        )
    out: dict[str, Any] = {
        "markdown": landed_md,
        "commit_sha": commit_sha,
        "branch_ref": branch,
        "submit_mode": submit_mode,
        "received": [{"path": f.get("path"), "op": f.get("op")} for f in files],
        "received_count": len(files),
        "dropped": [
            {"path": p, "reason": "factory scratch never lands"}
            for p in dropped_scratch
        ],
        **sub,
    }
    if multi:
        # US-27.1: the run closed, so say what it actually covered — and name
        # what was deliberately left, so "partial" is a recorded decision
        # rather than something the manager discovers in the diff.
        members = db.run_members(settings, run_id)
        left = [m for m in members if not m["landed"]]
        out["markdown"] += _coverage_md(members)
        out["coverage"] = members
        if left:
            out["partial"] = [m["issue_id"] for m in left]
            out["markdown"] += (
                "\n\nHanded back as **partial** — "
                + ", ".join(f"`{m['display_id']}`" for m in left)
                + " had no commit and went back to the pool."
            )
            try:
                db.record_issue_event(
                    settings,
                    str(run["org_id"]),
                    str(run["issue_id"]),
                    "partial-handback",
                    {
                        "run_id": str(run_id),
                        "landed": [m["display_id"] for m in members if m["landed"]],
                        "returned": [m["display_id"] for m in left],
                    },
                )
            except Exception:
                pass
    return _next(
        out,
        *_code_submit_steps(settings, run_id, str(worker["id"])),
    )


# US-13.13: the deploy-run tool set — claim-scoped, definition-only, and
# every one of them refuses any deployment other than the claimed one.


async def _held_deploy_run(run_id: str) -> tuple[
    dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None
]:
    """(run, bundle, error): a claimed run that OWNS a deployment, plus its
    deployment bundle (deployment + server + project rows, service-side).

    US-21.3: `release` runs deploy too — a release ships its pinned commit to
    the project's designated UAT deployment, and a promotion run ships it to
    production. Both name their deployment in `input_context.deployment_id`,
    exactly as a `deploy` run does, so they use the same tools and the same
    rails. Every rail below is unchanged: `agent_deploy_refusal` still refuses
    a protected deployment outright and still requires the human-set
    `agent_dispatch_allowed` flag on production, a run may trigger its
    deployment once, and rollback needs pre-authorization.
    """
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return None, None, _err(
            "run not found", "list_available_work shows valid run ids"
        )
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return None, None, _err("you do not hold this run", "claim_work it first")
    if run["kind"] not in ("deploy", "release"):
        return None, None, _err(
            f"this is a {run['kind']} run — deployment tools work on runs "
            "that own a deployment (deploy, release)",
            "use the tools matching the run's kind",
        )
    db.extend_claim(settings, run_id, str(worker["id"]), tool="deployment_check")
    ic = run.get("input_context") or {}
    deployment_id = str(ic.get("deployment_id") or "")
    if not deployment_id:
        # A release run reaches this when its project had no designated
        # deployment at dispatch. Say that, rather than "no longer exists".
        return None, None, _err(
            "this run names no deployment",
            "the project needs a release deployment designated on its "
            "Deployments tab — report and stop",
        )
    bundle = db.get_deployment_for_agent(
        settings, deployment_id, str(worker["org_id"])
    )
    if not bundle:
        return None, None, _err(
            "the claimed deployment no longer exists",
            "release_work with a note",
        )
    return run, bundle, None


@mcp.tool(**_write("Trigger deployment"))
async def trigger_deployment(run_id: str) -> dict[str, Any]:
    """Start the real deployment run for your claimed deploy run — the
    server executes it exactly as a human-clicked run; you hold no
    credentials. The rails are re-checked here independently of dispatch
    (protected: refused always; production: needs the human-set flag).
    Only the claimed deployment can be triggered."""
    settings = get_settings()
    worker = _worker()
    run, bundle, err = await _held_deploy_run(run_id)
    if err:
        return err
    ic = run.get("input_context") or {}
    dep = bundle["deployment"]
    # Defense in depth: dispatch checked these; the trigger re-checks.
    refusal = db.agent_deploy_refusal(dep)
    if refusal:
        return _err(refusal, "this rail is not negotiable — report and stop")
    if ic.get("deployment_run_id"):
        return _err(
            "this deploy run already triggered its deployment",
            "observe it with get_deployment_run_status; rollback (if "
            "pre-authorized) is the only other trigger",
        )
    server = bundle["server"]
    project = bundle["project"]
    # US-50.1: the server is what makes a deployment factory-run, so its
    # absence is only a fault for that kind.
    if not project or (not server and not deploy.is_external(dep)):
        return _err(
            "deployment is missing its server or project",
            "only a manager can fix the definition",
        )
    # US-7.3: classified environments release from the project's release
    # branch — same override the human path applies.
    env = dep.get("environment")
    release_branch = None
    if env == "uat":
        release_branch = (project.get("uat_branch") or "").strip() or None
    elif env == "production":
        release_branch = (project.get("production_branch") or "").strip() or None
    if release_branch:
        dep = {**dep, "branch": release_branch}
    import asyncio as _asyncio

    # US-21.3: a run that names a ref must SHIP that ref. create_run's
    # branch_override only records it on the row and flags is_override; the
    # pipeline pins a commit from ctx["override"] and otherwise resolves the
    # branch head. Passing one without the other made the row claim a ref the
    # deploy never used — harmless for a deploy run with no ref, fatal for a
    # release, whose whole point is that the pinned commit is what ships.
    override = None
    ref = (ic.get("ref") or "").strip()
    if ref:
        repo_full = project.get("repo_full_name") or ""
        if "/" not in repo_full:
            return _err(
                "this project has no linked repository",
                "a pinned ref cannot be resolved without one",
            )
        owner, repo = repo_full.split("/", 1)
        try:
            gh_token = await github_tokens.token_for_org(
                settings, str(run["org_id"]), repo_full
            )
            commit = await github.get_commit(gh_token, owner, repo, ref)
        except github.GitHubError as e:
            return _github_err(e)
        message = ((commit.get("commit") or {}).get("message") or "").strip()
        override = {
            "ref": ref,
            "sha": commit["sha"],
            "message": message.splitlines()[0][:200] if message else "",
            # US-50.4: read only by the merge pipeline — the branch cut at the
            # pinned commit, so an external deployment ships the pin rather
            # than a branch head that has moved. Absent for a plain deploy run
            # and for releases cut before us-50.4; the merge falls back to a
            # branch materialized at the commit, with the same result.
            "branch": (ic.get("release_branch") or "").strip() or None,
        }

    actor_label = f"agent:{worker['name']} ({run['kind']} run {str(run_id)[:8]})"
    try:
        deployment_run_id = await _asyncio.to_thread(
            deploy.create_run,
            settings,
            dep,
            None,
            actor_label,
            "branch",
            None,
            (ref or None),
        )
    except deploy.RunActive:
        return _err(
            "a run is already active for this deployment",
            "wait for it to finish, then re-check status",
        )
    deploy.launch(
        settings,
        {
            "run_id": deployment_run_id,
            "org_id": str(run["org_id"]),
            "deployment": dep,
            "server": server,
            "repo_full_name": project.get("repo_full_name"),
            "project_name": project.get("name") or "",
            "triggered_by": actor_label,
            "override": override,
        },
    )
    db.update_run_input_context(
        settings, run_id, {"deployment_run_id": deployment_run_id}
    )
    return _next(
        {
            "markdown": (
                f"Deployment started — run `{deployment_run_id}` on "
                f"{dep.get('name')}."
            ),
            "ok": True,
            "deployment_run_id": deployment_run_id,
        },
        (
            "get_deployment_run_status",
            "poll until it finishes, then verify health before any verdict",
        ),
    )


@mcp.tool(**_read("Get deployment run status"))
async def get_deployment_run_status(run_id: str) -> dict[str, Any]:
    """The triggered deployment run's status and log tail — observation
    only, scoped to the deployment your claim names."""
    settings = get_settings()
    worker = _worker()
    run, bundle, err = await _held_deploy_run(run_id)
    if err:
        return err
    ic = run.get("input_context") or {}
    dr_id = str(ic.get("deployment_run_id") or "")
    if not dr_id:
        return _err(
            "nothing triggered yet", "trigger_deployment starts the run"
        )
    view = db.get_deployment_run_view(settings, dr_id, str(worker["org_id"]))
    if not view:
        return _err("deployment run not found", "trigger_deployment first")
    return {
        "markdown": (
            f"# Deployment run `{dr_id}`\n\n- Status: **{view['status']}**\n"
            + (
                f"- Finished: {view['finished_at']}\n"
                if view.get("finished_at")
                else "- Still running.\n"
            )
            + (
                f"\n## Log tail\n\n````\n{view['log_tail']}\n````"
                if view.get("log_tail")
                else ""
            )
        ),
        "status": view["status"],
        "finished_at": str(view.get("finished_at") or "") or None,
        "log_tail": view.get("log_tail"),
    }


@mcp.tool(**_read("Get deployment health"))
async def get_deployment_health(run_id: str) -> dict[str, Any]:
    """Run the deployment's configured health check, from the target
    server — verify before declaring any verdict. Read-only."""
    settings = get_settings()
    run, bundle, err = await _held_deploy_run(run_id)
    if err:
        return err
    dep = bundle["deployment"]
    server = bundle["server"]
    # US-50.3: an external deployment has no machine to curl from, and the
    # factory does not own the pipeline that ships it. Answer plainly rather
    # than with an error, so the agent stops instead of retrying a tool that
    # will keep failing.
    if deploy.is_external(dep):
        return {
            "markdown": (
                "Not applicable — this deployment is **external**. The "
                "factory did not deploy it; it merged "
                f"`{dep.get('branch')}` into `{dep.get('target_branch')}` "
                "and stopped there. There is no health check to run, and "
                "the merge landing is the whole verdict."
            ),
            "applicable": False,
            "healthy": None,
            "detail": "external deployment — no health check exists",
            "url": None,
        }
    url = (dep.get("health_check_url") or "").strip()
    if not url:
        return _err(
            "no health check configured on this deployment",
            "without one there is nothing to verify against — say so in "
            "your verdict summary",
        )
    import asyncio as _asyncio

    try:
        conn = await deploy.connect_to_server(settings, server)
        try:
            healthy, detail = await _asyncio.to_thread(
                deploy.health_check_once,
                conn.transport,
                url,
                int(dep.get("health_check_expected_status") or 200),
            )
        finally:
            conn.close()
    except deploy.PipelineError as e:
        return _err(f"health check could not run: {e.message}", "retry")
    return {
        "markdown": (
            f"Health check {'PASSED' if healthy else 'FAILED'} — {detail} "
            f"({url})"
        ),
        "healthy": healthy,
        "detail": detail,
        "url": url,
    }


@mcp.tool(**_write("Trigger deployment rollback"))
async def trigger_deployment_rollback(run_id: str) -> dict[str, Any]:
    """Roll back the claimed deployment to its previous successful
    release — available ONLY when the manager pre-authorized it at
    dispatch, only once, and only on a failed deployment or failed
    health checks. The agent never decides on its own authority."""
    settings = get_settings()
    worker = _worker()
    run, bundle, err = await _held_deploy_run(run_id)
    if err:
        return err
    ic = run.get("input_context") or {}
    dep = bundle["deployment"]
    server = bundle["server"]
    if deploy.is_external(dep):
        # US-50.3: not supported, and the app says so once. Recovering an
        # external environment means merging a fix, or reverting on GitHub by
        # hand outside the factory.
        return _err(
            "rollback is not supported for an external deployment — the "
            "factory only merged into "
            f"{dep.get('target_branch')}; it has nothing to put back",
            "report the verdict you observed and stop — recovery is a fix "
            "merged forward, and the manager decides it",
        )
    refusal = db.agent_deploy_refusal(dep)
    if refusal:
        return _err(refusal, "this rail is not negotiable")
    if not ic.get("auto_rollback"):
        return _err(
            "rollback was not pre-authorized at dispatch",
            "report the verdict deployed-but-unhealthy and stop — the "
            "manager decides what happens next",
        )
    if ic.get("rollback_run_id"):
        return _err(
            "rollback already triggered — exactly once is the rail",
            "submit_deploy_run with verdict rolled-back",
        )
    dr_id = str(ic.get("deployment_run_id") or "")
    if not dr_id:
        return _err("nothing was deployed by this run", "nothing to roll back")
    view = db.get_deployment_run_view(settings, dr_id, str(run["org_id"]))
    if not view or not view.get("finished_at"):
        return _err(
            "the deployment run has not finished",
            "wait for it, verify health, then decide",
        )
    # The failure evidence: a failed pipeline, or failed health checks
    # observed right now.
    justified = view["status"] == "failed"
    if not justified:
        url = (dep.get("health_check_url") or "").strip()
        if not url:
            return _err(
                "the deployment succeeded and no health check is "
                "configured — there is no failure signal to justify a "
                "rollback",
                "report deployed (or deployed-but-unhealthy with your "
                "observations) instead",
            )
        import asyncio as _asyncio

        try:
            conn = await deploy.connect_to_server(settings, server)
            try:
                healthy, detail = await _asyncio.to_thread(
                    deploy.health_check_once,
                    conn.transport,
                    url,
                    int(dep.get("health_check_expected_status") or 200),
                )
            finally:
                conn.close()
        except deploy.PipelineError as e:
            return _err(f"health check could not run: {e.message}", "retry")
        if healthy:
            return _err(
                f"health checks pass ({detail}) — nothing to roll back",
                "submit_deploy_run with verdict deployed",
            )
    if (dep.get("strategy") or "in-place") != "releases":
        return _err(
            "rollback needs the releases strategy — this deployment is "
            "in-place",
            "report deployed-but-unhealthy; the manager handles recovery",
        )
    to_run = db.latest_successful_deployment_run(
        settings, str(dep["id"]), exclude_run_id=dr_id
    )
    if not to_run or not to_run.get("release_path"):
        return _err(
            "no previous successful release to roll back to",
            "report deployed-but-unhealthy; the manager handles recovery",
        )
    import asyncio as _asyncio

    actor_label = f"agent:{worker['name']} (deploy run {str(run_id)[:8]})"
    try:
        rollback_run_id = await _asyncio.to_thread(
            deploy.create_rollback_run,
            settings,
            dep,
            dict(to_run),
            str(run["org_id"]),
            actor_label,
        )
    except deploy.RunActive:
        return _err(
            "a run is already active for this deployment",
            "wait for it to finish, then re-check",
        )
    deploy.launch_rollback(
        settings,
        {
            "run_id": rollback_run_id,
            "org_id": str(run["org_id"]),
            "deployment": dep,
            "server": server,
            "to_run": dict(to_run),
            "project_name": (bundle.get("project") or {}).get("name") or "",
            "triggered_by": actor_label,
        },
    )
    db.update_run_input_context(
        settings, run_id, {"rollback_run_id": rollback_run_id}
    )
    return _next(
        {
            "markdown": (
                f"Rollback started — run `{rollback_run_id}` repoints to "
                f"`{to_run.get('release_path')}`."
            ),
            "ok": True,
            "rollback_run_id": rollback_run_id,
        },
        (
            "submit_deploy_run",
            "finish with the verdict rolled-back once the rollback lands",
        ),
    )


_DEPLOY_VERDICTS = ("deployed", "deployed-unhealthy", "rolled-back")


@mcp.tool(**_write("Submit deploy run"))
async def submit_deploy_run(
    run_id: str, verdict: str, summary: str, stdout: str = ""
) -> dict[str, Any]:
    """Complete a claimed deploy run with the honest verdict: `deployed`
    (pipeline succeeded AND health verified), `deployed-unhealthy`
    (checks failed and rollback was not authorized — the managers are
    notified), or `rolled-back` (checks failed, the pre-authorized
    rollback was taken). The verdict is validated against what actually
    happened — never claim an outcome you did not observe."""
    settings = get_settings()
    worker = _worker()
    run, bundle, err = await _held_deploy_run(run_id)
    if err:
        return err
    ic = run.get("input_context") or {}
    v = (verdict or "").strip()
    if v not in _DEPLOY_VERDICTS:
        return _err(
            f"verdict must be one of: {', '.join(_DEPLOY_VERDICTS)}",
            "pick the one matching what you observed",
        )
    if not (summary or "").strip():
        return _err("summary is required", "say what you observed")
    dr_id = str(ic.get("deployment_run_id") or "")
    if not dr_id:
        return _err(
            "nothing was triggered — there is no outcome to report",
            "trigger_deployment first, or release_work with a note",
        )
    view = db.get_deployment_run_view(settings, dr_id, str(run["org_id"]))
    if not view or not view.get("finished_at"):
        return _err(
            "the deployment run has not finished — no verdict yet",
            "poll get_deployment_run_status",
        )
    if v == "rolled-back" and not ic.get("rollback_run_id"):
        return _err(
            "no rollback was triggered by this run",
            "the verdict must match what happened",
        )
    if v == "deployed" and view["status"] != "succeeded":
        return _err(
            f"the deployment run finished '{view['status']}' — 'deployed' "
            "would misreport it",
            "use deployed-unhealthy (or rolled-back if you rolled back)",
        )
    db.update_run_input_context(settings, run_id, {"verdict": v})
    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        stdout or None,
        None,
        None,
        None,
        None,
        worker_name=worker["name"],
        trigger="submit",
    )
    if not ok:
        return _err(
            "run is not claimed — claim it and retry",
            "list_my_work shows what you hold",
        )
    dep_name = (ic.get("deployment") or {}).get("name") or "deployment"
    notes = f"Deploy verdict: {v} — {dep_name}, deployment run {dr_id}.\n\n" + summary.strip()
    from .routers.worker import _store_handback_notes

    _store_handback_notes(settings, run, worker, run_id, notes)
    if v == "deployed-unhealthy":
        try:
            db.notify_org_managers(
                settings,
                str(run["org_id"]),
                "deploy_unhealthy",
                {
                    "deployment": dep_name,
                    "run_id": str(run_id),
                    "message": summary.strip()[:200],
                },
            )
        except Exception:  # noqa: BLE001 — the verdict already landed
            pass
    return _next(
        {
            "markdown": f"Verdict recorded: **{v}** — {dep_name}.",
            "ok": True,
            "verdict": v,
            "deployment_run_id": dr_id,
        },
        (
            "get_run_status",
            "the manager sees the verdict and the linked deployment run",
        ),
    )


@mcp.tool(**_read("List release prep work"))
async def list_release_prep_work() -> dict[str, Any]:
    """List queued release-prep jobs in the org's pool (US-63.3). Not part
    of list_available_work — release prep has no issue and isn't story
    work. Each item is a release waiting for its commit range read and its
    notes written."""
    settings = get_settings()
    worker = _worker()
    items = db.list_release_prep_pool(settings, str(worker["org_id"]))
    rows = [
        {
            "prep_id": str(r["id"]),
            "release_id": str(r["release_id"]),
            "version": r["version"],
            "project": r["project_name"],
            "repo": r["repo_full_name"],
        }
        for r in items
    ]
    md = (
        "Queued release prep:\n"
        + "\n".join(
            f"- **{r['version']}** — `{r['prep_id']}` ({r['project']}, {r['repo']})"
            for r in rows
        )
        if rows
        else "No release prep queued."
    )
    return {"markdown": md, "items": rows}


@mcp.tool(**_write("Claim release prep work"))
async def claim_release_prep_work(prep_id: str) -> dict[str, Any]:
    """Atomically claim a release-prep job. Losing a race answers with
    guidance to list again, not an error."""
    settings = get_settings()
    worker = _worker()
    result = await release_prep.claim(settings, prep_id, worker)
    if "error" in result:
        return _err(result["error"], "list_release_prep_work shows what's still queued")
    return _next(
        {"markdown": f"Claimed release prep `{prep_id}`.", **result},
        (
            "get_release_changes",
            "read the commit range before writing notes",
        ),
    )


@mcp.tool(**_write("Submit release notes"))
async def submit_release_notes(
    prep_id: str,
    notes_summary: str,
    notes_detail: str,
    test_cases: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Complete a claimed release-prep job (US-63.1).

    Hand back BOTH sets of notes — `notes_summary` is a few lines a manager
    reads at a glance, `notes_detail` explains what actually changed: schema
    changes, migrations applied, modules affected, read from
    get_release_changes rather than inferred. The version is read from the
    release, never chosen by you. `test_cases` are the regression cases you
    authored for this release as a whole (title, steps, expected_result) and
    are attached alongside the ones the included work items already carry.

    That is the whole job. Deploying to UAT, verifying its health, and
    everything after is the system's own pipeline — it fires the moment this
    call succeeds. There is nothing left for you to trigger or verify."""
    settings = get_settings()
    worker = _worker()
    result = await release_prep.submit(
        settings, prep_id, worker, notes_summary, notes_detail, test_cases
    )
    if "error" in result:
        return _err(result["error"])

    md = f"Release {result['version']}'s notes are in — the UAT deploy is firing now."
    if result.get("deploy_error"):
        md = (
            f"Release {result['version']}'s notes are in, but the UAT deploy "
            f"failed to start: {result['deploy_error']}. The manager will see "
            "it flagged on the release."
        )
    if result.get("test_cases_attached") or result.get("test_cases_inherited"):
        md += (
            f" {result['test_cases_inherited']} inherited and "
            f"{result['test_cases_attached']} regression test cases attached."
        )
    return _next(
        {"markdown": md, **result},
        (
            "release_work",
            "your job here is done — nothing left to poll or verify",
        ),
    )


@mcp.tool(**_write("Submit test run"))
async def submit_test_run(
    run_id: str,
    summary: str,
    stdout: str = "",
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Complete a claimed test run after reporting per-case results with
    report_test_results. summary says what ran and where (suite, branch,
    environment) — it reaches the manager on the review surface. A test
    run that reported nothing has nothing to hand back: it is rejected
    here with guidance to release_work with a note instead."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_available_work shows valid run ids")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    if run["kind"] != "test":
        return _err(
            f"this is a {run['kind']} run — submit_test_run completes "
            "test runs only",
            "use the submit tool matching the run's kind",
        )
    if not (summary or "").strip():
        return _err(
            "summary is required",
            "say what ran and where — the manager reads it at the gate",
        )
    reported = db.count_run_test_results(settings, run_id)
    if reported == 0:
        return _err(
            "no test results reported by this run — nothing to hand back",
            "report_test_results for the cases you executed first; if you "
            "could not execute anything, release_work with a note saying "
            "why — an empty completion would read as verification that "
            "never happened",
        )
    ok = db.complete_run(
        settings,
        run_id,
        "succeeded",
        stdout or None,
        None,
        None,
        None,
        None,
        worker_name=worker["name"],
        trigger="submit",
    )
    if not ok:
        return _err(
            "run is not claimed — claim it and retry",
            "list_my_work shows what you hold",
        )
    from .routers.worker import _store_handback_notes

    combined = summary.strip()
    if (notes_for_manager or "").strip():
        combined += "\n\n" + notes_for_manager.strip()
    _store_handback_notes(settings, run, worker, run_id, combined)
    return _next(
        {
            "markdown": (
                f"Verification complete — {reported} per-case result(s) "
                "recorded on the review surface."
            ),
            "ok": True,
            "results_reported": reported,
        },
        (
            "get_run_status",
            "confirm the run completed; the manager reads the results at "
            "the review gate",
        ),
    )


@mcp.tool(**_write("Submit PRD"))
async def submit_prd(
    run_id: str,
    prd: str,
    stdout: str = "",
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Hand back a prd run: the four PRD sections as markdown (## Problem,
    ## Goals, ## Out of scope, ## Acceptance criteria). notes_for_manager
    carries anything the manager should know at the gate — flagging
    concerns is part of finishing the work; the notes ride the submission
    itself and land on the review surface and the item's thread."""
    from .routers.worker import Submit, perform_submit

    settings = get_settings()
    worker = _worker()
    try:
        result = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(prd=prd, stdout=stdout or None, notes=notes_for_manager or None),
        )
    except HTTPException as e:
        return _err(
            str(e.detail),
            getattr(e, "hint", "check claim_work / the run id and retry"),
        )
    return _next(
        {
            "markdown": (
                "PRD submitted — it now sits in the prd-review gate."
            ),
            **result,
        },
        (
            "get_run_status",
            "the review outcome; a rejected run names the retry that "
            "carries the manager's feedback",
        ),
    )


@mcp.tool(**_write("Submit stories"))
async def submit_stories(
    run_id: str,
    stories: list[dict[str, Any]],
    stdout: str = "",
    notes_for_manager: str = "",
) -> dict[str, Any]:
    """Hand back a breakdown run: the story split as a list of
    {title, body, acceptance_criteria}. The factory creates each as a
    draft child story of the feature for the manager to curate — there is
    no separate review gate. Honor the breakdown mode from the context
    ('single' ⇒ exactly one story). validate_submission dry-runs the split
    (≥1 story, each titled) first. notes_for_manager carries anything the
    manager should know about the split — it rides the submission itself
    and lands on the item's thread."""
    from .routers.worker import Submit, perform_submit

    settings = get_settings()
    worker = _worker()
    if not stories:
        return _err(
            "stories is empty",
            "send at least one {title, body, acceptance_criteria}",
        )
    try:
        result = await perform_submit(
            settings,
            worker,
            run_id,
            Submit(
                stories=stories,
                stdout=stdout or None,
                notes=notes_for_manager or None,
            ),
        )
    except HTTPException as e:
        return _err(
            str(e.detail),
            getattr(e, "hint", "check claim_work / the run id and retry"),
        )
    count = result.get("story_count", len(stories))
    return _next(
        {
            "markdown": (
                f"Story split submitted — {count} draft "
                f"{'story' if count == 1 else 'stories'} created for the "
                "manager to curate."
            ),
            **result,
        },
        (
            "get_run_status",
            "confirm the run succeeded and the stories landed",
        ),
    )


@mcp.tool(**_write("Report test results"))
async def report_test_results(
    run_id: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record pass/fail/blocked outcomes against the work item's test
    cases — their test_case_ids ride get_work_context's Test cases
    section. Each result is {test_case_id, status: passed|failed|blocked,
    evidence}. Reports show as agent-verified on the review page: passes
    lift the "unrun — merge override required" warning, failures and
    blocks keep it with your evidence attached. Re-reporting a case
    replaces your previous result for it (latest wins)."""
    settings = get_settings()
    worker = _worker()
    if not results:
        return _err(
            "results is empty",
            "send at least one {test_case_id, status, evidence}",
        )
    bad = sorted(
        {
            str(r.get("status"))
            for r in results
            if r.get("status") not in ("passed", "failed", "blocked")
        }
    )
    if bad:
        return _err(
            "invalid status value(s): " + ", ".join(bad),
            "each result's status must be passed, failed, or blocked",
        )
    if any(not str(r.get("test_case_id") or "").strip() for r in results):
        return _err(
            "every result needs a test_case_id",
            "ids are listed in get_work_context's Test cases section",
        )
    outcome = db.report_test_results(settings, run_id, worker, results)
    if outcome is None:
        return _err(
            "run not found",
            "list_my_work shows your runs; use the run_id you claimed",
        )
    if outcome.get("error"):
        hint = (
            "claim_work the run first (or report from the worker that "
            "submitted it)"
            if "claim holder" in outcome["error"]
            else "use the test_case_ids from get_work_context for this run"
        )
        return _err(outcome["error"], hint)
    return _next(
        {
            "markdown": (
                f"Recorded {outcome['recorded']} test result(s) — the "
                "review page now shows them as agent-verified."
            ),
            "test_run_id": outcome["test_run_id"],
            "recorded": outcome["recorded"],
            "ok": True,
        },
        (
            "get_pr_status",
            "the GitHub side of the PR: checks, mergeability, comments",
        ),
        ("get_run_status", "the factory's verdict on this run"),
    )


@mcp.tool(**_write("Report spec map"))
async def report_spec_map(
    run_id: str,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """US-81.5: name the automated spec that answers each test case you
    wrote specs for in this change. Each entry is {test_case_id, suite_id,
    spec_ref} — suite ids and conventions ride get_work_context's Test
    suites section; spec_ref is the stable JUnit identity your spec will
    report as (a pytest nodeid like tests/test_x.py::test_y reports
    classname::name; Playwright's junit reporter reports file::title).
    Held on the run and applied when the work item MERGES — a rejected
    changeset flips nothing. Re-reporting replaces the whole map."""
    settings = get_settings()
    worker = _worker()
    if not entries:
        return _err(
            "entries is empty",
            "send at least one {test_case_id, suite_id, spec_ref}",
        )
    missing = [
        k
        for e in entries
        for k in ("test_case_id", "suite_id", "spec_ref")
        if not str(e.get(k) or "").strip()
    ]
    if missing:
        return _err(
            "every entry needs test_case_id, suite_id and spec_ref",
            "suites are listed in get_work_context; case ids in its Test cases section",
        )
    outcome = db.store_spec_map(settings, run_id, worker, entries)
    if outcome is None:
        return _err(
            "run not found",
            "list_my_work shows your runs; use the run_id you claimed",
        )
    if outcome.get("error"):
        return _err(outcome["error"], "check ids against get_work_context")
    return _next(
        {
            "markdown": (
                f"Spec map stored ({outcome['stored']} case(s)) — applied "
                "automatically when this work item merges."
            ),
            "stored": outcome["stored"],
            "ok": True,
        },
        ("submit_changeset", "hand the change back if you haven't yet"),
    )


@mcp.tool(**_write("Report test evidence"))
async def report_test_evidence(
    run_id: str,
    command: str,
    exit_code: int,
    passed: int | None = None,
    failed: int | None = None,
    skipped: int | None = None,
    output_tail: str = "",
) -> dict[str, Any]:
    """US-81.6: record that you ran the project's pre-submit test command
    in your workspace, and what happened — command, exit code, counts, and
    the tail of the output (bounded; the last ~50 lines is plenty). Do this
    after your final code change, before submitting: the review page shows
    it beside the diff. Worker-reported and labeled as such — a review
    signal, not factory-observed proof. Re-reporting replaces it."""
    settings = get_settings()
    worker = _worker()
    if not command.strip():
        return _err("command is empty", "pass the command you actually ran")
    outcome = db.store_test_evidence(
        settings,
        run_id,
        worker,
        {
            "command": command,
            "exit_code": exit_code,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "output_tail": output_tail,
        },
    )
    if outcome is None:
        return _err(
            "run not found",
            "list_my_work shows your runs; use the run_id you claimed",
        )
    if outcome.get("error"):
        return _err(outcome["error"], "claim_work the run first")
    verdict = "passed" if exit_code == 0 else f"exit {exit_code}"
    return _next(
        {
            "markdown": f"Test evidence recorded ({verdict}).",
            "ok": True,
        },
        ("submit_changeset", "hand the change back if you haven't yet"),
    )


# US-5.5: run status → plain-vocabulary mapping. A run's own status covers
# the pre-submit states; a succeeded run's standing comes from the review
# decision that applies to it.
@mcp.tool(**_read("Get run status"))
async def get_run_status(run_id: str) -> dict[str, Any]:
    """Where a run stands: queued, claimed, in review, approved, rejected
    (with the manager's feedback), merged, or failed. Readable for any of
    your org's runs — check your own submission after submitting, or a
    retry another worker carried. A rejected run names its retry run once
    dispatched, so you can find and re-claim your own follow-up."""
    settings = get_settings()
    worker = _worker()
    view = db.get_run_status_view(settings, run_id, str(worker["org_id"]))
    if not view:
        return _err(
            "run not found",
            "list_my_work shows your runs; list_available_work shows the pool",
        )
    run, review, retry = view["run"], view["review"], view["retry"]
    kind = run["kind"]
    if run["status"] == "queued":
        status = "queued"
        detail = "Waiting unclaimed in the pool."
    elif run["status"] == "running":
        status = "claimed"
        holder = run["worker_name"] or "a worker"
        detail = (
            f"Claimed and running — held by {holder}, lease expires "
            f"{run['claim_expires_at']}."
        )
    elif run["status"] == "failed":
        status = "failed"
        detail = "The run failed" + (
            f": {run['error']}" if run.get("error") else "."
        )
    else:  # succeeded — the review decision is the standing
        decision = review["decision"] if review else None
        if decision == "approved":
            if kind == "code" and run["issue_status"] in ("merged", "done"):
                status = "merged"
                detail = "Approved and merged — this work shipped."
            else:
                status = "approved"
                detail = "Approved by the manager."
        elif decision in ("rejected", "sent-back"):
            status = "rejected"
            detail = (
                "The manager rejected this submission. A retry run "
                "carries the feedback back into the pool."
            )
        else:
            status = "in review"
            detail = "Submitted — waiting on the manager's review."
    md = (
        f"# {run['issue_title']} — {kind} run\n\n"
        f"Status: **{status}**\n\n{detail}"
    )
    out: dict[str, Any] = {
        "run_id": str(run["id"]),
        "kind": kind,
        "issue_id": str(run["issue_id"]),
        "issue_title": run["issue_title"],
        "status": status,
    }
    if kind == "code" and run.get("pr_url"):
        out["pr_url"] = run["pr_url"]
        md += f"\n\nPR: {run['pr_url']}"
    if review:
        out["review_outcome"] = review["decision"]
        if review.get("comment"):
            out["feedback"] = review["comment"]
            if status == "rejected":
                md += f"\n\n## Rejection feedback\n\n{review['comment']}"
    if status in ("rejected", "failed"):
        if retry:
            out["retry_run_id"] = str(retry["id"])
            out["retry_unclaimed"] = retry["status"] == "queued"
            if retry["status"] == "queued":
                md += (
                    f"\n\nRetry run `{retry['id']}` is in the pool, still "
                    "unclaimed — claim_work it to carry your own fix "
                    "forward."
                )
            else:
                md += (
                    f"\n\nRetry run `{retry['id']}` was dispatched and is "
                    f"no longer claimable ({retry['status']})."
                )
        else:
            md += (
                "\n\nNo retry dispatched yet — it will appear in "
                "list_available_work flagged as a retry of this run."
            )
    result = {"markdown": md, **out}
    if retry and status in ("rejected", "failed") and retry["status"] == "queued":
        return _next(
            result,
            (
                "claim_work",
                f"retry run `{retry['id']}` is unclaimed — carry your "
                "own fix forward",
            ),
        )
    result["next"] = []
    return result


# US-5.22: what get_run_status can't say — the GitHub signal that arrives
# between submit and the manager's verdict. Read-only, org-scoped the
# same way (a retry may be carried by a different worker).
MAX_PR_COMMENTS = 5

MERGEABLE_GUIDANCE = {
    "behind": "behind the default branch — rebase or merge main, then push",
    "dirty": (
        "merge conflicts with the default branch — merge main locally, "
        "resolve, and push"
    ),
    "blocked": "blocked by required checks or reviews",
    "unstable": "checks are failing or still running",
    "clean": "mergeable",
}


@mcp.tool(**_read("Get PR status"))
async def get_pr_status(run_id: str) -> dict[str, Any]:
    """The GitHub side of a submitted code run — PR state, current head,
    mergeability, CI check results, and unresolved review comments — so
    you can push a fix for a red check or answer a comment proactively
    instead of waiting blind for the manager's verdict. Readable for any
    of your org's code runs, like get_run_status."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_my_work shows your runs")
    if run["kind"] != "code":
        return _err(
            f"this is a {run['kind']} run — no PR exists",
            "get_run_status shows its review standing",
        )
    pr_url = run.get("pr_url") or ""
    if not pr_url:
        return _err(
            "no PR yet for this run",
            "submit_code_work opens the PR; get_run_status shows where "
            "the run stands",
        )
    if pr_url.startswith("simulated://"):
        return {
            "markdown": "Simulated PR — no GitHub state to report.",
            "pr_url": pr_url,
            "simulated": True,
        }
    ic = run.get("input_context") or {}
    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        return _err(e.message, "")
    checks: list[dict[str, Any]] = []
    checks_unavailable = ""
    try:
        token = await github_tokens.token_for_org(
            settings,
            str(run["org_id"]),
            ic.get("repo_full_name") or f"{owner}/{repo}",
        )
        pr = await github.get_pull(token, owner, repo, number)
        head_sha = (pr.get("head") or {}).get("sha") or ""
        if head_sha:
            # US-5.22/US-5.24: a missing Checks: read permission degrades
            # to PR-state-without-checks — the rest of the answer is
            # complete and the manager-fix note rides along.
            try:
                checks = await github.list_check_runs(
                    token, owner, repo, head_sha
                )
            except github.GitHubPermissionError as pe:
                checks_unavailable = pe.message
        threads = await github.list_review_threads(token, owner, repo, number)
    except github.GitHubError as e:
        return _github_err(e)

    state = "merged" if pr.get("merged") else (pr.get("state") or "unknown")
    mergeable_state = pr.get("mergeable_state") or "unknown"
    guidance = MERGEABLE_GUIDANCE.get(
        mergeable_state, "mergeability unknown yet — GitHub is still computing"
    )
    checks_out = [
        {
            "name": c.get("name"),
            "status": c.get("status"),
            "conclusion": c.get("conclusion"),
        }
        for c in checks
    ]
    failed = [c for c in checks_out if c["conclusion"] in ("failure", "timed_out")]
    pending = [c for c in checks_out if c["status"] != "completed"]

    unresolved = [t for t in threads if not t.get("isResolved")]
    comments_out = []
    for t in unresolved:
        for c in (t.get("comments") or {}).get("nodes") or []:
            comments_out.append(
                {
                    "author": (c.get("author") or {}).get("login"),
                    "path": c.get("path"),
                    "line": c.get("line"),
                    "body": (c.get("body") or "")[:400],
                }
            )
    total_comments = len(comments_out)
    comments_out = comments_out[:MAX_PR_COMMENTS]

    md = (
        f"# PR #{number} — {state}\n\n"
        f"- Head: `{head_sha}`\n"
        f"- Mergeability: {guidance}\n"
    )
    if checks_out:
        md += f"- Checks: {len(checks_out)} total, {len(failed)} failed, {len(pending)} pending\n"
        for c in checks_out:
            md += f"  - {c['name']}: {c['conclusion'] or c['status']}\n"
    elif checks_unavailable:
        md += f"- Checks: unavailable — {checks_unavailable}\n"
    else:
        md += "- Checks: none reported\n"
    if comments_out:
        md += f"\n## Unresolved review comments ({total_comments})\n"
        for c in comments_out:
            where = f" ({c['path']}:{c['line']})" if c.get("path") else ""
            md += f"\n- **{c['author']}**{where}: {c['body']}"
        if total_comments > len(comments_out):
            md += f"\n\n_{total_comments - len(comments_out)} more on GitHub._"
    out: dict[str, Any] = {
        "markdown": md,
        "pr_url": pr_url,
        "state": state,
        "head_sha": head_sha,
        "mergeable_state": mergeable_state,
        "mergeable_guidance": guidance,
        "checks": checks_out,
        "unresolved_comment_count": total_comments,
        "comments": comments_out,
        "simulated": False,
    }
    if checks_unavailable:
        out["checks_unavailable"] = checks_unavailable
    return out


@mcp.tool(**_write("Report progress (heartbeat)"))
async def report_progress(run_id: str, note: str = "") -> dict[str, Any]:
    """Heartbeat a long-running claim: extends your lease and (optionally)
    records a progress note the manager sees on the Workers page. Call it
    during long work so a slow-but-healthy run isn't re-pooled at lease
    expiry."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_my_work shows the runs you hold")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    extended = db.extend_claim(settings, run_id, str(worker["id"]), tool="report_progress")
    if not extended:
        return _err(
            "no live claim on this run to extend",
            "the run is no longer running — list_my_work to check",
        )
    if (note or "").strip():
        db.record_progress_note(settings, run, worker, note.strip())
    expires = str(extended["claim_expires_at"])
    md = f"Lease extended — your claim now expires {expires}."
    if (note or "").strip():
        md += " Progress note recorded for the manager."
    out: dict[str, Any] = {
        "markdown": md,
        "run_id": str(run["id"]),
        "claim_expires_at": expires,
    }
    # US-15.15: the manager asked you to stop. Surface it on the heartbeat you
    # already poll — stop making forward progress, undo what you can for this
    # run's kind (abandon a pushed branch, discard a draft artifact, remove
    # draft child stories), then call acknowledge_stop to hand the claim back
    # clean. If you can't cleanly stop, release_work and the manager will force
    # a reset.
    if run.get("stop_requested_at"):
        out["stop_requested"] = True
        out["markdown"] += (
            " ⛔ STOP REQUESTED by the manager: stop forward progress, undo your "
            "partial work with your own tools, then call acknowledge_stop."
        )
    return out


@mcp.tool(**_write("Acknowledge stop and release"))
async def acknowledge_stop(run_id: str, note: str = "") -> dict[str, Any]:
    """US-15.15: confirm you saw the manager's stop request, cleaned up your
    own partial work for this run, and are handing the claim back. Call this
    ONLY after you've actually undone what you did (abandoned the branch you
    pushed, discarded the draft artifact you wrote, removed draft child stories
    you created) — it releases your claim and returns the work item to the
    state it was in before this run started. `note` is your account of what you
    undid, shown to the manager. Refused if there's no live stop request on a
    run you hold."""
    settings = get_settings()
    worker = _worker()
    summary = db.acknowledge_stop(
        settings, run_id, str(worker["id"]), note=note or None
    )
    if summary is None:
        return _err(
            "no stop request to acknowledge on a run you hold",
            "this only applies to a running claim the manager asked to stop",
        )
    return {
        "markdown": (
            "Stop acknowledged — claim released and the work item is back in its "
            "pre-run state. Thanks for cleaning up."
        ),
        "run_id": run_id,
        "reset_to_status": summary["reset_to_status"],
    }


_TRACE_KINDS = {"step", "decision", "output", "progress", "error"}


@mcp.tool(**_write("Record run trace entry"))
async def record_trace(
    run_id: str, content: str, kind: str = "step"
) -> dict[str, Any]:
    """US-15.5: stream a detailed entry into this run's durable trace — the
    step you're taking, a decision you made and why, an output you produced, an
    error you hit — so the manager can read, long after the run ends, exactly
    what happened. Call it AS YOU WORK, not only at hand-back: a run that fails
    should still leave a trace explaining what it did before it stopped.

    `kind` is one of: step (default), decision, output, progress, error. Your
    tool calls, clarifications, and submissions are already recorded for you —
    use this for the reasoning and detail those don't capture. Entries are
    attributed to your claim automatically; you cannot write into another run's
    trace."""
    settings = get_settings()
    worker = _worker()
    k = (kind or "step").strip().lower()
    if k not in _TRACE_KINDS:
        return _err(
            f"unknown trace kind '{kind}'",
            "use one of: step, decision, output, progress, error",
        )
    if not (content or "").strip():
        return _err("content is required", "say what you did, decided, or hit")
    entry_id = db.record_run_trace(
        settings, run_id, str(worker["id"]), k, content.strip()
    )
    if entry_id is None:
        return _err(
            "you do not hold this run",
            "claim_work it first — a trace is attributed to your live claim",
        )
    return {
        "markdown": "Trace entry recorded.",
        "entry_id": entry_id,
        "run_id": run_id,
    }


@mcp.tool(**_write("Request clarification"))
async def request_clarification(
    run_id: str,
    question: str,
    options: list[dict[str, str]] | None = None,
    multi_select: bool = False,
) -> dict[str, Any]:
    """Ask the manager a question mid-run instead of guessing or releasing
    the claim. The question lands in their Things to Do; the answer is
    appended to the work item's instruction set and readable via
    get_clarifications. Your claim stays held and the lease extends like a
    heartbeat — keep working while you wait.

    When the ambiguity has a small number of concrete resolutions, OFFER
    THEM: options is [{"label": "...", "description": "..."}], 2 to 6 of
    them, and multi_select says whether more than one may be picked. The
    manager clicks instead of composing prose, and you read the choice back
    structurally rather than parsing a sentence. Keep prose for genuinely
    open questions.

    Ask one decision per question. Two decisions in one body cannot be
    answered with options, and the manager has to disentangle them."""
    settings = get_settings()
    worker = _worker()
    if not (question or "").strip():
        return _err(
            "question is required",
            "say what's ambiguous and what you need decided",
        )
    # Validate at the source: a malformed set must come back with a reason
    # the agent can act on, not render as a broken form for the manager.
    clean_options: list[dict[str, str]] | None = None
    if options:
        if not isinstance(options, list) or not 2 <= len(options) <= 6:
            return _err(
                "options must be a list of 2 to 6 choices",
                "one option is not a choice; past six, ask a different "
                "question. Omit options entirely for an open question.",
            )
        seen: set[str] = set()
        clean_options = []
        for opt in options:
            if not isinstance(opt, dict):
                return _err(
                    "each option must be an object",
                    'shape: {"label": "...", "description": "..."}',
                )
            label = str(opt.get("label") or "").strip()
            if not label:
                return _err(
                    "every option needs a label",
                    "the label is what the manager clicks",
                )
            if label.lower() in seen:
                return _err(
                    f"duplicate option label: {label}",
                    "two identical choices are not a choice",
                )
            seen.add(label.lower())
            clean_options.append(
                {
                    "label": label,
                    "description": str(opt.get("description") or "").strip(),
                }
            )
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_my_work shows the runs you hold")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    # US-59.5: bounded round-trips. Without a cap, an agent that keeps
    # re-asking after every answer — never converging — looks to the manager
    # like an endless string of interruptions on a run that never finishes,
    # even though each individual pause/resume worked correctly.
    used, cap = db.clarification_round_count(settings, run_id)
    if used >= cap:
        return _err(
            f"clarification budget exhausted ({used}/{cap} for this work item)",
            "proceed on your best judgment instead of asking again — note "
            "the assumption you made in your hand-back",
        )
    db.extend_claim(settings, run_id, str(worker["id"]), tool="request_clarification")
    row = db.add_clarification(
        settings, run, worker, question.strip(), clean_options,
        bool(multi_select) and bool(clean_options),
    )
    db.record_clarification_round(settings, run_id)
    return _next(
        {
            # US-59.5: this now actually pauses the run — end your turn, do
            # not keep working. Ending here (rather than looping on
            # get_clarifications) is what lets the runner preserve your
            # session and park it instead of racing your own turn budget for
            # an answer that may take a human a while to give.
            "markdown": (
                "Question sent — it shows in the manager's Things to Do. "
                "**End your turn now.** Do not keep working on this task: "
                "the run will park on this question and the same session "
                "resumes automatically, with the manager's answer, once "
                "they respond — nothing you do now survives past this "
                "point, and continuing only spends turns for nothing."
            ),
            "clarification_id": str(row["id"]),
            "asked_at": str(row["asked_at"]),
            "clarification_rounds_used": used + 1,
            "clarification_rounds_cap": cap,
        },
    )


@mcp.tool(**_read("Get clarifications"))
async def get_clarifications(run_id: str) -> dict[str, Any]:
    """The questions asked on your claimed run's work item and the
    manager's answers so far — this run's and earlier runs', so a retry
    claimer sees the whole exchange. Unanswered questions show as
    pending."""
    settings = get_settings()
    worker = _worker()
    run = db.get_worker_run(settings, run_id, str(worker["org_id"]))
    if not run:
        return _err("run not found", "list_my_work shows the runs you hold")
    if str(run.get("worker_id") or "") != str(worker["id"]):
        return _err("you do not hold this run", "claim_work it first")
    rows = db.list_run_clarifications(settings, run)
    items = [
        {
            "clarification_id": str(r["id"]),
            "question": r["question"],
            "answer": r["answer"],
            # US-14.9: the choice comes back structurally. Acting on an
            # answer must never depend on parsing the manager's prose.
            "options": r.get("options"),
            "multi_select": bool(r.get("multi_select")),
            "selected_options": r.get("selected_options"),
            "status": (
                "answered"
                if (r["answer"] or r.get("selected_options"))
                else "pending"
            ),
            "asked_at": str(r["asked_at"]),
            "answered_at": str(r["answered_at"]) if r["answered_at"] else None,
        }
        for r in rows
    ]
    if items:
        md = "Clarifications on this work item:\n" + "\n".join(
            f"- **Q:** {i['question']}\n  **A:** "
            + (i["answer"] if i["answer"] else "_pending — check back later_")
            for i in items
        )
    else:
        md = (
            "No clarifications asked on this work item yet. "
            "request_clarification sends the manager a question."
        )
    return {"markdown": md, "clarifications": items}


@mcp.tool(**_write("Release work"))
async def release_work(run_id: str, note: str = "") -> dict[str, Any]:
    """Hand a claim back to the pool so another worker can pick the item
    up. If you knowingly cannot proceed — missing capability, broken
    environment, work that doesn't match the claim — release WITH a note
    saying why, instead of holding the claim to lease expiry: giving up
    with a reason is a reportable outcome the manager sees; a silent
    timeout looks exactly like work until it doesn't."""
    settings = get_settings()
    worker = _worker()
    if not db.release_claim(settings, run_id, worker, note=note or None):
        return _err(
            "no live claim on this run to release",
            "only the claiming worker can release a running claim",
        )
    return _next(
        {"markdown": "Released — the item is back in the pool.", "ok": True},
        ("list_available_work", "find another item to claim"),
    )


async def _send_json(send, status: int, body: bytes) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_mcp_asgi():
    """The streamable-HTTP MCP app behind worker-token auth: X-Worker-Token
    is verified exactly like the REST endpoints; the worker rides a
    contextvar into the tools.

    Single endpoint (superseding the old US-3.14 /<org-shortname>[/<project-slug>]
    URL scoping): every worker token is scoped to at most one project via
    workers.project_id, so no path segments after /mcp are needed or
    accepted. A worker with no assigned project can still reach org-wide
    tools; project-scoped tools relying on _scoped_project will simply see
    None and error through their own "no project" path.

    All DB lookups run off the event loop via asyncio.to_thread — this
    handler runs ahead of every single MCP request (including tools/list),
    so a blocking call here stalls the whole process, not just one request.
    """
    inner = mcp.streamable_http_app()

    async def app(scope, receive, send):
        if scope["type"] != "http":
            await inner(scope, receive, send)
            return
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        settings = get_settings()
        worker = await asyncio.to_thread(
            db.get_worker_by_token, settings, headers.get("x-worker-token", "")
        )
        if not worker:
            await _send_json(
                send, 401, b'{"error": "invalid or revoked worker token"}'
            )
            return

        # Starlette's Mount keeps the full path and sets root_path to the
        # mount point (/mcp); anything after it is now unexpected.
        root_path = scope.get("root_path", "")
        raw_path = scope.get("path", "/")
        rel = raw_path[len(root_path):] if raw_path.startswith(root_path) else raw_path
        segments = [s for s in rel.split("/") if s]
        if segments:
            await _send_json(send, 404, b'{"error": "unknown factory MCP url"}')
            return

        scoped_project = (
            str(worker["project_id"]) if worker.get("project_id") else None
        )

        inner_path = (root_path or "") + "/"
        inner_scope = dict(scope)
        inner_scope["path"] = inner_path
        inner_scope["raw_path"] = inner_path.encode("latin-1")

        wtok = _current_worker.set(dict(worker))
        ptok = _scoped_project.set(scoped_project)
        try:
            await inner(inner_scope, receive, send)
        finally:
            _current_worker.reset(wtok)
            _scoped_project.reset(ptok)

    return app
