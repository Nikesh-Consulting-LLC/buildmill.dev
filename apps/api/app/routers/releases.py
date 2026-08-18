"""Release lifecycle: test results, UAT sign-off, promotion (US-21.4/21.5).

The gate that matters lives here. Promotion needs BOTH halves — the UAT
deployment actually succeeded, and every attached test case passed — because
either alone is a lie: cases approved against a UAT that is quietly down are
not a pass, and a healthy deployment nobody tested is not tested.

And promotion ships the release's PINNED commit. By the time anyone promotes,
the default branch is ahead of what UAT tested.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db, deploy
from .. import releases as releases_mod
from .. import suites as suites_pipeline
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    RpcError,
    postgrest_delete,
    postgrest_get,
    postgrest_patch,
    postgrest_post,
    rpc,
)

router = APIRouter(prefix="/releases", tags=["releases"])


async def _release_for_user(
    settings: Settings, token: str, release_id: str
) -> dict[str, Any]:
    """The release row under the caller's own JWT — RLS is the org gate, so a
    release in another org is simply not found."""
    rows = await postgrest_get(
        settings,
        token,
        "releases",
        {"select": "*", "id": f"eq.{release_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="release not found")
    return rows[0]


class ResultBody(BaseModel):
    result: Literal["pass", "fail", "blocked"]
    comment: str | None = None


@router.post("/{release_id}/test-cases/{case_id}/result")
async def record_result(
    release_id: UUID,
    case_id: UUID,
    body: ResultBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.4: one result per case per release, saved as it is clicked."""
    release = await _release_for_user(settings, user.token, str(release_id))
    cases = await postgrest_get(
        settings,
        user.token,
        "test_cases",
        {
            "select": "id",
            "id": f"eq.{case_id}",
            "release_id": f"eq.{release_id}",
            "limit": "1",
        },
    )
    if not cases:
        raise HTTPException(
            status_code=404, detail="that test case is not attached to this release"
        )
    existing = await postgrest_get(
        settings,
        user.token,
        "release_test_results",
        {
            "select": "id",
            "release_id": f"eq.{release_id}",
            "test_case_id": f"eq.{case_id}",
            "limit": "1",
        },
    )
    payload = {
        "result": body.result,
        "comment": (body.comment or "").strip() or None,
        "noted_by": user.id,
    }
    if existing:
        await postgrest_patch(
            settings,
            user.token,
            "release_test_results",
            {"id": f"eq.{existing[0]['id']}"},
            payload,
        )
    else:
        await postgrest_post(
            settings,
            user.token,
            "release_test_results",
            {
                **payload,
                "org_id": release["org_id"],
                "release_id": str(release_id),
                "test_case_id": str(case_id),
            },
        )
    blocker = await rpc(
        settings, user.token, "release_signoff_blocker", {"p_release": str(release_id)}
    )
    return {"ok": True, "signoff_blocker": blocker}


@router.get("/{release_id}/signoff-blocker")
async def signoff_blocker(
    release_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Why this release cannot be signed off yet — null when it can."""
    await _release_for_user(settings, user.token, str(release_id))
    blocker = await rpc(
        settings, user.token, "release_signoff_blocker", {"p_release": str(release_id)}
    )
    return {"blocker": blocker}


class CommentBody(BaseModel):
    comment: str | None = None


@router.post("/{release_id}/sign-off")
async def sign_off(
    release_id: UUID,
    body: CommentBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.4: the manual UAT gate. Refused unless every attached case has a
    result and none failed or blocked — `blocked` is not `passed`, because
    sign-off means the build was tested, not that testing was attempted."""
    await _release_for_user(settings, user.token, str(release_id))
    blocker = await rpc(
        settings, user.token, "release_signoff_blocker", {"p_release": str(release_id)}
    )
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    rows = await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {
            "status": "uat-signed-off",
            "signed_off_by": user.id,
            "signed_off_at": "now()",
        },
    )
    return {"ok": True, "status": "uat-signed-off", "release": (rows or [None])[0]}


@router.post("/{release_id}/reject")
async def reject(
    release_id: UUID,
    body: CommentBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.4: a release is immutable. A failed UAT is rejected and superseded
    by a NEW release — a version name means exactly one build, forever."""
    if not (body.comment or "").strip():
        raise HTTPException(status_code=422, detail="a reason is required")
    release = await _release_for_user(settings, user.token, str(release_id))
    if release["status"] in ("released", "rejected", "rolled-back"):
        raise HTTPException(
            status_code=409, detail=f'release is already {release["status"]}'
        )
    # US-103.3 AC5: rejecting a release that still has a live prep left the
    # JOB running. A zombie agent could then write notes onto a rejected
    # release and fire its UAT deploy — `release_prep.submit` gates on the
    # prep being `running`, and nothing had ever moved it off that.
    counts = await _stop_prep_runs(
        settings,
        user.token,
        str(release_id),
        user.email or "the manager",
        f"release rejected: {body.comment.strip()}",
    )
    # US-119.1: the same hole for the deploy leg — a rejected release must
    # not keep deploying to UAT.
    deploy_run = await _stop_uat_deploy(settings, release, user)

    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {
            "status": "rejected",
            "rejected_at": "now()",
            "rejected_reason": body.comment.strip(),
        },
    )
    return {"ok": True, "status": "rejected", "deploy_run": deploy_run, **counts}


# US-103.3: the states a Stop can end, and — for everything else in the
# lifecycle — the action that DOES apply, because a refusal that only says no
# is what sent the manager to the database on 2026-08-16.
#
# US-119.1 adds `deploying`. Its refusal used to read "let it finish, then
# stop or reject" — right for a pipeline that is running, and a dead end for
# one that is not: 2026.08.18.2's deploy was reaped eight seconds in and the
# release sat at `deploying` for nine and a half hours with no button on its
# page. Stop at `deploying` cancels the deploy run (cooperatively when it is
# live) and stops the release, one meaning everywhere.
STOPPABLE = ("queued", "running", "notes-ready", "deploying", "uat-deploy-failed")

_STOP_REFUSALS = {
    "uat-deployed": "this build is on UAT — reject it if testing found it "
    "bad; stop is for an attempt that never got there",
    "uat-signed-off": "this build is signed off — reject it if you no longer "
    "want it in production",
    "promoting": "the production deploy is running — let it finish, then roll "
    "it back if it is wrong",
    "released": "this build is live — roll it back rather than stopping it",
    "rejected": "this release is already rejected",
    "rolled-back": "this release was already rolled back",
    "cancelled": "this release is already stopped",
    "failed": "this release already failed — retry it, or cut a new one",
}


async def _stop_prep_runs(
    settings: Settings, token: str, release_id: str, actor: str, reason: str | None
) -> dict[str, int]:
    """US-103.3: end the JOB, not only the release.

    A queued row is deleted (US-23.1's reasoning holds — work that never began
    should not fabricate a failure in the activity feed). A running row moves
    to `cancelled`: migration 215 gave `release_prep_runs` that status and
    nothing has ever written it. Not deleted — it happened, it cost a session,
    and the release detail page's attempt list should say so.

    This is the half that makes Stop safe. `release_prep.submit` refuses any
    prep that is not `running`, so a zombie agent coming back cannot write
    notes onto a stopped release or fire its UAT deploy.
    """
    runs = await postgrest_get(
        settings,
        token,
        "release_prep_runs",
        {
            "select": "id,status,worker_id",
            "release_id": f"eq.{release_id}",
            "status": "in.(queued,running)",
        },
    )
    removed = stopped = 0
    note = f"stopped by {actor}" + (f": {reason}" if reason else "")
    for r in runs or []:
        if r["status"] == "queued" and not r.get("worker_id"):
            await postgrest_delete(
                settings, token, "release_prep_runs", {"id": f"eq.{r['id']}"}
            )
            removed += 1
        else:
            await postgrest_patch(
                settings,
                token,
                "release_prep_runs",
                {"id": f"eq.{r['id']}"},
                {
                    "status": "cancelled",
                    "finished_at": "now()",
                    "error": note[:2000],
                },
            )
            stopped += 1
    return {"runs_removed": removed, "runs_stopped": stopped}


async def _stop_uat_deploy(
    settings: Settings, release: dict[str, Any], user: AuthUser
) -> str:
    """US-119.1: end the UAT deploy run when a verdict lands on a `deploying`
    release — the same argument US-103.3 made for the prep job: Stop or
    Reject ends the release's job with the release, or a zombie pipeline
    keeps writing to UAT after the release is gone.

    Goes through `deploy.request_cancel`, so a live pipeline is cancelled
    cooperatively (US-1.35: files already transferred and script steps
    already run are NOT undone) and a dead one is marked. Returns the
    outcome word — `signalled`, `marked`, or `not-active` — and never raises
    for a release with no run to stop.
    """
    if release.get("status") != "deploying":
        return "not-active"
    run_id = release.get("uat_deployment_run_id")
    if not run_id:
        return "not-active"
    return await asyncio.to_thread(
        deploy.request_cancel,
        settings,
        str(run_id),
        user.id,
        user.email or "",
    )


@router.post("/{release_id}/cancel")
async def cancel(
    release_id: UUID,
    body: CommentBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-23.1/US-63.3, widened by US-103.3: stop a release that is going
    nowhere.

    The original restriction — queued only — was principled and turned out to
    be wrong in one respect. It reasoned that once a worker holds the prep it
    is doing real work, so the honest routes are "stop the run, or let it
    reach UAT and reject it". But release prep is not a `runs` row: there is
    no Stop-work button pointed at it anywhere, and rejecting a release stuck
    at `running` leaves the prep row live. The escape hatch it pointed at did
    not exist, which is how release 2026.08.16.3 came to be cleared by editing
    the production database.

    Stop is a verdict on the ATTEMPT — the agent died, the job hung, I changed
    my mind — and nothing was learned about the build. Reject is a verdict on
    the BUILD, and burns the version forever. Keeping them apart is why a
    release whose runner crashed ten minutes in does not enter the record as a
    rejected build.
    """
    release = await _release_for_user(settings, user.token, str(release_id))
    status = release["status"]
    if status not in STOPPABLE:
        raise HTTPException(
            status_code=409,
            detail=(
                f"release is {status} — "
                + _STOP_REFUSALS.get(status, "it cannot be stopped from here")
            ),
        )

    reason = ((body.comment or "").strip() or None) if body else None
    counts = await _stop_prep_runs(
        settings, user.token, str(release_id), user.email or "the manager", reason
    )
    # US-119.1: at `deploying` the job is the deploy pipeline, not a prep.
    # Ended BEFORE the release row moves, so a cancel that cannot reach the
    # run surfaces as the error rather than leaving a stopped release with a
    # deploy still writing to UAT. The pipeline's own late settle is guarded
    # to `deploying` and becomes a no-op once the row below lands.
    deploy_run = await _stop_uat_deploy(settings, release, user)

    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {
            "status": "cancelled",
            "cancelled_at": "now()",
            "cancelled_by": user.id,
            # Reuse the reason column; a cancellation may say nothing at all.
            "rejected_reason": reason,
        },
    )
    return {"ok": True, "status": "cancelled", "deploy_run": deploy_run, **counts}


@router.post("/{release_id}/retry")
async def retry(
    release_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-90.1: a failed release retries; a rejected one is final.

    Retry re-runs the failed ATTEMPT, never the build. A release whose notes
    prep died gets a fresh release_prep_runs row; one whose UAT deploy died
    gets a fresh deployment run. Both run against the release's stored
    version and pinned commit_sha — the endpoint takes no body, so there is
    no path by which a retry re-pins, rebuilds, or re-versions. When UAT
    showed the BUILD itself is bad, that is rejection, which stays final:
    supersede is the only road, exactly as before.
    """
    release = await _release_for_user(settings, user.token, str(release_id))
    if release["status"] not in ("failed", "uat-deploy-failed"):
        raise HTTPException(
            status_code=409,
            detail=(
                f'release is {release["status"]} — only a failed release can '
                "be retried; a rejected build is superseded, never re-run"
            ),
        )
    if release.get("promoted_at") or release.get("released_at"):
        raise HTTPException(
            status_code=409,
            detail="this release reached production — a failure there is an "
            "incident for the rollback machinery, not a retry",
        )

    attempts = await asyncio.to_thread(
        db.count_release_attempts, settings, str(release_id)
    )

    # Which leg died is a fact on the release row, not a caller choice:
    # notes never written means the prep leg; written means the deploy.
    # A completed leg is never redone.
    if not release.get("notes_written_at"):
        result = await asyncio.to_thread(
            db.dispatch_release_prep_for,
            settings,
            str(release_id),
            release["org_id"],
            user.id,
        )
        if "error" in result:
            raise HTTPException(status_code=409, detail=result["error"])
        # The reason lives on in the failed release_prep_runs row (the
        # attempt history); the release itself is in flight again.
        await asyncio.to_thread(
            db.update_release, settings, str(release_id), {"failure_reason": None}
        )
        return {
            "ok": True,
            "leg": "notes",
            "attempt": attempts["prep"] + 1,
            "prep_run_id": result["run_id"],
            "version": release["version"],
            "commit_sha": release["commit_sha"],
        }

    # Deploy leg: re-fire the deterministic pipeline at the pinned commit —
    # no agent involved, exactly as release_prep.submit fires it.
    deployment_id = await asyncio.to_thread(
        db.get_release_uat_deployment_id, settings, release["project_id"]
    )
    if not deployment_id:
        raise HTTPException(
            status_code=409,
            detail="this project has no UAT deployment designated for releases "
            "— set one on the Deployments tab",
        )
    bundle = await asyncio.to_thread(
        db.get_deployment_for_agent, settings, deployment_id, release["org_id"]
    )
    if not bundle:
        raise HTTPException(
            status_code=409, detail="the designated UAT deployment no longer exists"
        )
    try:
        run_id = deploy.launch_release_uat_deploy(
            settings,
            release,
            bundle["deployment"],
            bundle["server"],
            bundle["project"] or {},
            user.email or "manager (retry)",
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409, detail="a run is already active for that deployment"
        )
    await asyncio.to_thread(
        db.update_release,
        settings,
        str(release_id),
        {
            "status": "deploying",
            "uat_deployment_run_id": run_id,
            "failure_reason": None,
        },
    )
    return {
        "ok": True,
        "leg": "deploy",
        "attempt": attempts["deploy"] + 1,
        "deployment_run_id": run_id,
        "version": release["version"],
        "commit_sha": release["commit_sha"],
    }


@router.post("/{release_id}/promote")
async def promote(
    release_id: UUID,
    body: CommentBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.5: ship the tested build to production.

    Two paths, one gate. Where the production deployment permits agent
    deploys, this dispatches a `deploy` run — the proven us-13.13 machinery,
    pinned to the release's commit. Where it does not (a `protected`
    deployment is human-only *always*, and production needs the human-set
    `agent_dispatch_allowed`), the manager's own click runs it directly
    through the same pipeline. The rails are not weakened either way; only
    the hands differ.
    """
    release = await _release_for_user(settings, user.token, str(release_id))
    if release["status"] != "uat-signed-off":
        raise HTTPException(
            status_code=409,
            detail=(
                f'release is {release["status"]} — sign off on UAT before '
                "promoting"
            ),
        )
    projects = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "id,org_id,release_prod_deployment_id",
            "id": f"eq.{release['project_id']}",
            "limit": "1",
        },
    )
    prod_id = (projects[0] if projects else {}).get("release_prod_deployment_id")
    if not prod_id:
        raise HTTPException(
            status_code=409,
            detail="this project has no Production deployment designated for "
            "releases — set one on the Deployments tab",
        )

    bundle = await asyncio.to_thread(
        db.get_deployment_for_agent, settings, str(prod_id), release["org_id"]
    )
    if not bundle:
        raise HTTPException(
            status_code=409, detail="the designated Production deployment no longer exists"
        )
    dep = bundle["deployment"]

    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {"status": "promoting", "promoted_by": user.id, "promoted_at": "now()"},
    )

    # US-50.4: the branch cut at the pinned commit. An external production
    # deployment merges it, so what ships is the build UAT tested rather than
    # whatever the default branch has become; a release cut before us-50.4 has
    # no branch and the merge falls back to the commit.
    release_branch = releases_mod.release_branch_name(str(release["version"]))

    refusal = db.agent_deploy_refusal(dep)
    if not refusal:
        result = await asyncio.to_thread(
            db.dispatch_deploy_run,
            settings,
            str(prod_id),
            release["org_id"],
            release["commit_sha"],
            False,
            user.id,
            release_branch,
        )
        if "error" in result:
            raise HTTPException(status_code=409, detail=result["error"])
        return {
            "ok": True,
            "mode": "agent",
            "run_id": result.get("run_id"),
            "deployment": dep.get("name"),
        }

    # Human-only rail: the manager clicked Promote, so this IS their action.
    server = bundle["server"]
    project = bundle["project"] or {}
    actor = user.email or "manager"
    try:
        deployment_run_id = await asyncio.to_thread(
            deploy.create_run,
            settings,
            dep,
            user.id,
            actor,
            "branch",
            None,
            release["commit_sha"],
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409, detail="a run is already active for that deployment"
        )
    deploy.launch(
        settings,
        {
            "run_id": deployment_run_id,
            "org_id": release["org_id"],
            "deployment": dep,
            "server": server,
            "repo_full_name": project.get("repo_full_name"),
            "project_name": project.get("name") or "",
            "triggered_by": actor,
            # Pinned: production ships the build UAT tested, never the head of
            # a branch that has moved on since.
            "override": {
                "ref": release["version"],
                "sha": release["commit_sha"],
                "message": f"Release {release['version']}",
                # Read only by the merge pipeline (us-50.2): the branch to
                # open the pull request from. The SSH pipeline ignores it.
                "branch": release_branch,
            },
        },
    )
    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {"prod_deployment_run_id": deployment_run_id},
    )
    return {
        "ok": True,
        "mode": "direct",
        "reason": refusal,
        "deployment_run_id": deployment_run_id,
        "deployment": dep.get("name"),
    }


@router.post("/{release_id}/confirm-released")
async def confirm_released(
    release_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.5: mark the release live — but only against an observed deploy.

    Verifies that a production deployment run for THIS release's pinned commit
    actually succeeded, rather than taking the click as evidence.
    """
    release = await _release_for_user(settings, user.token, str(release_id))
    if release["status"] not in ("promoting", "uat-signed-off"):
        raise HTTPException(
            status_code=409, detail=f'release is {release["status"]}'
        )
    projects = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "release_prod_deployment_id",
            "id": f"eq.{release['project_id']}",
            "limit": "1",
        },
    )
    prod_id = (projects[0] if projects else {}).get("release_prod_deployment_id")
    runs = await postgrest_get(
        settings,
        user.token,
        "deployment_runs",
        {
            "select": "id,status,commit_sha,finished_at",
            "deployment_id": f"eq.{prod_id}",
            "commit_sha": f"eq.{release['commit_sha']}",
            "status": "eq.succeeded",
            "order": "finished_at.desc",
            "limit": "1",
        },
    )
    if not runs:
        raise HTTPException(
            status_code=409,
            detail="no successful production deployment of this release's "
            "commit was found — run it first",
        )
    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {
            "status": "released",
            "released_at": "now()",
            "prod_deployment_run_id": runs[0]["id"],
        },
    )
    # US-82.1: go-live confirmed is the one point both prod-deploy paths
    # converge on an observed successful deploy — so the prod-safe smoke
    # suites run now, pinned to the same commit. Fire-and-forget: a smoke
    # verdict must never fail the confirmation that just happened.
    smoke = await suites_pipeline.launch_release_suites(
        settings, str(release_id), environment="production"
    )
    return {
        "ok": True,
        "status": "released",
        "deployment_run_id": runs[0]["id"],
        "smoke_suites_launched": smoke,
    }


@router.post("/{release_id}/rolled-back")
async def mark_rolled_back(
    release_id: UUID,
    body: CommentBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-21.6: record that a released version was rolled back.

    Without this the release list claims a version is live that isn't, and
    "current in production" — which is derived from status — would be wrong.
    """
    release = await _release_for_user(settings, user.token, str(release_id))
    if release["status"] != "released":
        raise HTTPException(
            status_code=409,
            detail=f'release is {release["status"]} — only a released version '
            "can be rolled back",
        )
    await postgrest_patch(
        settings,
        user.token,
        "releases",
        {"id": f"eq.{release_id}"},
        {
            "status": "rolled-back",
            "rolled_back_at": "now()",
            "rejected_reason": (body.comment or "").strip() or None
            if body
            else None,
        },
    )
    return {"ok": True, "status": "rolled-back"}
