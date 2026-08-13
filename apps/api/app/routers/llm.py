"""Thinking-task LLM endpoints (US-1.14 / US-1.16 / US-1.21 / US-1.51).

Vault-held keys never reach the browser — every call resolves settings
under the caller's JWT and reads the secret server-side.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..llm import (
    LLM_FUNCTIONS,
    LlmCallError,
    LlmNotConfigured,
    elaborate_test_case,
    generate_deploy_script,
    merge_learnings,
    summarize_content,
    summarize_work_item,
)
from ..supabase import (
    RpcError,
    admin_patch,
    postgrest_get,
    postgrest_upsert,
    rpc,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/llm", tags=["llm"])


@router.get("/functions")
async def list_llm_functions(user: AuthUser = Depends(verify_token)):
    """US-3.17: the backend-owned registry of routable thinking functions.
    The settings UI renders this list; routes reference these keys."""
    return [
        {"key": key, "label": meta["label"], "description": meta["description"]}
        for key, meta in LLM_FUNCTIONS.items()
    ]


class TldrRequest(BaseModel):
    content: str = Field(min_length=1, max_length=60000)
    kind: str = Field(default="content")


@router.post("/tldr")
async def tldr(
    body: TldrRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-18.1: a very short, summary-only TLDR (headline + bullets) of a
    work-item content block, via the org's configured LLM. The browser posts
    the content; the key never leaves the server."""
    try:
        return await summarize_content(
            settings, user.token, body.content, body.kind
        )
    except LlmNotConfigured:
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured — set one up in Settings.",
        )
    except LlmCallError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e.message}")


# --------------------------------------------------------------------------
# US-25.3: TLDR of a whole work item.
#
# The us-18.1 endpoint above summarizes one content block the browser posts up.
# This one answers the manager's actual question — "what IS this item" — from
# everything the item carries, and it does so out of band: the request never
# waits on a model round trip. The popup polls; it sees `generating`, then
# `ready`, or `failed` with a reason it can retry from.
# --------------------------------------------------------------------------

# In-flight and failed generations, keyed by issue id. Deliberately in-process
# and lossy: it exists so a spinner can become an error message, not as a job
# store. After a restart the next poll simply starts a fresh generation, which
# is the correct behaviour anyway.
_TLDR_RUNS: dict[str, dict[str, Any]] = {}
# Strong refs to detached tasks — asyncio only holds weak ones, and a
# garbage-collected task is a summary that silently never arrives.
_TLDR_TASKS: set[asyncio.Task] = set()


def _source_hash(sources: list[tuple[str, str]]) -> str:
    """Over the source texts that feed the summary, headings included.

    A hash rather than a timestamp comparison, for the reason us-22.7 gives:
    it survives an edit that cancels out, it does not treat a failed generation
    as current, and it makes reopening an untouched item free.
    """
    h = hashlib.sha256()
    for heading, text in sources:
        h.update(heading.encode())
        h.update(b"\0")
        h.update((text or "").encode())
        h.update(b"\0")
    return h.hexdigest()


async def _collect_sources(
    settings: Settings, token: str, issue: dict[str, Any]
) -> tuple[str, list[tuple[str, str]], list[str]]:
    """What a summary of this item is made of — scoped by type.

    A feature and a story are different questions: "what is this change" versus
    "what am I being asked to build". Feeding a story's plan into a feature
    summary would bury the PRD.
    """
    is_feature = issue.get("type") in ("feature", "epic")
    kinds = ["prd"] if is_feature else ["plan"]
    artifacts = await postgrest_get(
        settings,
        token,
        "artifacts",
        {
            "select": "kind, content, version",
            "issue_id": f"eq.{issue['id']}",
            "kind": f"in.({','.join(kinds)})",
            "order": "version.desc",
        },
    )
    latest: dict[str, str] = {}
    for a in artifacts or []:
        if a["kind"] not in latest and (a.get("content") or "").strip():
            latest[a["kind"]] = a["content"]

    sources: list[tuple[str, str]] = []
    missing: list[str] = []

    def add(heading: str, text: str | None, missing_label: str) -> None:
        if text and str(text).strip():
            sources.append((heading, str(text)))
        else:
            missing.append(missing_label)

    if is_feature:
        add("Description", issue.get("body"), "its description")
        add("Approved PRD", latest.get("prd"), "an approved PRD")
        return "feature", sources, missing

    add("Story", issue.get("body"), "its story text")
    criteria = issue.get("acceptance_criteria")
    criteria_text = ""
    if isinstance(criteria, list):
        criteria_text = "\n".join(f"- {c}" for c in criteria if str(c).strip())
    elif criteria:
        criteria_text = str(criteria)
    add("Acceptance criteria", criteria_text, "acceptance criteria")
    add("Approved plan", latest.get("plan"), "an approved plan")
    add("Instruction set", issue.get("instruction_set"), "an instruction set")
    return "story", sources, missing


async def _generate_tldr(
    settings: Settings,
    issue_id: str,
    org_id: str,
    type_label: str,
    sources: list[tuple[str, str]],
    missing: list[str],
    digest: str,
) -> None:
    """The out-of-band half: call the model, store the result, and record a
    failure so the popup can show it instead of spinning forever."""
    try:
        result = await summarize_work_item(
            settings, org_id, type_label, sources, missing
        )
        await admin_patch(
            settings,
            "issues",
            {"id": f"eq.{issue_id}"},
            {
                "summary": json.dumps(result),
                "summary_generated_at": datetime.now(timezone.utc).isoformat(),
                "summary_source_hash": digest,
            },
        )
        _TLDR_RUNS.pop(issue_id, None)
    except LlmNotConfigured:
        _TLDR_RUNS[issue_id] = {
            "hash": digest,
            "state": "failed",
            "error": "No LLM provider configured — set one up in Settings.",
        }
    except Exception as e:  # noqa: BLE001 — the popup must show what went wrong
        logger.exception("work-item TLDR generation failed for %s", issue_id)
        message = getattr(e, "message", None) or str(e)
        _TLDR_RUNS[issue_id] = {
            "hash": digest,
            "state": "failed",
            "error": f"Couldn't summarize this item: {message}",
        }


@router.get("/work-items/{issue_id}/tldr")
async def work_item_tldr(
    issue_id: UUID,
    retry: bool = False,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-25.3: the stored summary of a work item, generating it if stale.

    Never blocks on the model. Returns `ready` with the stored summary when the
    sources are unchanged, otherwise kicks off a generation and returns
    `generating` — the popup polls until it flips.
    """
    rows = await postgrest_get(
        settings,
        user.token,
        "issues",
        {
            "select": (
                "id, org_id, type, title, body, acceptance_criteria, "
                "instruction_set, summary, summary_generated_at, "
                "summary_source_hash"
            ),
            "id": f"eq.{issue_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="work item not found")
    issue = rows[0]

    type_label, sources, missing = await _collect_sources(
        settings, user.token, issue
    )
    if not sources:
        # Nothing to summarize is a fact, not a failure — and not a spinner.
        return {
            "status": "empty",
            "detail": "This work item has no content to summarize yet.",
        }
    digest = _source_hash(sources)

    key = str(issue_id)
    if retry:
        _TLDR_RUNS.pop(key, None)
    elif issue.get("summary") and issue.get("summary_source_hash") == digest:
        try:
            stored = json.loads(issue["summary"])
        except (TypeError, ValueError):
            stored = None
        if stored:
            return {
                "status": "ready",
                "summary": stored,
                "generated_at": issue.get("summary_generated_at"),
            }

    run = _TLDR_RUNS.get(key)
    if run and run["hash"] == digest:
        if run["state"] == "failed":
            return {"status": "failed", "error": run["error"]}
        return {"status": "generating"}

    _TLDR_RUNS[key] = {"hash": digest, "state": "running", "error": None}
    task = asyncio.create_task(
        _generate_tldr(
            settings,
            key,
            issue["org_id"],
            type_label,
            sources,
            missing,
            digest,
        )
    )
    _TLDR_TASKS.add(task)
    task.add_done_callback(_TLDR_TASKS.discard)
    return {"status": "generating"}


class ElaborateRequest(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    context: str | None = Field(default=None, max_length=8000)
    project_id: str | None = Field(default=None)


@router.post("/elaborate-test")
async def elaborate_test(
    body: ElaborateRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    try:
        return await elaborate_test_case(
            settings, user.token, body.description, body.context,
            project_id=body.project_id,
        )
    except LlmNotConfigured:
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured — set one up in Settings.",
        )
    except LlmCallError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e.message}")


class LearningsUpdateRequest(BaseModel):
    context: str = Field(min_length=1, max_length=8000)


@router.post("/learnings/{project_id}/update")
async def update_learnings(
    project_id: UUID,
    body: LearningsUpdateRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    project_rows = await postgrest_get(
        settings,
        user.token,
        "projects",
        {"select": "org_id", "id": f"eq.{project_id}", "limit": "1"},
    )
    if not project_rows:
        raise HTTPException(status_code=404, detail="project not found")
    org_id = project_rows[0]["org_id"]

    existing_rows = await postgrest_get(
        settings,
        user.token,
        "project_learnings",
        {"select": "content", "project_id": f"eq.{project_id}", "limit": "1"},
    )
    existing_content = existing_rows[0]["content"] if existing_rows else ""

    try:
        merged = await merge_learnings(
            settings, user.token, existing_content, body.context
        )
    except LlmNotConfigured:
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured — set one up in Settings.",
        )
    except LlmCallError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e.message}")

    rows = await postgrest_upsert(
        settings,
        user.token,
        "project_learnings",
        {
            "org_id": org_id,
            "project_id": str(project_id),
            "content": merged,
            "last_updated_by": "llm",
        },
        on_conflict="project_id",
    )
    return rows[0]


class LearningDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=2000)


@router.post("/learnings/{project_id}/submissions/{submission_id}/decide")
async def decide_learning_submission(
    project_id: UUID,
    submission_id: UUID,
    body: LearningDecisionRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-5.31: the manager's gate on agent-submitted learnings. Approve
    runs the curated LLM merge into the learnings document and stamps the
    submission; reject stamps it with an optional note and leaves the
    document untouched. A merge failure leaves the submission pending —
    never silently lost."""
    from .. import db

    # The read under the caller's JWT is the org-membership check: RLS
    # hides other orgs' submissions.
    rows = await postgrest_get(
        settings,
        user.token,
        "learning_submissions",
        {
            "select": "id,org_id,text,status",
            "id": f"eq.{submission_id}",
            "project_id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="submission not found")
    sub = rows[0]
    if sub["status"] != "pending":
        raise HTTPException(
            status_code=409, detail=f"already {sub['status']}"
        )

    if body.decision == "reject":
        if not db.decide_learning_submission(
            settings, str(submission_id), "rejected", user.id, body.note
        ):
            raise HTTPException(status_code=409, detail="already decided")
        return {"status": "rejected"}

    existing_rows = await postgrest_get(
        settings,
        user.token,
        "project_learnings",
        {"select": "content", "project_id": f"eq.{project_id}", "limit": "1"},
    )
    existing_content = existing_rows[0]["content"] if existing_rows else ""
    try:
        merged = await merge_learnings(
            settings, user.token, existing_content, sub["text"]
        )
    except LlmNotConfigured:
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured — set one up in Settings. "
            "The submission stays pending.",
        )
    except LlmCallError as e:
        raise HTTPException(
            status_code=502,
            detail=f"LLM call failed: {e.message}. The submission stays "
            "pending.",
        )
    # The upsert rides the caller's JWT, so the content_audit row (us-5.33)
    # names the approving manager.
    await postgrest_upsert(
        settings,
        user.token,
        "project_learnings",
        {
            "org_id": sub["org_id"],
            "project_id": str(project_id),
            "content": merged,
            "last_updated_by": "llm",
        },
        on_conflict="project_id",
    )
    if not db.decide_learning_submission(
        settings, str(submission_id), "approved", user.id, body.note
    ):
        # Lost a race after the merge landed — surface it rather than
        # pretending this call decided it.
        raise HTTPException(status_code=409, detail="already decided")
    return {"status": "approved"}


class GenerateDeployScriptRequest(BaseModel):
    """In-form deployment fields — must work for unsaved drafts (US-1.51)."""

    project_id: UUID
    name: str = Field(default="", max_length=200)
    branch: str = Field(default="", max_length=200)
    target_folder: str = Field(default="", max_length=500)
    source_folder: str = Field(default="", max_length=500)
    strategy: str = Field(default="releases", max_length=32)
    keep_releases: int = Field(default=5, ge=1, le=50)
    run_timeout_minutes: int = Field(default=30, ge=1, le=240)
    health_check_url: str | None = Field(default=None, max_length=2000)
    # Names only — values must never be sent (and are ignored if present).
    env_var_names: list[str] = Field(default_factory=list, max_length=64)


@router.post("/generate-deploy-script")
async def generate_deploy_script_endpoint(
    body: GenerateDeployScriptRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    projects = await postgrest_get(
        settings,
        user.token,
        "projects",
        {
            "select": "id,name,description,repo_full_name,default_branch",
            "id": f"eq.{body.project_id}",
            "limit": "1",
        },
    )
    if not projects:
        raise HTTPException(status_code=404, detail="project not found")
    project = projects[0]

    try:
        guidelines = await rpc(
            settings,
            user.token,
            "assemble_project_guidelines",
            {"p_project": str(body.project_id)},
        )
    except RpcError as e:
        raise HTTPException(status_code=400, detail=e.message) from e
    if not isinstance(guidelines, str):
        guidelines = ""

    # US-43.4: say whether this draft had anything real to work from. The
    # generator has always been handed "project + guideline context" and the
    # guidelines have never carried a single deployment fact, so it drafted
    # from the stack and convention — a plausible script, not this project's.
    # The Deployment and Release section is what changes that, and the manager
    # reading the output deserves to know which of the two they are looking at.
    deployment_sections = await postgrest_get(
        settings,
        user.token,
        "project_guidelines",
        {
            "select": "id",
            "project_id": f"eq.{body.project_id}",
            "section_key": "eq.deployment",
            "limit": "1",
        },
    )
    grounded = bool(deployment_sections)

    try:
        result = await generate_deploy_script(
            settings,
            user.token,
            project_name=project.get("name") or "",
            project_description=project.get("description"),
            repo_full_name=project.get("repo_full_name") or "",
            default_branch=project.get("default_branch") or "main",
            guidelines=guidelines,
            deployment_name=body.name,
            branch=body.branch,
            target_folder=body.target_folder,
            source_folder=body.source_folder,
            strategy=body.strategy,
            keep_releases=body.keep_releases,
            run_timeout_minutes=body.run_timeout_minutes,
            health_check_url=body.health_check_url,
            env_var_names=body.env_var_names,
            project_id=str(body.project_id),
        )
        return {**result, "grounded_in_deployment_section": grounded}
    except LlmNotConfigured:
        raise HTTPException(
            status_code=409,
            detail="No LLM provider configured — set one up in Settings.",
        )
    except LlmCallError as e:
        raise HTTPException(status_code=502, detail=f"LLM call failed: {e.message}")


# ---------------------------------------------------------------------------
# US-33.1: per-model prices — the rate spend is derived from
# ---------------------------------------------------------------------------
# Tokens are the measured fact; money is tokens times a rate the org sets. The
# rate in force is copied onto each usage row, so repricing a model changes what
# future calls cost and never rewrites what past ones did.


class ModelPriceBody(BaseModel):
    model: str
    input_per_mtok: float
    output_per_mtok: float
    # US-38.1: optional, and None means "charge these at the input rate" --
    # which is what they have always been charged at, so leaving them unset
    # changes no figure. A default of 0 here would silently make every cached
    # token free, which is the one thing us-33.1 forbids.
    cache_read_per_mtok: float | None = None
    cache_write_per_mtok: float | None = None


async def _require_manage_org_llm(
    org_id: str, user: AuthUser, settings: Settings
) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_project"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403, detail="Not authorized to change model prices"
        )


@router.put("/orgs/{org_id}/model-prices")
async def put_model_price(
    org_id: str,
    body: ModelPriceBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_manage_org_llm(org_id, user, settings)
    model = (body.model or "").strip()
    if not model:
        raise HTTPException(status_code=422, detail="a price needs a model")
    for label, value in (
        ("input_per_mtok", body.input_per_mtok),
        ("output_per_mtok", body.output_per_mtok),
        ("cache_read_per_mtok", body.cache_read_per_mtok),
        ("cache_write_per_mtok", body.cache_write_per_mtok),
    ):
        if value is None:
            continue
        if value < 0 or value > 100_000:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be between 0 and 100000 dollars per million tokens",
            )
    row = db.set_model_price(
        settings,
        org_id,
        model[:200],
        body.input_per_mtok,
        body.output_per_mtok,
        body.cache_read_per_mtok,
        body.cache_write_per_mtok,
    )
    return {
        "model": row["model"],
        "input_per_mtok": float(row["input_per_mtok"]),
        "output_per_mtok": float(row["output_per_mtok"]),
        "cache_read_per_mtok": (
            float(row["cache_read_per_mtok"])
            if row["cache_read_per_mtok"] is not None
            else None
        ),
        "cache_write_per_mtok": (
            float(row["cache_write_per_mtok"])
            if row["cache_write_per_mtok"] is not None
            else None
        ),
    }


@router.delete("/orgs/{org_id}/model-prices/{model:path}")
async def delete_model_price(
    org_id: str,
    model: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Removing a price does not remove the cost already recorded — those rows
    carry the rate they were charged at."""
    await _require_manage_org_llm(org_id, user, settings)
    return {"deleted": db.delete_model_price(settings, org_id, model)}


# ---------------------------------------------------------------------------
# US-33.3: what it cost, by project, agent, provider and model
# ---------------------------------------------------------------------------


@router.get("/orgs/{org_id}/spend")
async def org_spend(
    org_id: str,
    group_by: str = "project",
    days: int = 30,
    project_id: str | None = None,
    worker_id: str | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """One grain, four dimensions, over a window.

    Read-gated on org membership rather than a capability: spend is something
    every member of the org can see, and RLS already scopes the underlying rows
    the same way. Computed from the append-only usage rows at read time — there
    is no counter to drift.
    """
    try:
        member = await rpc(settings, user.token, "is_org_member", {"org": org_id})
    except RpcError:
        member = False
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")
    return await asyncio.to_thread(
        db.spend_breakdown,
        settings,
        org_id,
        group_by=group_by,
        days=days,
        project_id=project_id,
        worker_id=worker_id,
    )
