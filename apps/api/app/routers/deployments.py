"""Deployment orchestration (US-1.32/1.37/1.38).

Only orchestration and secret handling live here — deployment CRUD and
run/event reads go straight through the Supabase SDK under RLS ("build
less API"). Every endpoint authorizes under the caller's own JWT (RLS
hides foreign deployments) before touching service-role resources.

Env var VALUES (US-1.37) are write-only: they flow browser -> api ->
data bucket and are never returned by any response here.
"""

import asyncio
import hashlib
import re

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from .. import db, deploy, github, storage
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, postgrest_delete, postgrest_get, rpc
from .github import _org_github_token

router = APIRouter(prefix="/deployments", tags=["deployments"])

ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

ZIP_MAX_BYTES = 200 * 1024 * 1024  # stated in the UI (US-1.33)


async def get_deployment_for_user(
    settings: Settings, token: str, deployment_id: str
) -> dict:
    """Fetch a deployment (+server +project) under the caller's JWT.

    RLS makes a foreign deployment a 404 — the single cross-org gate for
    every orchestration endpoint here.
    """
    rows = await postgrest_get(
        settings,
        token,
        "deployments",
        {
            # The FK is named because it has to be: US-16.1's `app_issues`
            # holds foreign keys to BOTH deployments and projects, so PostgREST
            # infers a second, many-to-many path between them and answers an
            # un-hinted `projects(...)` with 300 Multiple Choices. That turned
            # every deployment read here into a 500 the moment 182 was applied.
            "select": (
                "*,servers(*),projects!deployments_project_id_org_id_fkey"
                "(id,name,repo_full_name,uat_branch,production_branch)"
            ),
            "id": f"eq.{deployment_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Deployment not found")
    return rows[0]


async def assert_can_operate(settings: Settings, user: AuthUser, dep: dict) -> None:
    """US-1.41: protected deployments are owners-only, enforced server-side
    — running, cancelling, editing config, and deleting all pass here."""
    if not dep.get("protected"):
        return
    try:
        is_owner = await rpc(
            settings, user.token, "is_org_owner", {"org": dep["org_id"]}
        )
    except RpcError:
        is_owner = False
    if not is_owner:
        raise HTTPException(status_code=403, detail="Protected — owners only")


def assert_factory(dep: dict, action: str) -> None:
    """US-50.3: refuse the SSH-shaped actions on an external deployment.

    Hidden is a courtesy; refused is the guarantee. The precedent is
    `assert_can_operate` — the browser is not the only caller, and half of
    these would otherwise fail deep inside `deploy.py` with a paramiko error
    the manager cannot read.
    """
    if (dep.get("kind") or "factory") != "external":
        return
    raise HTTPException(
        status_code=400,
        detail=(
            f"{action} is not available on an external deployment — it has no "
            "machine. Deploying it merges "
            f"{dep.get('branch')} into {dep.get('target_branch')} on GitHub, "
            "and the team's own pipeline takes it from there."
        ),
    )


class RunBody(BaseModel):
    ref: str | None = None  # US-1.50: one-off branch/commit override


@router.post("/{deployment_id}/run", status_code=202)
async def run_deployment(
    deployment_id: str,
    body: RunBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    server = dep.get("servers")
    project = dep.get("projects")
    external = (dep.get("kind") or "factory") == "external"
    if not project:
        raise HTTPException(status_code=400, detail="Deployment is missing its project")
    # US-50.1: the server is what makes a deployment factory-run, so the
    # missing-server 400 is a factory-only check.
    if not external and not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    if external and not (dep.get("target_branch") or "").strip():
        raise HTTPException(
            status_code=400, detail="Deployment is missing its target branch"
        )

    # US-7.3: a classified UAT/Production deployment releases from the
    # project's release branch (the single source of truth). Fallback: if the
    # project's release branch is unset, keep the deployment's own branch.
    env = dep.get("environment")
    release_branch = None
    if env == "uat":
        release_branch = (project.get("uat_branch") or "").strip() or None
    elif env == "production":
        release_branch = (project.get("production_branch") or "").strip() or None
    if release_branch:
        dep = {**dep, "branch": release_branch}

    override = None
    ref = (body.ref or "").strip() if body else ""
    if ref:
        # US-1.50: not available on protected deployments — production
        # ships its configured branch or a promotion.
        if dep.get("protected"):
            raise HTTPException(
                status_code=403,
                detail="Ref overrides are not available on protected deployments.",
            )
        owner, repo = project["repo_full_name"].split("/", 1)
        token = await _org_github_token(
            settings, user, project["repo_full_name"], org_id=dep["org_id"]
        )
        try:
            commit = await github.get_commit(token, owner, repo, ref)
        except github.GitHubError as e:
            raise HTTPException(status_code=400, detail=f"Cannot deploy '{ref}': {e.message}")
        message = ((commit.get("commit") or {}).get("message") or "").strip()
        override = {
            "ref": ref,
            "sha": commit["sha"],
            "message": message.splitlines()[0][:200] if message else "",
        }

    try:
        run_id = await asyncio.to_thread(
            deploy.create_run,
            settings,
            dep,
            user.id,
            user.email or "",
            "branch",
            None,
            ref or None,
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this deployment — wait for it to finish.",
        )

    deploy.launch(
        settings,
        {
            "run_id": run_id,
            "org_id": dep["org_id"],
            "deployment": dep,
            "server": server,
            "repo_full_name": project["repo_full_name"],
            "project_name": project.get("name") or "",
            "triggered_by": user.email or "",
            "override": override,
        },
    )
    return {"run_id": run_id, "status": "queued"}


class AgentDispatchBody(BaseModel):
    ref: str | None = None  # captured like a manual run (US-1.50 override)
    auto_rollback: bool = False  # US-13.13: pre-authorized or it never happens


@router.post("/{deployment_id}/agent-dispatch", status_code=202)
async def agent_dispatch(
    deployment_id: str,
    body: AgentDispatchBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-13.13: hand the execution and babysitting of this deployment to
    an agent — a `deploy` pool run offered only to workers granted the
    `deploy` capability. The human decision moves to dispatch time;
    nothing ever auto-deploys. Refuses protected deployments and
    production without the per-deployment 'agent may deploy' flag (the
    trigger tool re-checks both independently)."""
    # RLS gate first: a foreign deployment is a 404.
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    result = db.dispatch_deploy_run(
        settings,
        deployment_id,
        dep["org_id"],
        ref=(body.ref if body else None),
        auto_rollback=bool(body.auto_rollback) if body else False,
        actor=user.id,
    )
    if "error" in result:
        raise HTTPException(status_code=409, detail=result["error"])
    return {"ok": True, **result}


class DuplicateBody(BaseModel):
    name: str


@router.post("/{deployment_id}/duplicate", status_code=201)
async def duplicate_deployment(
    deployment_id: str,
    body: DuplicateBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.42: create a sibling with the same config. Env var VALUES are
    copied server-side (bucket object copy) — never via the browser. The
    protected flag does not copy; the duplicate starts fresh."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)  # duplicating protected = owner-only
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name is required")

    from ..supabase import postgrest_post

    fields = {
        "org_id": dep["org_id"],
        "project_id": dep["project_id"],
        # US-50.1: Duplicate makes a sibling of the SAME kind — the kind is
        # what a deployment is, and a mixed history would mean nothing.
        "kind": dep.get("kind") or "factory",
        "server_id": dep["server_id"],
        "name": name,
        "branch": dep["branch"],
        "target_branch": dep.get("target_branch") or "",
        "target_folder": dep["target_folder"],
        "script": dep["script"],
        "run_timeout_minutes": dep.get("run_timeout_minutes", 30),
        "strategy": dep.get("strategy", "in-place"),
        "keep_releases": dep.get("keep_releases", 5),
        "source_folder": dep.get("source_folder", ""),
        "exclude_patterns": dep.get("exclude_patterns", ""),
        "health_check_url": dep.get("health_check_url", ""),
        "health_check_expected_status": dep.get("health_check_expected_status", 200),
        "health_check_window_seconds": dep.get("health_check_window_seconds", 60),
        "health_check_initial_delay_seconds": dep.get(
            "health_check_initial_delay_seconds", 0
        ),
        # protected deliberately not copied (US-1.42)
    }
    import httpx as _httpx

    try:
        rows = await postgrest_post(settings, user.token, "deployments", fields)
    except _httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            raise HTTPException(
                status_code=409,
                detail="A deployment with this name already exists on this project.",
            )
        raise
    new_dep = rows[0]

    # Server-side env value copy: bucket object -> bucket object.
    names = await asyncio.to_thread(deploy.list_env_var_names, settings, deployment_id)
    src_prefix = storage.deployment_prefix(dep["org_id"], deployment_id)
    dst_prefix = storage.deployment_prefix(dep["org_id"], new_dep["id"])
    for var_name in names:
        value = await storage.get_object(settings, f"{src_prefix}/env/{var_name}")
        if value is None:
            continue
        await storage.put_object(settings, f"{dst_prefix}/env/{var_name}", value)
        await asyncio.to_thread(
            deploy.upsert_env_var,
            settings,
            dep["org_id"],
            new_dep["id"],
            var_name,
            user.email or "api",
        )
    return {"id": new_dep["id"], "name": new_dep["name"], "copied_env_vars": len(names)}


@router.post("/{deployment_id}/runs/{run_id}/cancel")
async def cancel_run(
    deployment_id: str,
    run_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.35: stop an in-progress run. Cancellation does not undo work
    already done on the server — the UI says so before confirming."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    run = await asyncio.to_thread(deploy.get_run, settings, run_id)
    if not run or str(run["deployment_id"]) != deployment_id:
        raise HTTPException(status_code=404, detail="Run not found")
    outcome = await asyncio.to_thread(
        deploy.request_cancel, settings, run_id, user.id, user.email or ""
    )
    if outcome == "not-active":
        raise HTTPException(status_code=409, detail="This run is no longer active.")
    return {"ok": True, "outcome": outcome}


@router.get("/{deployment_id}/drift")
async def deployment_drift(
    deployment_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.34: how far the branch has moved past the deployed payload,
    plus the commits that would ship on the next run. Read-only — members
    keep it even on protected deployments (US-1.41).

    US-50.3: on an external deployment the question changes meaning rather
    than disappearing — "what would the next run ship" is how far the source
    branch is ahead of the TARGET branch, which needs no run history at all,
    so the card answers before anything has ever run."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    project = dep.get("projects") or {}
    repo_full_name = project.get("repo_full_name") or ""

    if (dep.get("kind") or "factory") == "external":
        base = (dep.get("target_branch") or "").strip()
        if not base:
            return {"state": "never"}
    else:
        current_id = dep.get("current_run_id")
        if not current_id:
            return {"state": "never"}
        run = await asyncio.to_thread(deploy.get_run, settings, str(current_id))
        if not run:
            return {"state": "never"}
        if run["source"] == "zip":
            return {"state": "zip"}
        base = str(run["commit_sha"])

    owner, repo = repo_full_name.split("/", 1)
    token = await _org_github_token(
        settings, user, repo_full_name, org_id=dep["org_id"]
    )
    try:
        cmp = await github.compare_commits(
            token, owner, repo, base, dep["branch"]
        )
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)

    status = cmp.get("status")
    if status == "identical" or (status == "behind" and cmp.get("behind_by") == 0):
        return {"state": "up-to-date"}
    if status == "ahead":
        commits = [
            {
                "sha": c["sha"],
                "message": (c["commit"]["message"] or "").splitlines()[0][:200],
                "author": (c["commit"].get("author") or {}).get("name") or "",
                "date": (c["commit"].get("author") or {}).get("date") or "",
            }
            for c in reversed(cmp.get("commits", []))
        ]
        # US-1.48: deploy views speak in factory issues where they can.
        try:
            issue_map = await asyncio.to_thread(
                deploy.map_commits_to_issues,
                settings,
                dep["project_id"],
                [c["sha"] for c in commits],
            )
            for c in commits:
                if c["sha"] in issue_map:
                    c["issue"] = issue_map[c["sha"]]
        except Exception:
            pass  # mapping is best-effort display sugar
        return {"state": "behind", "behind_by": cmp.get("ahead_by", 0), "commits": commits}
    # behind/diverged: the deployed commit is no longer an ancestor of head.
    return {"state": "diverged"}


def _launch_zip_run(
    settings: Settings, dep: dict, server: dict, user: AuthUser, zip_filename: str
) -> str:
    """Shared tail of the two zip flows: create the run + fire the pipeline."""
    try:
        run_id = deploy.create_run(
            settings, dep, user.id, user.email or "", source="zip",
            zip_filename=zip_filename,
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this deployment — wait for it to finish.",
        )
    project = dep.get("projects") or {}
    deploy.launch(
        settings,
        {
            "run_id": run_id,
            "org_id": dep["org_id"],
            "deployment": dep,
            "server": server,
            "repo_full_name": project.get("repo_full_name") or "",
            "project_name": project.get("name") or "",
            "triggered_by": user.email or "",
            "source": "zip",
            "zip_filename": zip_filename,
        },
    )
    return run_id


@router.post("/{deployment_id}/zip", status_code=202)
async def deploy_from_zip(
    deployment_id: str,
    file: UploadFile,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.33: upload a zip, stage it (replacing any previous one), and
    start a run with it as the payload."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    assert_factory(dep, "Deploying a zip")
    server = dep.get("servers")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    filename = file.filename or "artifact.zip"
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip files are accepted")
    if await asyncio.to_thread(deploy.has_active_run, settings, deployment_id):
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this deployment — wait for it to finish.",
        )

    hasher = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > ZIP_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Zip exceeds the {ZIP_MAX_BYTES // (1024 * 1024)} MB limit",
            )
        hasher.update(chunk)
        chunks.append(chunk)
    if total == 0:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    data = b"".join(chunks)
    if not data.startswith(b"PK"):
        raise HTTPException(status_code=400, detail="That file doesn't look like a zip")

    prefix = storage.deployment_prefix(dep["org_id"], deployment_id)
    await storage.put_object(
        settings, f"{prefix}/staged.zip", data, content_type="application/zip"
    )
    await asyncio.to_thread(
        deploy.update_staged_zip,
        settings,
        deployment_id,
        filename,
        total,
        hasher.hexdigest(),
        user.email or "",
    )
    run_id = _launch_zip_run(settings, dep, server, user, filename)
    return {"run_id": run_id, "status": "queued"}


@router.post("/{deployment_id}/redeploy-zip", status_code=202)
async def redeploy_last_zip(
    deployment_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.33: rerun the staged zip without re-uploading."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    assert_factory(dep, "Redeploying the staged zip")
    server = dep.get("servers")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    if not dep.get("staged_zip_filename"):
        raise HTTPException(
            status_code=409, detail="No zip has been staged for this deployment yet."
        )
    run_id = _launch_zip_run(
        settings, dep, server, user, dep["staged_zip_filename"]
    )
    return {"run_id": run_id, "status": "queued"}


def _artifact_label(run: dict) -> str:
    if run.get("source") == "zip":
        return f"zip {run.get('zip_filename') or ''}".strip()
    sha = run.get("commit_sha") or ""
    return f"{run.get('branch')} @ {sha[:7]}" if sha else str(run.get("branch"))


def _archive_ctx_for(run: dict) -> dict:
    path = run["artifact_path"]
    return {
        "path": path,
        "ext": "zip" if path.endswith(".zip") else "tgz",
        "label": _artifact_label(run),
        # US-2.14: the archived size is known before fetch — feeds the
        # payload-aware disk preflight.
        "bytes": run.get("artifact_bytes"),
    }


@router.post("/{deployment_id}/runs/{run_id}/redeploy", status_code=202)
async def redeploy_run(
    deployment_id: str,
    run_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.47: rerun an archived payload as-is through the deployment's
    full CURRENT pipeline — works even if the branch was rewritten, the
    staged zip replaced, or the release folder pruned."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    assert_factory(dep, "Redeploying an archived payload")
    server = dep.get("servers")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    src = await asyncio.to_thread(deploy.get_run, settings, run_id)
    if not src or str(src["deployment_id"]) != deployment_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if not src.get("artifact_path"):
        raise HTTPException(
            status_code=409, detail="This run has no archived payload to redeploy."
        )

    try:
        new_run_id = await asyncio.to_thread(
            deploy.create_derived_run,
            settings,
            dep,
            src,
            "redeploy",
            user.id,
            user.email or "",
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this deployment — wait for it to finish.",
        )
    project = dep.get("projects") or {}
    deploy.launch(
        settings,
        {
            "run_id": new_run_id,
            "org_id": dep["org_id"],
            "deployment": dep,
            "server": server,
            "repo_full_name": project.get("repo_full_name") or "",
            "project_name": project.get("name") or "",
            "triggered_by": user.email or "",
            "source": src["source"],
            "zip_filename": src.get("zip_filename"),
            "archive": _archive_ctx_for(src),
        },
    )
    return {"run_id": new_run_id, "status": "queued"}


class PromoteBody(BaseModel):
    target_deployment_id: str


@router.post("/{deployment_id}/runs/{run_id}/promote", status_code=202)
async def promote_run(
    deployment_id: str,
    run_id: str,
    body: PromoteBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.43: ship the EXACT tested payload to a sibling deployment,
    through the target's full pipeline and rules."""
    source_dep = await get_deployment_for_user(settings, user.token, deployment_id)
    assert_factory(source_dep, "Promoting a run payload")
    src = await asyncio.to_thread(deploy.get_run, settings, run_id)
    if not src or str(src["deployment_id"]) != deployment_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if src["status"] != "succeeded":
        raise HTTPException(status_code=400, detail="Only successful runs can be promoted")

    target = await get_deployment_for_user(
        settings, user.token, body.target_deployment_id
    )
    if target["project_id"] != source_dep["project_id"]:
        raise HTTPException(
            status_code=400, detail="Promotion targets must belong to the same project"
        )
    # The TARGET's rules apply — including protection (US-1.41) and its kind.
    await assert_can_operate(settings, user, target)
    assert_factory(target, "Promoting a run payload into this deployment")
    server = target.get("servers")
    project = target.get("projects") or {}
    if not server:
        raise HTTPException(status_code=400, detail="Target is missing its server")

    override = None
    archive = None
    if src["source"] == "branch":
        # Pin by SHA (target's source filters apply); fall back to the
        # archived artifact only if GitHub can't serve the commit anymore.
        owner, repo = (project.get("repo_full_name") or "/").split("/", 1)
        token = await _org_github_token(
            settings,
            user,
            project.get("repo_full_name"),
            org_id=source_dep["org_id"],
        )
        try:
            await github.get_commit(token, owner, repo, str(src["commit_sha"]))
            override = {
                "ref": f"promoted {str(src['commit_sha'])[:7]}",
                "sha": src["commit_sha"],
                "message": src.get("commit_message") or "",
            }
        except github.GitHubError:
            if not src.get("artifact_path"):
                raise HTTPException(
                    status_code=409,
                    detail="GitHub can no longer serve this commit and no archived"
                    " payload exists — cannot promote.",
                )
            archive = _archive_ctx_for(src)
    else:
        if not src.get("artifact_path"):
            raise HTTPException(
                status_code=409,
                detail="This zip run has no archived payload — cannot promote.",
            )
        archive = _archive_ctx_for(src)

    try:
        new_run_id = await asyncio.to_thread(
            deploy.create_derived_run,
            settings,
            target,
            src,
            "promote",
            user.id,
            user.email or "",
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409,
            detail="A run is already active on the target — wait for it to finish.",
        )
    deploy.launch(
        settings,
        {
            "run_id": new_run_id,
            "org_id": target["org_id"],
            "deployment": target,
            "server": server,
            "repo_full_name": project.get("repo_full_name") or "",
            "project_name": project.get("name") or "",
            "triggered_by": user.email or "",
            "source": src["source"],
            "zip_filename": src.get("zip_filename"),
            "override": override,
            "archive": archive,
        },
    )
    return {"run_id": new_run_id, "status": "queued"}


@router.get("/{deployment_id}/runs/{run_id}/artifact")
async def download_artifact(
    deployment_id: str,
    run_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.47: stream an archived payload. The client never touches the
    bucket; this endpoint only ever serves paths under the deployment's
    own runs/ folder — it cannot reach credential objects."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    run = await asyncio.to_thread(deploy.get_run, settings, run_id)
    if not run or str(run["deployment_id"]) != deployment_id:
        raise HTTPException(status_code=404, detail="Run not found")
    path = run.get("artifact_path")
    expected_prefix = f"{storage.deployment_prefix(dep['org_id'], deployment_id)}/runs/"
    if not path or not path.startswith(expected_prefix):
        raise HTTPException(status_code=404, detail="No archived payload for this run")
    data = await storage.get_object(settings, path)
    if data is None:
        raise HTTPException(status_code=404, detail="Archived payload is missing")
    from fastapi.responses import Response

    filename = path.rsplit("/", 1)[-1]
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class RollbackBody(BaseModel):
    run_id: str


@router.post("/{deployment_id}/rollback", status_code=202)
async def rollback_deployment(
    deployment_id: str,
    body: RollbackBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.39: repoint `current` to a retained release — no re-transfer."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    # US-50.3: rollback is not supported for an external deployment, and the
    # app says so once. Recovery there means merging a fix — or reverting on
    # GitHub by hand and recording it with POST /releases/{id}/rolled-back.
    assert_factory(dep, "Rollback")
    server = dep.get("servers")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    if dep.get("strategy") != "releases":
        raise HTTPException(
            status_code=400,
            detail="Rollback needs the releases strategy — this deployment is in-place.",
        )

    to_run = await asyncio.to_thread(deploy.get_run, settings, body.run_id)
    if not to_run or str(to_run["deployment_id"]) != deployment_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if to_run["status"] != "succeeded":
        raise HTTPException(status_code=400, detail="Only successful runs can be rolled back to")
    if not to_run.get("release_path"):
        raise HTTPException(
            status_code=409,
            detail="That release was pruned from the server — redeploy from the"
            " archived payload (us-1.47) instead.",
        )

    try:
        run_id = await asyncio.to_thread(
            deploy.create_rollback_run, settings, dep, to_run, user.id, user.email or ""
        )
    except deploy.RunActive:
        raise HTTPException(
            status_code=409,
            detail="A run is already active for this deployment — wait for it to finish.",
        )

    deploy.launch_rollback(
        settings,
        {
            "run_id": run_id,
            "org_id": dep["org_id"],
            "deployment": dep,
            "server": server,
            "to_run": to_run,
            "project_name": (dep.get("projects") or {}).get("name") or "",
            "triggered_by": user.email or "",
        },
    )
    return {"run_id": run_id, "status": "queued"}


@router.post("/{deployment_id}/preflight")
async def run_preflight(
    deployment_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Standalone Check action (US-1.38): preflight only — no transfer,
    no script — so a new deployment config can be validated up front.
    Changes nothing on the server, so members keep it (US-1.41)."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    assert_factory(dep, "Preflight")
    server = dep.get("servers")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")

    try:
        conn = await deploy.connect_to_server(settings, server)
    except deploy.PipelineError as e:
        # Connect/auth is itself the first check — report it as a result.
        return {"ok": False, "results": [{"check": "ssh", "ok": False, "detail": e.message}]}

    # US-2.14: check the extraction tool for THIS deployment's payload
    # (unzip when a zip is staged, else tar), against a payload-aware
    # requirement when the staged size is known.
    has_staged_zip = bool(dep.get("staged_zip_filename"))
    tools = ("unzip",) if has_staged_zip else ("tar",)
    required_mb, space_reason = deploy.compute_required_free_mb(
        dep.get("staged_zip_bytes") if has_staged_zip else None,
        dep.get("strategy") or "in-place",
        dep.get("keep_releases") or 5,
    )
    try:
        results = await asyncio.to_thread(
            deploy.preflight_checks,
            conn.transport,
            dep["target_folder"],
            required_mb,
            tools,
            space_reason,
        )
    finally:
        conn.close()
    return {"ok": all(r["ok"] for r in results), "results": results}


@router.post("/{deployment_id}/health-check")
async def manual_health_check(
    deployment_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-1.40: run the configured health check on demand — no deploy."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    # US-50.3: absent, not disabled — an external deployment has no machine to
    # curl from, and probing the public URL from `api` would be a different
    # measurement wearing the same button.
    assert_factory(dep, "The health check")
    server = dep.get("servers")
    url = (dep.get("health_check_url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="No health check configured")
    if not server:
        raise HTTPException(status_code=400, detail="Deployment is missing its server")
    try:
        conn = await deploy.connect_to_server(settings, server)
    except deploy.PipelineError as e:
        return {"ok": False, "last": e.message}
    try:
        ok, last = await asyncio.to_thread(
            deploy.health_check_once,
            conn.transport,
            url,
            dep.get("health_check_expected_status") or 200,
        )
    except deploy.PipelineError as e:
        return {"ok": False, "last": e.message}
    finally:
        conn.close()
    return {"ok": ok, "last": last}


class EnvValueBody(BaseModel):
    value: str


@router.put("/{deployment_id}/env/{name}")
async def set_env_var(
    deployment_id: str,
    name: str,
    body: EnvValueBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Add or replace an env var (US-1.37). The value goes straight to the
    data bucket and is never echoed back."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    # US-50.3: env var values live in the data bucket to be written onto a
    # target machine. An external deployment has nowhere to write them, and
    # offering the field would promise the other system reads them.
    assert_factory(dep, "Environment variables")
    if not ENV_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="Names must be valid POSIX environment variable names"
            " (letters, digits, underscore; not starting with a digit).",
        )
    prefix = storage.deployment_prefix(dep["org_id"], deployment_id)
    await storage.put_object(
        settings, f"{prefix}/env/{name}", body.value.encode("utf-8")
    )
    await asyncio.to_thread(
        deploy.upsert_env_var,
        settings,
        dep["org_id"],
        deployment_id,
        name,
        user.email or "api",
    )
    return {"ok": True}


@router.delete("/{deployment_id}/env/{name}")
async def remove_env_var(
    deployment_id: str,
    name: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    assert_factory(dep, "Environment variables")
    if not ENV_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="Invalid variable name")
    prefix = storage.deployment_prefix(dep["org_id"], deployment_id)
    await storage.delete_object(settings, f"{prefix}/env/{name}")
    await asyncio.to_thread(
        deploy.delete_env_var,
        settings,
        deployment_id,
        name,
        dep["org_id"],
        user.email or "api",
    )
    return {"ok": True}


@router.delete("/{deployment_id}")
async def delete_deployment(
    deployment_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Delete a deployment and its bucket folder (env values now, staged
    zips/artifacts as later stories land). Row delete runs under the
    caller's JWT so RLS stays the authorizer."""
    dep = await get_deployment_for_user(settings, user.token, deployment_id)
    await assert_can_operate(settings, user, dep)
    await postgrest_delete(
        settings, user.token, "deployments", {"id": f"eq.{deployment_id}"}
    )
    prefix = storage.deployment_prefix(dep["org_id"], deployment_id)
    try:
        await storage.delete_prefix(settings, f"{prefix}/env")
        await storage.delete_prefix(settings, f"{prefix}/runs")  # US-1.47 artifacts
        await storage.delete_prefix(settings, prefix)
    except storage.StorageError:
        pass  # row is gone; orphaned objects are unreachable but harmless
    return {"ok": True}
