"""Approve / reject a run (US-1.12, US-1.13).

Both decisions are transactional Postgres functions called with the
caller's JWT (RLS authorizes). Approve merges the PR first: real PRs via
the GitHub API, using a token resolved by github_tokens (see its
preference order, US-3.15); simulated PRs are a no-op.
"""

import asyncio
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db, github, github_tokens, issue_sync
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..errors import ReportedHTTPException
from ..supabase import RpcError, postgrest_get, postgrest_patch, rpc

router = APIRouter(prefix="/runs", tags=["reviews"])

SIMULATED_PREFIX = "simulated://"


def _map_rpc_error(e: RpcError) -> HTTPException:
    if "not found" in e.message:
        return HTTPException(status_code=404, detail=e.message)
    if "not in review" in e.message or "required" in e.message:
        return HTTPException(status_code=409, detail=e.message)
    return HTTPException(status_code=400, detail=e.message)


class MergeConflict(Exception):
    """Raised when GitHub can't merge a PR because it's dirty (real conflict
    with the base branch), as opposed to any other merge failure (blocked
    checks, permissions, etc). Carries enough to route the manager to a
    dedicated resolution flow instead of a generic 409."""

    def __init__(
        self, pr_url: str, pr_number: int, base_branch: str, files: list[str]
    ):
        self.pr_url = pr_url
        self.pr_number = pr_number
        self.base_branch = base_branch
        self.files = files


async def _merge_pr(
    settings: Settings,
    user_token: str,
    org_id: str,
    pr_url: str | None,
    merge_method: str = "squash",
) -> str:
    """Merge the run's PR. Simulated PRs (and missing tokens) skip GitHub.
    Resolves the credential from **the run's own org** via github_tokens
    (US-76.4; see its preference order, US-3.15).

    Squash is right for a work item's PR — one item, one commit. us-98.6
    passes `merge` for a merge run: squashing would collapse every source
    branch's history into a single new commit and destroy the only record of
    where those changes came from, which is the same reason release PRs to
    `prod` are never squashed."""
    if not pr_url or pr_url.startswith(SIMULATED_PREFIX):
        return "simulated"

    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    context = {"pr_url": pr_url, "owner": owner, "repo": repo, "pr_number": number}
    try:
        token, credential = await github_tokens.resolve_for_user(
            settings, user_token, org_id, f"{owner}/{repo}"
        )
    except github.GitHubNotConfigured as e:
        # Truly no connection (and no env fallback) — configuration the
        # manager hasn't done yet, not a dependency failing.
        raise HTTPException(status_code=409, detail=e.message)
    except github.GitHubError as e:
        # US-79.2 (prod BUG-2): a connection EXISTS and its credential failed
        # (Vault secret gone, App token mint refused). The old blanket answer —
        # "No GitHub connection" — sent the manager off to create a connection
        # they already had. The taxonomy's own message names the cure.
        raise ReportedHTTPException(status_code=409, detail=e.message, context=context)

    try:
        merge_sha = await github.merge_pull_request(
            token, owner, repo, number, merge_method
        )
    except github.GitHubError as e:
        status = getattr(e, "upstream_status", None)
        if status == 401:
            # US-79.2 (prod BUG-2): "Bad credentials" is GitHub rejecting the
            # credential, not the merge — say which one was used and the cure.
            raise ReportedHTTPException(
                status_code=409,
                detail=(
                    f"GitHub rejected {credential} (401 Bad credentials) while "
                    f"merging {owner}/{repo}#{number} — reconnect GitHub in "
                    "Settings → GitHub"
                ),
                context={**context, "connection": credential, "upstream_status": 401},
            )
        # One probe answers every remaining branch: merged by hand, closed by
        # hand, a true conflict, or invisible to the credential.
        try:
            pull = await github.get_pull(token, owner, repo, number)
        except github.GitHubError:
            pull = None
        if pull and pull.get("merged"):
            # US-79.2 (prod BUG-3): merged by hand on GitHub — the outcome
            # approve wanted. Reconcile as success instead of refusing what
            # already happened; the real merge SHA keeps traceability.
            sha = pull.get("merge_commit_sha") or ""
            return f"already-merged:{sha}" if sha else "already-merged"
        if pull and pull.get("state") == "closed":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{owner}/{repo}#{number} was closed on GitHub without "
                    "merging — reopen it there, or reject this run"
                ),
            )
        if status == 404:
            # US-79.2 (prod BUG-3): the PR is invisible to this credential —
            # GitHub answers 404 for unauthorized reads, so "not found" and
            # "no access" are the same answer. Name the credential; the grant
            # is the thing to check.
            raise ReportedHTTPException(
                status_code=409,
                detail=(
                    f"GitHub answered 404 for {owner}/{repo}#{number}: the PR "
                    f"does not exist or {credential} cannot see the repository "
                    "— check the connection's repository access in "
                    "Settings → GitHub"
                ),
                context={**context, "connection": credential, "upstream_status": 404},
            )
        if pull and pull.get("mergeable_state") == "dirty":
            # A true conflict gets a dedicated outcome the manager can act on
            # directly (US-76.2).
            try:
                files_json = await github.list_pull_files(token, owner, repo, number)
            except github.GitHubError:
                files_json = []
            raise MergeConflict(
                pr_url=pr_url,
                pr_number=number,
                base_branch=(pull.get("base") or {}).get("ref") or "the base branch",
                files=[f.get("filename") for f in files_json if f.get("filename")],
            )
        # US-76.2: `e.message` already reads "GitHub merge failed: ..." — the
        # prefix belongs to `merge_pull_request`, which is the only layer that
        # knows the operation was a merge. Adding it again here is what made
        # the toast say it twice.
        # US-76.1: GitHub refusing a merge is a dependency failure wearing a
        # 409, not pipeline state, so it goes to the superadmin's console.
        raise ReportedHTTPException(
            status_code=409,
            detail=e.message,
            context={**context, "connection": credential},
        )
    return f"merged:{merge_sha}" if merge_sha else "merged"


@router.post("/{run_id}/force-requeue")
async def force_requeue(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-13.6: one-click recovery for a run whose worker went silent —
    release the claim and return the run to the pool for another worker.
    The RLS-scoped read is the org gate; the write is the same requeue
    the lease-expiry sweep performs."""
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "id,status,worker_id", "id": f"eq.{run_id}", "limit": "1"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail="only a running (claimed) run can be requeued",
        )
    ok = db.force_requeue_run(
        settings,
        str(run_id),
        note="requeued by the manager — the worker went silent",
    )
    if not ok:
        raise HTTPException(
            status_code=409, detail="the run is no longer running — refresh"
        )
    return {"ok": True, "status": "queued"}


@router.post("/{run_id}/reset")
async def reset_run(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-15.14: reset a wrongly-started run — discard this attempt's draft
    output, return the issue to its pre-dispatch status, and re-queue the run
    for a fresh claim. Unlike force-requeue this isn't gated on the worker
    looking stuck; it works on any active (queued or running) run. The
    RLS-scoped read is the org gate; the write is the service-role cleanup."""
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "id,status", "id": f"eq.{run_id}", "limit": "1"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] not in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail="only an active (queued or running) run can be reset",
        )
    summary = db.reset_run(
        settings, str(run_id), actor=user.email or "manager"
    )
    if summary is None:
        raise HTTPException(
            status_code=409, detail="the run is no longer active — refresh"
        )
    return {"ok": True, "status": "queued", **summary}


class Cancel(BaseModel):
    reason: str


@router.post("/{run_id}/cancel")
async def cancel(
    run_id: UUID,
    body: Cancel,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-27.10: retire a run that should not have been dispatched.

    Distinct from its two neighbours, and the difference is the whole point:
    **pause** keeps a run in the queue, **reset** sends it back to the pool to
    be claimed again, **cancel** ends it. The work item returns to the status
    it held before the dispatch, and the run stays readable in the item's
    history with its reason — deleting it would make a mis-dispatch
    unexplainable.

    A queued run is cancelled outright; a running one is asked to stop
    cooperatively and lands `cancelled` when its worker hands back."""
    if not body.reason.strip():
        raise HTTPException(
            status_code=422,
            detail="a reason is required — the queue is a shared surface",
        )
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "id,status", "id": f"eq.{run_id}", "limit": "1"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] not in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail=f"a {runs[0]['status']} run is already over — nothing to cancel",
        )
    summary = db.cancel_run(
        settings, str(run_id), body.reason, actor=user.email or "manager"
    )
    if summary is None:
        raise HTTPException(
            status_code=409, detail="the run is no longer active — refresh"
        )
    return {"ok": True, **summary}


@router.post("/{run_id}/request-stop")
async def request_stop(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-15.15: ask the working agent to stop and clean up after itself. The
    request rides the agent's next report_progress heartbeat; the agent undoes
    its own partial work and calls acknowledge_stop. If it never cooperates,
    the manager still has the forced reset (us-15.14) as the guarantee. Only a
    running (claimed) run can be asked — a queued run is just reset."""
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "id,status", "id": f"eq.{run_id}", "limit": "1"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] != "running":
        raise HTTPException(
            status_code=409,
            detail="only a running (claimed) run can be asked to stop — a "
            "queued run is reset instead",
        )
    if not db.request_run_stop(settings, str(run_id)):
        raise HTTPException(
            status_code=409, detail="the run is no longer running — refresh"
        )
    return {"ok": True, "stop_requested": True}


@router.post("/{run_id}/approve")
async def approve(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    # RLS scopes this read; empty result means not ours / doesn't exist.
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "pr_url,issue_id,kind,org_id", "id": f"eq.{run_id}"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="Run not found")

    # approve_run re-checks these transactionally, but by then a real PR
    # would already be squash-merged on GitHub — guard before the
    # irreversible call so a stale approve can't merge without recording.
    #
    # US-40.1: the guard asks the DATABASE the same question approve_run will
    # ask, instead of a narrower version of it. It used to read `runs.issue_id`
    # alone — which for a feature batch is the FEATURE, and a feature can sit
    # at `in-review` while its member stories do not. On 2026-07-28 that gap
    # merged PR #12 into `main` and then refused to record the approval,
    # leaving the code shipped and the factory unaware. One predicate, one
    # place: `approve_run_precheck` is what `approve_run` itself raises from.
    if (runs[0].get("kind") or "code") != "code":
        raise HTTPException(status_code=409, detail="approve_run only applies to code runs")
    try:
        refusal = await rpc(
            settings, user.token, "approve_run_precheck", {"p_run": str(run_id)}
        )
    except RpcError as e:
        raise _map_rpc_error(e)
    if refusal:
        raise HTTPException(status_code=409, detail=refusal)

    try:
        merge_result = await _merge_pr(
            settings,
            user.token,
            runs[0]["org_id"],
            runs[0]["pr_url"],
            # us-98.6: a merge keeps every source commit reachable.
            "merge" if runs[0].get("kind") == "merge" else "squash",
        )
    except MergeConflict as e:
        # Nothing else has run yet (approve_run_precheck only reads) — the
        # issue is still in-review, untouched. Return a normal 200 with a
        # structured payload the UI special-cases into a conflict panel,
        # instead of throwing a generic 409 the manager can't act on.
        return {
            "ok": False,
            "merge_conflict": True,
            "pr_url": e.pr_url,
            "base_branch": e.base_branch,
            "files": e.files,
        }

    # US-1.48: record the squash-merge commit so deploy changelogs can map
    # commits back to the issue that shipped them. `already-merged:` (US-79.2)
    # is the same traceability for a PR merged by hand on GitHub.
    if merge_result.startswith(("merged:", "already-merged:")):
        prefix, merge_sha = merge_result.split(":", 1)
        merge_result = prefix
        try:
            await postgrest_patch(
                settings,
                user.token,
                "runs",
                {"id": f"eq.{run_id}"},
                {"merge_commit_sha": merge_sha},
            )
        except Exception:
            pass  # traceability is best-effort; the merge itself succeeded

    try:
        await rpc(settings, user.token, "approve_run", {"p_run": str(run_id)})
    except RpcError as e:
        # US-40.1: the precheck passed and the merge has already happened, so
        # reaching here means a race (a status moved underneath us) or a
        # transient fault. Either way the code is on the default branch and the
        # approval is not recorded. Mark the run so that split state is visible
        # in the app instead of only on GitHub, and let `finish-approval` close
        # it without calling GitHub again. A simulated merge merged nothing and
        # has nothing to reconcile.
        if merge_result != "simulated":
            try:
                await postgrest_patch(
                    settings,
                    user.token,
                    "runs",
                    {"id": f"eq.{run_id}"},
                    {
                        "merged_unapproved_at": datetime.now(timezone.utc).isoformat()
                    },
                )
            except Exception:
                pass  # the refusal below is the thing that must not be lost
        raise _map_rpc_error(e)

    # US-81.5: the merge is real — apply the run's case→spec map, flipping
    # the mapped cases to automated. Best-effort like the traceability write.
    if merge_result != "simulated":
        try:
            await asyncio.to_thread(db.apply_spec_map, settings, str(run_id))
        except Exception:
            pass

    # Best-effort: a synced issue's GitHub issue closes when it merges (US-1.20).
    await issue_sync.push_issue_state_via_postgrest(
        settings, user.token, str(runs[0]["issue_id"]), "closed"
    )

    return {"ok": True, "merge": merge_result}


@router.post("/{run_id}/finish-approval")
async def finish_approval(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-40.1: record the approval for a run whose PR merged but whose
    approval then failed to land.

    GitHub is deliberately not called. The merge already happened and cannot
    happen twice, which is exactly why the normal approve path cannot repair
    this state — it would fail EARLIER, at `_merge_pr`, on a PR GitHub already
    considers merged. This endpoint runs the recording half alone.
    """
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {
            "select": "id,kind,issue_id,merged_unapproved_at",
            "id": f"eq.{run_id}",
            "limit": "1",
        },
    )
    if not runs:
        raise HTTPException(status_code=404, detail="Run not found")
    if not runs[0].get("merged_unapproved_at"):
        raise HTTPException(
            status_code=409,
            detail=(
                "this run is not merged-but-unapproved — approve it the normal "
                "way"
            ),
        )

    try:
        await rpc(settings, user.token, "approve_run", {"p_run": str(run_id)})
    except RpcError as e:
        raise _map_rpc_error(e)

    if runs[0].get("issue_id"):
        await issue_sync.push_issue_state_via_postgrest(
            settings, user.token, str(runs[0]["issue_id"]), "closed"
        )
    return {"ok": True, "merge": "already-merged"}


class Abandon(BaseModel):
    reason: str


@router.post("/{run_id}/abandon")
async def abandon(
    run_id: UUID,
    body: Abandon,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-59.7: close out a parked run on purpose — a distinct terminal fact
    from `cancel` (a queued/running mis-dispatch) and from `failed` (broke on
    its own): this one the manager chose to stop. Releases the preserved
    workspace the same way the automatic TTL sweep (us-59.8) does, through
    the identical `db.abandon_run` path — one code path for "we're done with
    this", manual or automatic, never two that could disagree."""
    if not body.reason.strip():
        raise HTTPException(
            status_code=422,
            detail="a reason is required — the queue is a shared surface",
        )
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "id,org_id,status", "id": f"eq.{run_id}", "limit": "1"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] not in ("paused", "awaiting_input"):
        raise HTTPException(
            status_code=409,
            detail=f"a {runs[0]['status']} run is not parked — nothing to abandon",
        )
    ok = db.abandon_run(
        settings,
        str(run_id),
        str(runs[0]["org_id"]),
        reason=body.reason.strip(),
        member={"id": user.id, "name": user.email},
    )
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="no longer parked — it may already be resuming; refresh",
        )
    return {"ok": True, "status": "abandoned"}


@router.post("/{run_id}/resume")
async def resume_stopped(
    run_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-59.3: the manager's explicit approval to continue a spend-ceiling
    `stopped` run. Deliberately manual and separate from `abandon`/`cancel`
    — resuming past a ceiling must never be silent, or the ceiling stops
    meaning anything. Requires a captured session id; a `stopped` run from
    before Phase 59 has nothing to resume into and this refuses with 409."""
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {
            "select": "id,org_id,status,claude_session_id",
            "id": f"eq.{run_id}",
            "limit": "1",
        },
    )
    if not runs:
        raise HTTPException(status_code=404, detail="run not found")
    if runs[0]["status"] != "stopped":
        raise HTTPException(
            status_code=409,
            detail=f"a {runs[0]['status']} run is not stopped — nothing to resume",
        )
    if not runs[0].get("claude_session_id"):
        raise HTTPException(
            status_code=409,
            detail="this run has no captured session to resume — it predates "
            "session resume, or never got far enough to report one",
        )
    ok = db.mark_stopped_resumable(
        settings,
        str(run_id),
        str(runs[0]["org_id"]),
        {"id": user.id, "name": user.email},
    )
    if not ok:
        raise HTTPException(
            status_code=409, detail="no longer stopped — refresh"
        )
    return {"ok": True, "status": "paused"}


class Rejection(BaseModel):
    comment: str


@router.post("/{run_id}/reject")
async def reject(
    run_id: UUID,
    body: Rejection,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    if not body.comment.strip():
        raise HTTPException(status_code=422, detail="A comment is required to reject")
    try:
        await rpc(
            settings,
            user.token,
            "reject_run",
            {"p_run": str(run_id), "p_comment": body.comment.strip()},
        )
    except RpcError as e:
        raise _map_rpc_error(e)

    return {"ok": True}


class ConflictRejection(BaseModel):
    direction: str | None = None


@router.post("/{run_id}/reject-conflict")
async def reject_conflict(
    run_id: UUID,
    body: ConflictRejection,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Send a PR that failed to merge on a real conflict back to the coding
    agent, with clear direction. Re-derives the conflict fresh from GitHub
    (rather than trusting whatever the client's Approve attempt saw) so a
    conflict resolved manually in the meantime doesn't get redispatched for
    nothing. Reuses reject_run/dispatch_issue unchanged — dispatch_issue
    already forwards the rejection comment verbatim into the redispatched
    run's input_context.feedback, so the MERGE CONFLICT framing below reaches
    the agent with no further plumbing."""
    runs = await postgrest_get(
        settings,
        user.token,
        "runs",
        {"select": "pr_url,kind,org_id", "id": f"eq.{run_id}"},
    )
    if not runs:
        raise HTTPException(status_code=404, detail="Run not found")
    pr_url = runs[0].get("pr_url")
    if not pr_url or pr_url.startswith(SIMULATED_PREFIX):
        raise HTTPException(
            status_code=409, detail="This run has no real PR to check for conflicts"
        )

    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        token = await github_tokens.token_for_user(
            settings, user.token, runs[0]["org_id"], f"{owner}/{repo}"
        )
        pull = await github.get_pull(token, owner, repo, number)
    except github.GitHubError as e:
        raise HTTPException(status_code=409, detail=f"Could not reach GitHub: {e.message}")

    if pull.get("mergeable_state") != "dirty":
        raise HTTPException(
            status_code=409,
            detail="This PR is no longer showing a merge conflict — try Approve again.",
        )

    try:
        files_json = await github.list_pull_files(token, owner, repo, number)
    except github.GitHubError:
        files_json = []
    files = [f.get("filename") for f in files_json if f.get("filename")]
    base_branch = (pull.get("base") or {}).get("ref") or "the base branch"

    direction = (body.direction or "").strip()
    lines = [
        f"MERGE CONFLICT — GitHub could not merge this PR into `{base_branch}`.",
        "",
        "Pull the latest base branch, resolve the conflicts locally, and push "
        "an updated commit. Do not resubmit the same diff unchanged.",
    ]
    if files:
        lines += ["", "Files this PR touched (most likely where conflicts are "
                  "— git is authoritative):"]
        lines += [f"- {f}" for f in files]
    lines += ["", f"Manager direction: {direction or 'none provided — use your judgement'}"]
    comment = "\n".join(lines)

    try:
        await rpc(
            settings,
            user.token,
            "reject_run",
            {"p_run": str(run_id), "p_comment": comment},
        )
    except RpcError as e:
        raise _map_rpc_error(e)

    return {"ok": True}
