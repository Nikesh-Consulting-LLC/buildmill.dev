"""POST /api/v1/issues/{issue_id}/dispatch (US-1.9/US-2.1).

Dispatch is a single transactional Postgres function (dispatch_issue,
migration 005/031) called through PostgREST with the caller's own JWT —
RLS is the authorization: a non-member cannot see the issue at all.
"""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import asyncio
import logging

from .. import complexity, db, deploy, github, github_tokens, issue_sync, repo_docs
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, postgrest_get, postgrest_post, rpc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/issues", tags=["issues"])


@router.post("/{issue_id}/complexity-score")
async def complexity_score(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-7.1: score (or refine) this item's advisory complexity from whatever
    it currently has — a plan if one exists, else the story. Best-effort: any
    failure leaves the existing values untouched and never errors the caller.
    Only dispatchable items (story / bug / chore) are scored."""
    rows = await postgrest_get(
        settings,
        user.token,
        "issues",
        {"select": "type", "id": f"eq.{issue_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="issue not found")
    if rows[0]["type"] not in ("story", "bug", "chore"):
        # Features/epics aren't dispatchable — no estimate.
        return {"scored": False}
    scored = await complexity.score_and_store_issue(settings, str(issue_id))
    return {"scored": scored}


async def _sync_repo_before_dispatch(
    settings: Settings, project_id: str | None
) -> None:
    """US-22.7 / US-22.4: bring the repo up to date before the agent reads it.

    A coding agent gets the workspace as a zip pinned to a commit, so
    AGENTS.md, CLAUDE.md and docs/factory/ reach it as files on disk —
    whatever was last committed is what it obeys for the whole run. Dispatch
    is the last moment a write still reaches the agent that needs it; at
    claim time the zip may already have been cut.

    Never raises and never blocks: availability of the factory beats
    freshness of a markdown file. A failure leaves the recorded hash
    untouched, so the next dispatch retries.
    """
    if not project_id:
        return
    try:
        await repo_docs.sync_instruction_files(settings, project_id, "dispatch")
        await repo_docs.sync_tree(settings, project_id, trigger="dispatch")
    except Exception:  # noqa: BLE001 — best-effort by contract
        pass


class DispatchBody(BaseModel):
    """US-33.5: the manager's choice for THIS dispatch.

    A preset id, or nothing. It becomes the `manager` layer us-32.7 already
    resolves at the top of precedence — an explicit choice at the moment of
    dispatch is the strongest signal available and outranks the agent's standing
    default and any supervisor escalation. Absent, everything behaves exactly as
    it did: the agent's own route decides.
    """

    preset_id: str | None = None

    kind: Literal["plan", "code"] | None = None
    """The phase to run, when the caller wants to name it (migration 166).

    Absent — the overwhelmingly common case — `dispatch_issue` infers the phase
    from the issue's status exactly as it always has. Present, it is an
    instruction: "re-plan this story" on one that already holds an approved
    plan, or "build this one" on one a failed build left at `failed`. The RPC
    still enforces every rule; naming a phase only chooses between the legal
    ones.
    """


@router.post("/{issue_id}/dispatch", status_code=202)
async def dispatch(
    issue_id: UUID,
    body: DispatchBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    prior = await postgrest_get(
        settings,
        user.token,
        "issues",
        {"select": "status,project_id", "id": f"eq.{issue_id}", "limit": "1"},
    )
    prior_status = prior[0]["status"] if prior else None
    project_id = prior[0].get("project_id") if prior else None

    # US-36.2: a manual dispatch resets the attempt count. us-31.5's cap exists
    # to stop an AGENT looping unattended; the trigger still enforces that on
    # every automatic path. A manager pressing Dispatch is the deliberate
    # decision the separate release was there to force, so it should not also
    # require a second click somewhere else.
    #
    # Best-effort, like the preset write below: the reset is a convenience, not
    # a precondition. If it cannot be written, the dispatch still goes — and if
    # the item really is blocked the trigger refuses it with the message it
    # always had, which is the honest outcome. Failing the dispatch outright
    # would turn a transient database blip into "you cannot dispatch anything".
    try:
        await asyncio.to_thread(
            db.reset_issue_attempts,
            settings,
            str(issue_id),
            user.email or str(user.id),
        )
    except Exception:  # noqa: BLE001 — a dispatch must not fail over this
        logger.warning("could not reset attempts for issue %s", issue_id)

    args: dict[str, str] = {"p_issue": str(issue_id)}
    if body and body.kind:
        args["p_kind"] = body.kind

    try:
        run_id = await rpc(settings, user.token, "dispatch_issue", args)
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        # Covers "not dispatchable from status", and migration 166's
        # "not dispatchable for planning/coding from status" — a named phase
        # that is not legal from here is the same class of refusal.
        if "not dispatchable" in e.message:
            raise HTTPException(status_code=409, detail=e.message)
        if "requires an approved plan" in e.message:
            raise HTTPException(status_code=409, detail=e.message)
        if "owns the build" in e.message:
            # US-22.10: the feature owns the code build under route-feature-
            # as-one. (US-86.1 deleted the "must reach merged" refusal — a
            # dispatch is never refused for another item being in flight;
            # the serial law holds at claim time instead.)
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    # US-33.5: record the manager's choice on the run the RPC just created,
    # before any agent can claim it. Written into `input_context` because that
    # is where the resolver looks (us-32.7) — no new plumbing, and the run detail
    # already labels the value's source as `manager`.
    if body and body.preset_id and run_id:
        try:
            await asyncio.to_thread(
                db.set_manager_settings_override,
                settings,
                str(run_id),
                body.preset_id,
            )
        except Exception:  # noqa: BLE001 — a dispatch must not fail over this
            logger.warning(
                "could not record the dispatch-time preset for run %s", run_id
            )

    # After the run exists, so a GitHub outage can never stop work — and
    # after the status moved, so the story this dispatch published is
    # included in the tree it writes (US-22.4). US-69.3: in the background,
    # concurrent with the claim — the response must not wait minutes on
    # GitHub. The agent's plan and instructions travel in the run context;
    # the docs tree follows within a couple of minutes, serialized per
    # project by repo_docs' own lock.
    repo_docs.spawn_background(
        _sync_repo_before_dispatch(settings, str(project_id) if project_id else None)
    )

    if prior_status == "failed":
        # Best-effort: a redispatch out of "failed" reopens the synced
        # issue that was closed when it first failed (US-1.20).
        await issue_sync.push_issue_state_via_postgrest(
            settings, user.token, str(issue_id), "open"
        )

    return {"run_id": run_id, "status": "queued"}


class BatchDispatchBody(BaseModel):
    issue_ids: list[UUID]


def batch_order_key(issue: dict) -> tuple:
    """US-85.2: the canonical build order — epic number, then item number,
    then sub number (nulls last), then age. The 2026-08-12 incident: the
    dashboard looped checkbox-CLICK order, so story 9's plan dispatched (and
    therefore ran) ahead of stories 4–8; claim-side ordering can only
    serialize runs that exist."""
    def _n(v):  # nulls last
        return (1, 0) if v is None else (0, v)

    epic = (issue.get("epics") or {}).get("number")
    return (
        str(issue.get("project_id") or ""),
        _n(epic),
        _n(issue.get("item_no")),
        _n(issue.get("sub_no")),
        issue.get("created_at") or "",
    )


@router.post("/batch-dispatch", status_code=202)
async def batch_dispatch_ids(
    body: BatchDispatchBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-85.2: the dashboard's Dispatch selected / Dispatch all, as ONE
    request the server executes in build order with a per-item outcome.

    Each item goes through the exact single-dispatch path — attempt reset,
    `dispatch_issue` under the caller's own JWT (RLS is the authorization),
    repo-docs sync per touched project. The batch adds ordering and
    reporting, never a bypass: a refusal is recorded in `skipped` verbatim
    and the loop continues (AC4).
    """
    if not body.issue_ids:
        raise HTTPException(status_code=422, detail="issue_ids is empty")
    if len(body.issue_ids) > 100:
        raise HTTPException(
            status_code=422, detail="at most 100 items per batch"
        )

    wanted = [str(i) for i in body.issue_ids]
    rows = await postgrest_get(
        settings,
        user.token,
        "issues",
        {
            "select": "id,status,project_id,item_no,sub_no,created_at,epics(number)",
            "id": f"in.({','.join(wanted)})",
        },
    )
    by_id = {str(r["id"]): r for r in rows or []}

    dispatched: list[dict] = []
    skipped: list[dict] = []
    # Ids RLS hid (or that don't exist) are reported, not silently dropped —
    # a count that quietly shrinks is how "it won't triage all items" felt.
    for issue_id in wanted:
        if issue_id not in by_id:
            skipped.append({"id": issue_id, "reason": "not found"})

    ordered = sorted(by_id.values(), key=batch_order_key)
    for issue in ordered:
        issue_id = str(issue["id"])
        try:
            await asyncio.to_thread(
                db.reset_issue_attempts,
                settings,
                issue_id,
                user.email or str(user.id),
            )
        except Exception:  # noqa: BLE001 — same best-effort as single dispatch
            logger.warning("could not reset attempts for issue %s", issue_id)
        try:
            run_id = await rpc(
                settings, user.token, "dispatch_issue", {"p_issue": issue_id}
            )
        except RpcError as e:
            skipped.append({"id": issue_id, "reason": e.message})
            continue
        dispatched.append({"id": issue_id, "run_id": run_id})

    # One docs sync per touched project, only for work that actually went.
    touched = {str(by_id[d["id"]].get("project_id") or "") for d in dispatched}
    for project_id in sorted(p for p in touched if p):
        repo_docs.spawn_background(
            _sync_repo_before_dispatch(settings, project_id)
        )

    return {"dispatched": dispatched, "skipped": skipped}


@router.post("/{issue_id}/batch-dispatch", status_code=202)
async def batch_dispatch(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-20.5: dispatch every story in this feature, in story-id order.

    The phase is inferred from the children's rolled-up state, the same way
    `dispatch_issue` infers a kind from one issue's status — one action, not
    two endpoints to keep in sync. A child that is not dispatchable from its
    current status is reported in `skipped`, never fatal to the batch.

    US-41.1: works in every build mode. It used to refuse anything that was
    not `feature`/`epic`, which left `story`-mode projects — the default —
    dispatching a feature's stories one click at a time. The mode now only
    decides the SHAPE of the code phase: one feature-owned run carrying
    run_items, or one run per story.
    """
    # US-36.2: the same manual-dispatch reset the single dispatch does, applied
    # to the children — the trigger fires per run insert, so this must happen
    # before the RPC. It covers every child rather than only those the batch
    # will actually dispatch, because which those are is not known until the
    # RPC has already inserted (and been refused). Clearing a counter on a
    # child that is then skipped costs nothing the manager wanted to keep.
    try:
        children = await postgrest_get(
            settings,
            user.token,
            "issues",
            {"select": "id", "parent_id": f"eq.{issue_id}"},
        )
        for child in children or []:
            await asyncio.to_thread(
                db.reset_issue_attempts,
                settings,
                str(child["id"]),
                user.email or str(user.id),
            )
    except Exception:  # noqa: BLE001 — a batch must not fail over this
        logger.warning("could not reset attempts under feature %s", issue_id)

    try:
        result = await rpc(
            settings,
            user.token,
            "dispatch_feature_batch",
            {"p_feature": str(issue_id)},
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Feature not found")
        if (
            # US-41.1: the "needs build mode feature or epic" refusal is gone —
            # batching works in every mode now, and the mode only decides
            # whether the code phase is one feature-owned run or one per story.
            "no stories" in e.message
            or "abandoned" in e.message
            or "applies to a feature" in e.message
            # US-27.11: the refusal that stops a feature whose stories all
            # hold approved plans from being silently re-planned.
            or "refusing to plan" in e.message
            or "not ready to build" in e.message
        ):
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    dispatched = (result or {}).get("dispatched") or []
    skipped = (result or {}).get("skipped") or []

    # US-22.4/22.7: the batch publishes several stories at once — the tree and
    # the instruction files owe the agent the same currency as a single
    # dispatch does.
    features = await postgrest_get(
        settings,
        user.token,
        "issues",
        {"select": "project_id", "id": f"eq.{issue_id}", "limit": "1"},
    )
    if features:
        # US-69.3: backgrounded for the same reason as the single dispatch.
        repo_docs.spawn_background(
            _sync_repo_before_dispatch(
                settings, str(features[0].get("project_id") or "") or None
            )
        )

    # US-27.11: the phase this batch actually ran, and why. "Planning 6
    # stories" and "building 6 stories" are different enough that the manager
    # must never learn which one happened by reading the event log afterwards.
    return {
        "dispatched": dispatched,
        "skipped": skipped,
        "phase": (result or {}).get("phase"),
        "phase_reason": (result or {}).get("phase_reason"),
        "story_count": (result or {}).get("story_count"),
    }


@router.get("/{issue_id}/attempts")
async def attempts(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-31.5: what a blocked item says about itself — attempts spent, which
    agents spent them, and the last error verbatim. Read-gated by RLS: the
    caller must be able to see the issue at all."""
    rows = await postgrest_get(
        settings, user.token, "issues", {"select": "id", "id": f"eq.{issue_id}"}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Issue not found")
    return db.issue_attempt_summary(settings, str(issue_id))


@router.post("/{issue_id}/attempts/release")
async def release_attempts(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-31.5: the manager's explicit release — clears the block and the
    attempt history so the item starts counting again. A decision with a
    record, not a silent retry. Dispatching afterwards is a separate action,
    deliberately: releasing says "try again", not "go now"."""
    rows = await postgrest_get(
        settings, user.token, "issues", {"select": "id", "id": f"eq.{issue_id}"}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Issue not found")
    if not db.release_attempt_block(
        settings, str(issue_id), actor=user.email or str(user.id)
    ):
        raise HTTPException(
            status_code=409, detail="this item is not blocked on attempts"
        )
    return {"ok": True}


@router.post("/{issue_id}/revert")
async def revert(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    issues_rows = await postgrest_get(
        settings, user.token, "issues", {"select": "id,status,title", "id": f"eq.{issue_id}"}
    )
    if not issues_rows:
        raise HTTPException(status_code=404, detail="Issue not found")
    issue = issues_rows[0]
    if issue["status"] != "merged":
        raise HTTPException(
            status_code=409, detail=f'issue is not merged (status "{issue["status"]}")'
        )

    events = await postgrest_get(
        settings,
        user.token,
        "issue_events",
        {
            "select": "org_id,payload",
            "issue_id": f"eq.{issue_id}",
            "type": "eq.merged",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if not events:
        raise HTTPException(status_code=409, detail="No merged PR recorded for this issue")
    pr_url = events[0]["payload"].get("pr_url")
    org_id = events[0]["org_id"]
    if not pr_url or pr_url.startswith("simulated://"):
        raise HTTPException(status_code=409, detail="No real GitHub PR to revert (simulated)")

    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        token = await github_tokens.token_for_user(
            settings, user.token, org_id, f"{owner}/{repo}"
        )
    except github.GitHubError:
        raise HTTPException(status_code=409, detail="No GitHub connection for this org")

    try:
        pull = await github.get_pull(token, owner, repo, number)
        revert_url = await github.revert_pull_request(
            token, pull["node_id"], f'Revert "{issue["title"]}"'
        )
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)

    await postgrest_post(
        settings,
        user.token,
        "issue_events",
        {
            "org_id": org_id,
            "issue_id": str(issue_id),
            "type": "reverted",
            "payload": {"revert_pr_url": revert_url},
        },
    )

    return {"revert_pr_url": revert_url}


@router.get("/{issue_id}/deployments")
async def issue_deployments(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.48: where is this issue's merged change live? For each of the
    project's deployments, whether the currently deployed payload contains
    the issue's merge commit. Read-only; GitHub failures degrade to
    'unknown' — the page is never blocked."""
    issues = await postgrest_get(
        settings, user.token, "issues",
        {"select": "id,project_id,org_id", "id": f"eq.{issue_id}", "limit": "1"},
    )
    if not issues:
        raise HTTPException(status_code=404, detail="Issue not found")
    project_id = issues[0]["project_id"]
    org_id = issues[0]["org_id"]

    runs = await postgrest_get(
        settings, user.token, "runs",
        {
            "select": "id,pr_url,merge_commit_sha",
            "issue_id": f"eq.{issue_id}",
            "order": "created_at.desc",
        },
    )
    merge_sha = next((r["merge_commit_sha"] for r in runs if r.get("merge_commit_sha")), None)

    projects = await postgrest_get(
        settings, user.token, "projects",
        {"select": "repo_full_name", "id": f"eq.{project_id}", "limit": "1"},
    )
    repo_full_name = projects[0]["repo_full_name"] if projects else ""

    token = None
    if not merge_sha:
        # Backfill best-effort from the stored pr_url (pre-us-1.48 merges).
        candidate = next((r for r in runs if r.get("pr_url")), None)
        if candidate and repo_full_name and not str(candidate["pr_url"]).startswith("simulated"):
            try:
                owner, repo, number = github.parse_pr_url(candidate["pr_url"])
                token = await github_tokens.token_for_user(
                    settings, user.token, org_id, f"{owner}/{repo}"
                )
                pull = await github.get_pull(token, owner, repo, number)
                merge_sha = pull.get("merge_commit_sha") if pull.get("merged") else None
                if merge_sha:
                    await asyncio.to_thread(
                        deploy.set_run_merge_sha, settings, str(candidate["id"]), merge_sha
                    )
            except Exception:
                merge_sha = None
    if not merge_sha:
        return {"merge_sha": None, "deployments": []}

    deployments = await postgrest_get(
        settings, user.token, "deployments",
        {"select": "id,name,current_run_id", "project_id": f"eq.{project_id}", "order": "name"},
    )
    owner, repo = (repo_full_name or "/").split("/", 1)
    results = []
    for dep in deployments:
        entry = {"id": dep["id"], "name": dep["name"], "state": "never", "since": None}
        current_id = dep.get("current_run_id")
        if current_id:
            run = await asyncio.to_thread(deploy.get_run, settings, str(current_id))
            if not run:
                entry["state"] = "unknown"
            elif run["source"] == "zip":
                entry["state"] = "zip"
            else:
                try:
                    if token is None:
                        token = await github_tokens.token_for_user(
                            settings, user.token, org_id, f"{owner}/{repo}"
                        )
                    cmp = await github.compare_commits(
                        token, owner, repo, merge_sha, str(run["commit_sha"])
                    )
                    contained = cmp.get("status") in ("identical", "ahead")
                    entry["state"] = "deployed" if contained else "not-deployed"
                    if contained:
                        entry["since"] = str(run.get("finished_at") or "")
                except Exception:
                    entry["state"] = "unknown"
        results.append(entry)
    return {"merge_sha": merge_sha, "deployments": results}
