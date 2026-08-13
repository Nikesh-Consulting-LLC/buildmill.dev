"""Phase-2 orchestration: PRD, story breakdown, plan gates, release sign-off.

Thinking-LLM steps (PRD draft, story split) use the org LLM when configured,
otherwise fall back to deterministic simulation so the pipeline is testable
without a live model. The coding agent remains the runner (plan/code runs).
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import (
    artifacts_sim,
    complexity,
    db,
    llm,
    repo_docs,
    wireframe_docs,
    wireframes,
)
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..llm import LlmCallError, LlmNotConfigured
from ..llm import complete as llm_complete
from ..supabase import (
    postgrest_get,
    postgrest_patch,
    postgrest_post,
    rpc,
    RpcError,
)

router = APIRouter(tags=["workflow"])

logger = logging.getLogger(__name__)


async def _sync_docs_tree(
    settings: Settings,
    token: str,
    issue: dict[str, Any],
    trigger: str,
) -> dict[str, Any]:
    """US-13.4: project the approved state into the repo docs tree.
    Best-effort by contract — a write failure (GitHub unreachable,
    permissions, protected branch) is surfaced as an issue event and in
    the response, and NEVER fails the approval that triggered it.

    US-15.1: a *successful* write is recorded too (a `docs-written` event
    carrying the commit), so the outcome — not only the failure — lands in
    the issue timeline and the manager never has to open GitHub to learn
    whether it worked. The web app also surfaces it inline from the
    approval response's `docs_tree` field."""
    try:
        result = await repo_docs.sync_tree(
            settings, str(issue["project_id"]), trigger=trigger
        )
    except Exception as e:  # noqa: BLE001 — the approval already stands
        detail = str(e)[:300]
        try:
            await _event(
                settings,
                token,
                issue["org_id"],
                str(issue["id"]),
                "docs-write-failed",
                {"error": detail, "trigger": trigger},
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "docs-write-failed event not recorded for issue %s", issue["id"]
            )
        return {
            "error": detail,
            "retry": "POST /projects/{project_id}/docs-tree/sync",
        }

    # A real commit (not a skip because the tree is off/repo-less, and not an
    # unchanged no-op) is worth a durable record. Skips and no-ops stay quiet.
    if result.get("commit_sha") and not result.get("skipped") and not result.get(
        "unchanged"
    ):
        try:
            await _event(
                settings,
                token,
                issue["org_id"],
                str(issue["id"]),
                "docs-written",
                {
                    "commit_sha": result["commit_sha"],
                    "trigger": trigger,
                    "file_count": len(result.get("files") or []),
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "docs-written event not recorded for issue %s", issue["id"]
            )
    return result


async def _get_issue(settings: Settings, token: str, issue_id: str) -> dict[str, Any]:
    rows = await postgrest_get(
        settings,
        token,
        "issues",
        {
            "select": "id,org_id,project_id,type,title,body,acceptance_criteria,status,parent_id,epic_id,abandoned_at,breakdown_mode,breakdown_instructions",
            "id": f"eq.{issue_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Issue not found")
    return rows[0]


async def _event(
    settings: Settings,
    token: str,
    org_id: str,
    issue_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    await postgrest_post(
        settings,
        token,
        "issue_events",
        {
            "org_id": org_id,
            "issue_id": issue_id,
            "type": event_type,
            "payload": payload,
        },
    )


async def _approval(
    settings: Settings,
    token: str,
    *,
    org_id: str,
    issue_id: str,
    gate: str,
    decision: str,
    actor: str,
    subject_type: str | None = None,
    subject_id: str | None = None,
    comment: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = await postgrest_post(
        settings,
        token,
        "approvals",
        {
            "org_id": org_id,
            "issue_id": issue_id,
            "gate": gate,
            "decision": decision,
            "actor": actor,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "comment": comment,
            "payload": payload,
        },
    )
    return rows[0] if isinstance(rows, list) else rows


async def _llm_or_simulate(
    settings: Settings,
    token: str,
    *,
    function_key: str,
    system: str,
    user: str,
    simulate: Any,
) -> Any:
    """Try the org LLM via the routing resolver (US-3.17); simulation only
    when no usable default provider exists, never for a failed call."""
    try:
        result = await llm_complete(
            settings,
            token,
            function_key,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return result.text
    except LlmNotConfigured:
        return simulate()
    except LlmCallError:
        raise
    except Exception as e:
        raise LlmCallError(str(e)) from e


# ---------------------------------------------------------------- PRD


@router.post("/issues/{issue_id}/prd/draft")
async def draft_prd(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-3.21: dispatches a `kind='prd'` run into the worker pool instead
    of calling the LLM inline — the runner claims it by default, or a
    connected human/agent worker can claim it first."""
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_prd_draft", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        if "only for feature" in e.message or "cannot draft PRD" in e.message:
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return {"run_id": run_id, "status": "queued"}


@router.post("/issues/{issue_id}/elaboration/dispatch")
async def dispatch_elaboration(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-44.1: put an agent on fleshing out ONE story against the codebase.

    Never automatic and never required — no gate, dispatch or auto-approve
    path queues this on its own. A story can still go draft → curate → plan
    without one; this is a lever the manager reaches for on a story that
    reads thin, before the plan run spends $5–15 on it."""
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_elaboration", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        if (
            "abandoned" in e.message
            or "already in flight" in e.message
            or "elaborated by its PRD" in e.message
        ):
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return {"run_id": run_id, "status": "queued"}


class WireframeDispatch(BaseModel):
    """A redo carries the manager's comment; a first draw carries nothing."""

    feedback: str | None = None


@router.post("/issues/{issue_id}/wireframe/dispatch")
async def dispatch_wireframe(
    issue_id: UUID,
    body: WireframeDispatch | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-48.2: put an agent on drawing ONE story, before it is planned.

    The same endpoint is the redo: `feedback` is the manager's comment on what
    was wrong with the last one, and it reaches the agent alongside the
    wireframe it is replacing. There is no gate to send back through — a
    sketch is not a contract — so this is the whole of the manager's control
    loop over a wireframe."""
    feedback = (body.feedback if body else None) or None
    try:
        run_id = await rpc(
            settings,
            user.token,
            "dispatch_wireframe",
            {"p_issue": str(issue_id), "p_feedback": feedback},
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        if (
            "abandoned" in e.message
            or "already in flight" in e.message
            or "drawn by drawing its stories" in e.message
        ):
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return {"run_id": run_id, "status": "queued", "redo": bool(feedback)}


@router.post("/issues/{issue_id}/wireframes/batch-dispatch")
async def dispatch_wireframe_batch(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-48.3: one wireframe run per story under this feature.

    A feature's stories are slices of one surface, and the value of drawing
    them is seeing the screens together. Abandoned, in-flight and already-drawn
    children come back in `skipped` with a reason rather than failing the
    batch, and a feature whose every child is skipped is a no-op the response
    explains — not an error."""
    try:
        result = await rpc(
            settings,
            user.token,
            "dispatch_wireframe_batch",
            {"p_feature": str(issue_id)},
        )
    except RpcError as e:
        if "not found" in e.message:
            raise HTTPException(status_code=404, detail="Feature not found")
        if "abandoned" in e.message or "applies to a feature" in e.message:
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)

    return result


@router.get("/issues/{issue_id}/wireframe/preview", response_class=HTMLResponse)
async def wireframe_preview(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-48.2: the wireframe as one self-contained page.

    The app drops this into a sandboxed iframe. It has to be self-contained
    because that frame is sandboxed WITHOUT `allow-same-origin` — its origin
    is opaque, so it can resolve nothing back to the app — and it renders
    through the same kit.js the repository gets, rather than a second
    renderer the manager's picture could drift from."""
    issue = await _get_issue(settings, user.token, str(issue_id))
    artifact = db.get_current_wireframe(settings, str(issue_id))
    if not artifact:
        raise HTTPException(status_code=404, detail="This story has no wireframe")

    declaration = wireframe_docs.declaration_of(artifact)
    if declaration.get("no_ui_surface"):
        raise HTTPException(
            status_code=409,
            detail=declaration.get("reason") or "This story has no UI surface",
        )

    project = db.get_project_docs_config(settings, str(issue["project_id"]))
    tokens_css = None
    if project:
        tokens_css, _ = await wireframe_docs.tokens_for_project(settings, project)

    meta = db.get_issue_for_wireframe(settings, str(issue_id)) or {}
    display = (
        db.work_item_display_id(
            meta.get("type"),
            meta.get("epic_number"),
            meta.get("item_no"),
            meta.get("sub_no"),
        )
        or str(issue_id)[:8]
    )
    return HTMLResponse(
        wireframes.build_preview(
            display, issue.get("title") or "", declaration, tokens_css
        ),
        headers={"Cache-Control": "no-store"},
    )


class PrdDecision(BaseModel):
    comment: str | None = None


class PrdApprove(BaseModel):
    """US-2.28: how the feature should break into stories — saved on the
    feature as standing values and echoed into the approval's payload."""

    breakdown_mode: str | None = Field(
        default=None, pattern="^(automatic|single|multiple)$"
    )
    breakdown_instructions: str | None = None


@router.post("/issues/{issue_id}/prd/approve")
async def approve_prd(
    issue_id: UUID,
    body: PrdApprove | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    issue = await _get_issue(settings, user.token, str(issue_id))
    if issue["status"] != "prd-review":
        raise HTTPException(status_code=409, detail="issue is not in prd-review")
    # Standing values: what the dialog chose, falling back to what the
    # feature already carries (pre-us-2.28 features behave as 'automatic').
    mode = (body.breakdown_mode if body else None) or (
        issue.get("breakdown_mode") or "automatic"
    )
    instructions = (
        body.breakdown_instructions
        if body and body.breakdown_instructions is not None
        else issue.get("breakdown_instructions")
    )
    instructions = (instructions or "").strip() or None
    drafts = await postgrest_get(
        settings,
        user.token,
        "artifacts",
        {
            "select": "id,version",
            "issue_id": f"eq.{issue_id}",
            "kind": "eq.prd",
            "status": "eq.draft",
            "order": "version.desc",
            "limit": "1",
        },
    )
    if not drafts:
        raise HTTPException(status_code=409, detail="no draft PRD to approve")
    art = drafts[0]
    await postgrest_patch(
        settings,
        user.token,
        "artifacts",
        {"id": f"eq.{art['id']}"},
        {"status": "approved"},
    )
    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="prd",
        decision="approved",
        actor=user.id,
        subject_type="artifact",
        subject_id=art["id"],
        payload={
            "breakdown_mode": mode,
            "breakdown_instructions": instructions,
        },
    )
    await postgrest_patch(
        settings,
        user.token,
        "issues",
        {"id": f"eq.{issue_id}"},
        {
            "status": "ready",
            "breakdown_mode": mode,
            "breakdown_instructions": instructions,
        },
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "prd-approved",
        {"artifact_id": art["id"], "version": art["version"]},
    )
    # US-69.3: the docs-tree commit is minutes of serial GitHub calls and the
    # approval must not wait on it. The outcome still reaches the manager as a
    # docs-written / docs-write-failed event on the issue timeline.
    repo_docs.spawn_background(
        _sync_docs_tree(settings, user.token, issue, "PRD approved")
    )
    return {
        "status": "ready",
        "artifact_id": art["id"],
        "breakdown_mode": mode,
        "docs_tree": {"deferred": True},
    }


@router.post("/issues/{issue_id}/prd/send-back")
async def send_back_prd(
    issue_id: UUID,
    body: PrdDecision,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-2.27: send-back records the feedback AND puts the refinement in
    motion — a new `kind='prd'` run is dispatched carrying the sent-back
    draft and the comment (dispatch_prd_draft assembles both)."""
    if not body.comment or not body.comment.strip():
        raise HTTPException(status_code=422, detail="comment is required")
    issue = await _get_issue(settings, user.token, str(issue_id))
    if issue["status"] != "prd-review":
        raise HTTPException(status_code=409, detail="issue is not in prd-review")
    active = await postgrest_get(
        settings,
        user.token,
        "runs",
        {
            "select": "id,status",
            "issue_id": f"eq.{issue_id}",
            "kind": "eq.prd",
            "status": "in.(queued,running)",
            "limit": "1",
        },
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=(
                "a PRD run is already queued or running — wait for it to "
                "land (or release it) before sending back"
            ),
        )
    drafts = await postgrest_get(
        settings,
        user.token,
        "artifacts",
        {
            "select": "id",
            "issue_id": f"eq.{issue_id}",
            "kind": "eq.prd",
            "status": "eq.draft",
            "order": "version.desc",
            "limit": "1",
        },
    )
    subject_id = drafts[0]["id"] if drafts else None
    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="prd",
        decision="sent-back",
        actor=user.id,
        subject_type="artifact" if subject_id else None,
        subject_id=subject_id,
        comment=body.comment.strip(),
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "prd-sent-back",
        {"comment": body.comment.strip()},
    )
    # The sent-back draft stays readable as a prior version but is no longer
    # the live draft — superseded now, not at next submit, so the panel drops
    # into the "Drafting…" state and the next submit lands as a new version.
    if subject_id:
        await postgrest_patch(
            settings,
            user.token,
            "artifacts",
            {"id": f"eq.{subject_id}"},
            {"status": "superseded"},
        )
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_prd_draft", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        # The send-back itself stands: feedback recorded, no live draft, so
        # Draft PRD is visible again and a manual redispatch carries this
        # same comment as feedback. Don't fail the request.
        return {"status": "prd-review", "run_id": None, "dispatch_error": e.message}
    return {"status": "prd-review", "run_id": run_id}


# --------------------------------------------------------------------------
# US-44.1: the elaboration gate.
#
# The proposal an `elaborate` run hands back is an artifact, never an in-place
# edit — so the manager gets a before, an after and a decision, which is the
# whole reason the plan and PRD gates exist. These two endpoints are that
# decision, and they mirror the PRD gate deliberately: one send-back behaviour
# in the app, not two.
# --------------------------------------------------------------------------


async def _latest_elaboration_draft(
    settings: Settings, token: str, issue_id: str
) -> dict[str, Any] | None:
    rows = await postgrest_get(
        settings,
        token,
        "artifacts",
        {
            "select": "id,content,version",
            "issue_id": f"eq.{issue_id}",
            "kind": "eq.elaboration",
            "status": "eq.draft",
            "order": "version.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


@router.post("/issues/{issue_id}/elaboration/approve")
async def approve_elaboration(
    issue_id: UUID,
    body: PrdDecision | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Apply the proposed story text and criteria to the issue.

    US-44.1: approving an elaboration IS curation. A manager who has read the
    proposal closely enough to accept it has done what us-15.3's gate asks
    for; making them then press *Curate* would be two clicks meaning one
    thing. So a story still at `draft` moves to `ready` — and a story already
    past `draft` keeps whatever status it has.
    """
    issue = await _get_issue(settings, user.token, str(issue_id))
    draft = await _latest_elaboration_draft(settings, user.token, str(issue_id))
    if not draft:
        raise HTTPException(
            status_code=409, detail="no elaboration is awaiting a decision"
        )

    try:
        proposal = json.loads(draft["content"])
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=422, detail="the elaboration artifact is not readable"
        )

    if not proposal.get("proposes_change"):
        raise HTTPException(
            status_code=409,
            detail=(
                "this elaboration proposes no change — there is nothing to "
                "apply. Dismiss it, or send it back with what you wanted."
            ),
        )

    previous_body = issue.get("body") or ""
    previous_criteria = issue.get("acceptance_criteria") or []
    new_body = (proposal.get("story") or "").strip() or previous_body
    new_criteria = proposal.get("acceptance_criteria") or previous_criteria

    patch: dict[str, Any] = {
        "body": new_body,
        "acceptance_criteria": new_criteria,
    }
    # Curation, subsumed — and ONLY from draft. Anything further along keeps
    # the status it earned.
    if issue["status"] == "draft":
        patch["status"] = "ready"

    await postgrest_patch(
        settings, user.token, "issues", {"id": f"eq.{issue_id}"}, patch
    )
    await postgrest_patch(
        settings,
        user.token,
        "artifacts",
        {"id": f"eq.{draft['id']}"},
        {"status": "approved"},
    )
    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="elaboration",
        decision="approved",
        actor=user.id,
        subject_type="artifact",
        subject_id=draft["id"],
        comment=(body.comment.strip() if body and body.comment else None),
    )
    # The PREVIOUS text, so the replaced wording is recoverable from the
    # timeline rather than only from the superseded artifact chain.
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "elaborated",
        {
            "previous_body": previous_body,
            "previous_acceptance_criteria": previous_criteria,
            "curated": patch.get("status") == "ready",
        },
    )
    # US-69.3: deferred — see approve_prd. The timeline events carry the outcome.
    repo_docs.spawn_background(
        _sync_docs_tree(settings, user.token, issue, "elaboration approved")
    )
    return {
        "status": patch.get("status", issue["status"]),
        "curated": patch.get("status") == "ready",
        "docs_tree": {"deferred": True},
    }


@router.post("/issues/{issue_id}/elaboration/send-back")
async def send_back_elaboration(
    issue_id: UUID,
    body: PrdDecision,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Record the feedback and put the next pass in motion — the same shape
    as the PRD gate's send-back, so there is one send-back behaviour here."""
    if not body.comment or not body.comment.strip():
        raise HTTPException(status_code=422, detail="comment is required")
    issue = await _get_issue(settings, user.token, str(issue_id))
    active = await postgrest_get(
        settings,
        user.token,
        "runs",
        {
            "select": "id,status",
            "issue_id": f"eq.{issue_id}",
            "kind": "eq.elaborate",
            "status": "in.(queued,running)",
            "limit": "1",
        },
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=(
                "an elaboration run is already queued or running — wait for "
                "it to land before sending back"
            ),
        )
    draft = await _latest_elaboration_draft(settings, user.token, str(issue_id))
    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="elaboration",
        decision="sent-back",
        actor=user.id,
        subject_type="artifact" if draft else None,
        subject_id=draft["id"] if draft else None,
        comment=body.comment.strip(),
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "elaboration-sent-back",
        {"comment": body.comment.strip()},
    )
    if draft:
        await postgrest_patch(
            settings,
            user.token,
            "artifacts",
            {"id": f"eq.{draft['id']}"},
            {"status": "superseded"},
        )
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_elaboration", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        # The send-back stands: the feedback is recorded and no live draft
        # remains, so the action is offered again and a manual dispatch
        # carries this same comment as feedback.
        return {"status": issue["status"], "run_id": None, "dispatch_error": e.message}
    return {"status": issue["status"], "run_id": run_id}


class ArtifactPatch(BaseModel):
    content: str = Field(min_length=1)


@router.patch("/artifacts/{artifact_id}")
async def patch_artifact(
    artifact_id: UUID,
    body: ArtifactPatch,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    rows = await postgrest_get(
        settings,
        user.token,
        "artifacts",
        {"select": "id,status", "id": f"eq.{artifact_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="artifact not found")
    if rows[0]["status"] != "draft":
        raise HTTPException(status_code=409, detail="only draft artifacts are editable")
    updated = await postgrest_patch(
        settings,
        user.token,
        "artifacts",
        {"id": f"eq.{artifact_id}"},
        {"content": body.content},
    )
    return updated[0] if isinstance(updated, list) else updated


# -------------------------------------------------------- story breakdown


@router.post("/issues/{issue_id}/breakdown/dispatch")
async def dispatch_breakdown_run(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-2.33: breakdown joins the worker pool. Dispatches a
    ``kind='breakdown'`` run for a ready feature (approved PRD, no
    children); a worker claims it over MCP, reads the PRD + repo +
    guidelines + learnings, and hands the split back with submit_stories,
    which auto-creates the child stories as drafts. Replaces the old
    synchronous propose/accept endpoints."""
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_breakdown", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        if "issue not found" in e.message:
            raise HTTPException(status_code=404, detail="Issue not found")
        if (
            "only a feature" in e.message
            or "only a ready feature" in e.message
            or "approved PRD required" in e.message
            or "already has children" in e.message
            or "already in progress or complete" in e.message
            or "abandoned" in e.message
        ):
            raise HTTPException(status_code=409, detail=e.message)
        raise HTTPException(status_code=400, detail=e.message)
    return {"run_id": run_id, "status": "queued"}


# ------------------------------------------------------- reset (US-68.1)


class ResetIssueRequest(BaseModel):
    stage: str
    destination_status: str | None = None
    note: str | None = None


@router.post("/issues/{issue_id}/reset")
async def reset_issue(
    issue_id: UUID,
    body: ResetIssueRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-68.1: send this item back to a chosen stage — PRD (Feature only),
    Elaboration, Planning, or Dispatch for Coding — replacing US-15.17's
    always-to-Triage full reset. Pre-merge only — RevertButton owns the
    post-merge path. Never touches GitHub: no branch is deleted, no PR is
    closed, by any stage of this action."""
    try:
        summary = db.reset_issue_to_stage(
            settings,
            str(issue_id),
            body.stage,
            destination_status=body.destination_status,
            note=body.note,
            actor=user.email or "manager",
        )
    except db.ResetBlocked as e:
        raise HTTPException(status_code=409, detail=str(e))
    except db.ResetStageError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if summary is None:
        raise HTTPException(status_code=404, detail="Issue not found")

    return {"ok": True, **summary}


# -------------------------------------------------------------- plan gate


async def _materialize_test_plan(
    settings: Settings, token: str, issue: dict[str, Any], test_plan_content: str
) -> int:
    cases = artifacts_sim.parse_test_plan_cases(test_plan_content)
    # A re-approved plan replaces the issue's materialized cases — abandon
    # the prior agent set first, or every re-plan doubles the library.
    await postgrest_patch(
        settings,
        token,
        "test_cases",
        {
            "issue_id": f"eq.{issue['id']}",
            "source": "eq.agent",
            "status": "eq.active",
        },
        {"status": "abandoned"},
    )
    count = 0
    for tc in cases:
        await postgrest_post(
            settings,
            token,
            "test_cases",
            {
                "org_id": issue["org_id"],
                "project_id": issue["project_id"],
                "issue_id": issue["id"],
                "title": tc.get("title") or "Untitled test",
                "steps": tc.get("steps") or "",
                "expected_result": tc.get("expected_result") or "",
                "source": "agent",
                "test_types": tc.get("test_types") or [],
                "environments": tc.get("environments") or ["dev"],
                # US-81.5: an automated-marked case means the coding agent
                # delivers the spec as part of the change and reports the
                # case→spec link at submit (report_spec_map).
                "execution": tc.get("execution") or "manual",
            },
        )
        count += 1
    return count


class PlanDecision(BaseModel):
    comment: str | None = None


class TestDispatchRequest(BaseModel):
    instructions: str | None = None


@router.post("/issues/{issue_id}/test-run/dispatch", status_code=202)
async def dispatch_test_run(
    issue_id: UUID,
    body: TestDispatchRequest = TestDispatchRequest(),
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-13.11: send the submitted code run's branch for staffed
    verification — a `test`-kind pool run offered only to workers granted
    the `test` capability. Explicit dispatch, never automatic; refuses
    when nothing has been submitted, there are no active test cases to run,
    or one is already in flight. `instructions` is an optional free-text
    note from the manager (e.g. "focus on the mobile layout") layered on top
    of the issue's authored test cases — it never changes what a worker is
    allowed to report results against."""
    from .. import db

    issue = await _get_issue(settings, user.token, str(issue_id))
    result = db.dispatch_test_run(
        settings,
        str(issue_id),
        issue["org_id"],
        actor=user.id,
        instructions=body.instructions,
    )
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"ok": True, "run_id": result["run_id"]}


async def _approve_plan_for_issue(
    settings: Settings,
    user: AuthUser,
    issue: dict[str, Any],
    sync_docs: bool = True,
) -> dict[str, Any]:
    """US-20.6: the body of the plan gate, shared by the single-issue endpoint
    and the feature-level batch, so approving nine plans does exactly what
    approving one does — same artifact patches, same approval rows, same test
    plan materialization, same event, same docs-tree sync.

    This is four writes wide — artifacts, approvals, test cases, the issue —
    and it is not a transaction, so a network blip partway through used to
    leave the gate permanently wedged: the artifacts were already `approved`,
    so every retry found no draft and answered 409 forever, with the issue
    still sitting in plan-review. That happened on 2026-08-10 (a
    `httpcore.ConnectTimeout` on the third test-case insert), and the only way
    out was editing the database by hand.

    So the query accepts an already-`approved` artifact too. That is not a
    second approval sneaking in: the caller only reaches here while the issue
    is in plan-review, and a fresh plan run supersedes prior draft/approved
    plan artifacts before writing its own (`db.py`), so an approved artifact
    on a plan-review issue can only be a half-applied approve. Resuming one
    re-patches nothing and re-approves nothing — it just finishes the job.
    """
    issue_id = issue["id"]
    arts = await postgrest_get(
        settings,
        user.token,
        "artifacts",
        {
            "select": "id,kind,content,version,status",
            "issue_id": f"eq.{issue_id}",
            "status": "in.(draft,approved)",
            "or": "(kind.eq.plan,kind.eq.test_plan)",
        },
    )
    if not any(a["kind"] == "plan" for a in arts):
        raise HTTPException(status_code=409, detail="no draft plan to approve")

    # Each write is skipped on its own evidence, not on the one before it —
    # the crash can land between the artifact patch and its approval row, and
    # inferring "already recorded" from the artifact's status would drop the
    # manager's decision from the audit trail in exactly that window.
    recorded = await postgrest_get(
        settings,
        user.token,
        "approvals",
        {
            "select": "subject_id",
            "issue_id": f"eq.{issue_id}",
            "gate": "eq.plan",
            "decision": "eq.approved",
        },
    )
    already_approved = {r["subject_id"] for r in recorded or []}

    test_content = ""
    for a in arts:
        if a.get("status") != "approved":
            await postgrest_patch(
                settings,
                user.token,
                "artifacts",
                {"id": f"eq.{a['id']}"},
                {"status": "approved"},
            )
        # A second approval row is a lie in the audit trail — it says the
        # manager decided twice.
        if a["id"] not in already_approved:
            await _approval(
                settings,
                user.token,
                org_id=issue["org_id"],
                issue_id=str(issue_id),
                gate="plan",
                decision="approved",
                actor=user.id,
                subject_type="artifact",
                subject_id=a["id"],
            )
        if a["kind"] == "test_plan":
            test_content = a["content"]

    materialized = 0
    if test_content:
        materialized = await _materialize_test_plan(
            settings, user.token, issue, test_content
        )

    await postgrest_patch(
        settings,
        user.token,
        "issues",
        {"id": f"eq.{issue_id}"},
        {"status": "planned"},
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "plan-approved",
        {"materialized_test_cases": materialized},
    )
    # US-69.3: the code run this approval unlocks is dispatched by the next
    # click (or the same one, with "Approve & build") — minutes of serial
    # GitHub calls here delayed every code run by that much. The agent's own
    # plan travels in the run context; the docs tree follows in the background
    # and its outcome lands on the issue timeline.
    # US-86.1 AC5b: the feature-level batch passes sync_docs=False and commits
    # the tree ONCE for the whole batch instead of once per story.
    if sync_docs:
        repo_docs.spawn_background(
            _sync_docs_tree(settings, user.token, issue, "plan approved")
        )
    return {
        "status": "planned",
        "materialized_test_cases": materialized,
        "docs_tree": {"deferred": True},
    }


@router.post("/issues/{issue_id}/plan/approve")
async def approve_plan(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    issue = await _get_issue(settings, user.token, str(issue_id))
    if issue["status"] != "plan-review":
        raise HTTPException(status_code=409, detail="issue is not in plan-review")
    return await _approve_plan_for_issue(settings, user, issue)


@router.post("/issues/{issue_id}/plans/approve-all")
async def approve_all_plans(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-20.6: clear the plan gate for every child story of this feature that
    is sitting in plan-review.

    Only ever approves — a send-back stays a per-story decision, because one
    comment cannot honestly describe what is wrong with nine different plans.
    A child that is not in plan-review is skipped, not failed; a child whose
    approval errors is reported with its reason and the rest still go through.
    """
    feature = await _get_issue(settings, user.token, str(issue_id))
    if feature.get("type") != "feature":
        raise HTTPException(
            status_code=409, detail="batch plan approval applies to a feature"
        )

    children = await postgrest_get(
        settings,
        user.token,
        "issues",
        {
            "select": "id,org_id,project_id,type,title,status,item_no,sub_no,parent_id",
            "parent_id": f"eq.{issue_id}",
            "abandoned_at": "is.null",
            "order": "sub_no.asc",
        },
    )

    approved: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for child in children or []:
        if child["status"] != "plan-review":
            skipped.append(
                {"issue_id": child["id"], "reason": f'status is "{child["status"]}"'}
            )
            continue
        try:
            result = await _approve_plan_for_issue(
                settings, user, child, sync_docs=False
            )
        except HTTPException as e:
            skipped.append({"issue_id": child["id"], "reason": str(e.detail)})
            continue
        approved.append(
            {
                "issue_id": child["id"],
                "materialized_test_cases": result["materialized_test_cases"],
            }
        )

    # US-86.1 AC5b: one repo docs commit for the whole batch — the tree's
    # content is identical to what N per-story syncs would have left.
    if approved:
        repo_docs.spawn_background(
            _sync_docs_tree(settings, user.token, feature, "plans approved (batch)")
        )

    return {"approved": approved, "skipped": skipped}


@router.post("/issues/{issue_id}/plan/send-back")
async def send_back_plan(
    issue_id: UUID,
    body: PlanDecision,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    if not body.comment or not body.comment.strip():
        raise HTTPException(status_code=422, detail="comment is required")
    issue = await _get_issue(settings, user.token, str(issue_id))
    if issue["status"] != "plan-review":
        raise HTTPException(status_code=409, detail="issue is not in plan-review")

    # Return to pre-dispatch status from last plan-dispatched event
    events = await postgrest_get(
        settings,
        user.token,
        "issue_events",
        {
            "select": "payload",
            "issue_id": f"eq.{issue_id}",
            "type": "eq.plan-dispatched",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    back_to = "draft"
    if events:
        back_to = events[0].get("payload", {}).get("from_status") or "draft"
    if back_to not in ("draft", "ready", "failed", "planned"):
        back_to = "draft"

    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="plan",
        decision="sent-back",
        actor=user.id,
        comment=body.comment.strip(),
    )
    await postgrest_patch(
        settings,
        user.token,
        "issues",
        {"id": f"eq.{issue_id}"},
        {"status": back_to},
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "plan-sent-back",
        {"comment": body.comment.strip(), "status": back_to},
    )
    return {"status": back_to}


@router.post("/issues/{issue_id}/replan", status_code=202)
async def replan(
    issue_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Start a fresh plan run, superseding prior plan artifacts on completion."""
    issue = await _get_issue(settings, user.token, str(issue_id))
    if issue["status"] in ("queued", "running", "planning", "in-review"):
        raise HTTPException(status_code=409, detail="issue has an active run")

    # US-11.2: a feature is never planned directly — its rail is
    # Draft -> PRD -> Stories. This endpoint used to move a feature to
    # 'ready' and call dispatch_issue, which is exactly how a feature plan
    # run could reach the pool. Re-doing a feature's thinking means
    # redrafting its PRD, not planning it.
    if issue["type"] == "feature":
        raise HTTPException(
            status_code=409,
            detail=(
                "a feature is not planned directly — send its PRD back for a "
                "redraft, or re-plan its stories individually"
            ),
        )

    # Drop approved plan so dispatch_issue creates a plan run.
    arts = await postgrest_get(
        settings,
        user.token,
        "artifacts",
        {
            "select": "id",
            "issue_id": f"eq.{issue_id}",
            "or": "(kind.eq.plan,kind.eq.test_plan)",
            "status": "eq.approved",
        },
    )
    for a in arts:
        await postgrest_patch(
            settings,
            user.token,
            "artifacts",
            {"id": f"eq.{a['id']}"},
            {"status": "superseded"},
        )

    # Move to a dispatchable status for plan. Features are refused above,
    # so this is always a story/bug/chore going back to 'draft'.
    target = "draft"
    if issue["status"] not in ("draft", "ready", "failed", "planned", "needs-fixes"):
        raise HTTPException(
            status_code=409, detail=f'cannot re-plan from status "{issue["status"]}"'
        )
    await postgrest_patch(
        settings,
        user.token,
        "issues",
        {"id": f"eq.{issue_id}"},
        {"status": target},
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "re-planned",
        {},
    )
    try:
        run_id = await rpc(
            settings, user.token, "dispatch_issue", {"p_issue": str(issue_id)}
        )
    except RpcError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"run_id": run_id, "status": "queued"}


# -------------------------------------------------------- merge override


class MergeOverride(BaseModel):
    reason: str = Field(min_length=1)
    run_id: UUID


@router.post("/issues/{issue_id}/merge-override")
async def merge_override(
    issue_id: UUID,
    body: MergeOverride,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Record a soft merge-override approval before the normal approve path."""
    issue = await _get_issue(settings, user.token, str(issue_id))
    await _approval(
        settings,
        user.token,
        org_id=issue["org_id"],
        issue_id=str(issue_id),
        gate="merge-override",
        decision="approved",
        actor=user.id,
        subject_type="run",
        subject_id=str(body.run_id),
        comment=body.reason.strip(),
    )
    await _event(
        settings,
        user.token,
        issue["org_id"],
        str(issue_id),
        "merge-override",
        {"run_id": str(body.run_id), "reason": body.reason.strip()},
    )
    return {"ok": True}


# ------------------------------------------------------ release sign-off


