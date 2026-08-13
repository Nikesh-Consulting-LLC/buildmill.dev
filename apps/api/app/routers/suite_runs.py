"""Suite run endpoints (US-81.2/81.3/81.4).

Three verbs: run a suite by hand against a chosen deployment, re-run a
release's suite, and waive a release suite verdict for sign-off. Authorization
is the house rule — the rows are loaded under the caller's own JWT, so RLS is
the org gate and another org's suite is simply not found. The pipeline itself
then runs service-side (it outlives the request).
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import github, github_tokens
from .. import suites as suites_mod
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import postgrest_get, rpc

router = APIRouter(tags=["suites"])


async def _one(
    settings: Settings, token: str, table: str, params: dict[str, str], what: str
) -> dict[str, Any]:
    rows = await postgrest_get(settings, token, table, {**params, "limit": "1"})
    if not rows:
        raise HTTPException(status_code=404, detail=f"{what} not found")
    return rows[0]


async def _load_server(
    settings: Settings, token: str, server_id: str
) -> dict[str, Any]:
    return await _one(
        settings, token, "servers", {"select": "*", "id": f"eq.{server_id}"}, "server"
    )


class ManualRunBody(BaseModel):
    deployment_id: UUID


@router.post("/suites/{suite_id}/run")
async def run_suite(
    suite_id: UUID,
    body: ManualRunBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-81.2: a manual run against any deployment of the suite's project.
    The commit is the deployment's configured branch head — a manual run
    answers "does the suite pass against what this deployment runs", not a
    release question."""
    suite = await _one(
        settings, user.token, "test_suites",
        {"select": "*", "id": f"eq.{suite_id}"}, "suite",
    )
    deployment = await _one(
        settings, user.token, "deployments",
        {"select": "*", "id": f"eq.{body.deployment_id}"}, "deployment",
    )
    if deployment["project_id"] != suite["project_id"]:
        raise HTTPException(
            status_code=400, detail="deployment belongs to a different project"
        )
    base_url = (deployment.get("website_url") or "").strip()
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail="this deployment has no website URL — the suite needs a base URL to test",
        )
    project = await _one(
        settings, user.token, "projects",
        {"select": "*", "id": f"eq.{suite['project_id']}"}, "project",
    )
    repo_full_name = project.get("repo_full_name") or ""
    if "/" not in repo_full_name:
        raise HTTPException(status_code=400, detail="project has no GitHub repository")
    owner, repo = repo_full_name.split("/", 1)
    try:
        token = await github_tokens.token_for_org(
            settings, suite["org_id"], repo_full_name
        )
        head = await github.get_branch(token, owner, repo, deployment["branch"])
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=str(e))
    sha = head["commit"]["sha"]

    server = await _load_server(
        settings, user.token, suite.get("server_id") or deployment["server_id"]
    )
    try:
        run_id = await asyncio.to_thread(
            suites_mod.create_suite_run,
            settings,
            org_id=suite["org_id"],
            project_id=suite["project_id"],
            suite_id=str(suite_id),
            deployment_id=str(body.deployment_id),
            trigger="manual",
            commit_sha=sha,
            base_url=base_url,
        )
    except suites_mod.SuiteRunActive:
        raise HTTPException(
            status_code=409, detail="this suite already has a run in flight"
        )
    suites_mod.launch(
        settings,
        {
            "run_id": run_id,
            "org_id": suite["org_id"],
            "suite": suite,
            "deployment": deployment,
            "server": server,
            "repo_full_name": repo_full_name,
            "project": project,
            "release": None,
            "commit_sha": sha,
            "base_url": base_url,
        },
    )
    return {"run_id": run_id, "commit_sha": sha}


class RerunBody(BaseModel):
    environment: str = Field(default="uat", pattern="^(uat|production)$")


@router.post("/releases/{release_id}/suites/{suite_id}/rerun")
async def rerun_release_suite(
    release_id: UUID,
    suite_id: UUID,
    body: RerunBody | None = None,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-81.3: run this suite again for this release — same pinned commit,
    same deployment. Available while the release sits where that environment's
    testing happens."""
    environment = (body or RerunBody()).environment
    release = await _one(
        settings, user.token, "releases",
        {"select": "*", "id": f"eq.{release_id}"}, "release",
    )
    suite = await _one(
        settings, user.token, "test_suites",
        {"select": "*", "id": f"eq.{suite_id}"}, "suite",
    )
    if suite["project_id"] != release["project_id"]:
        raise HTTPException(
            status_code=400, detail="suite belongs to a different project"
        )
    if environment == "uat" and release["status"] != "uat-deployed":
        raise HTTPException(
            status_code=409,
            detail=f"release is {release['status']} — UAT suites run on a release on UAT",
        )
    if environment == "production" and release["status"] != "released":
        raise HTTPException(
            status_code=409,
            detail=f"release is {release['status']} — production smoke runs on a released release",
        )
    project = await _one(
        settings, user.token, "projects",
        {"select": "*", "id": f"eq.{release['project_id']}"}, "project",
    )
    dep_col = (
        "release_uat_deployment_id"
        if environment == "uat"
        else "release_prod_deployment_id"
    )
    dep_id = project.get(dep_col)
    if not dep_id:
        raise HTTPException(
            status_code=400,
            detail=f"project has no designated {environment} release deployment",
        )
    deployment = await _one(
        settings, user.token, "deployments",
        {"select": "*", "id": f"eq.{dep_id}"}, "deployment",
    )
    base_url = (deployment.get("website_url") or "").strip()
    if not base_url:
        raise HTTPException(
            status_code=400, detail="the release deployment has no website URL"
        )
    server = await _load_server(
        settings, user.token, suite.get("server_id") or deployment["server_id"]
    )
    try:
        run_id = await asyncio.to_thread(
            suites_mod.create_suite_run,
            settings,
            org_id=suite["org_id"],
            project_id=suite["project_id"],
            suite_id=str(suite_id),
            deployment_id=str(dep_id),
            trigger="uat-deploy" if environment == "uat" else "prod-promote",
            commit_sha=release["commit_sha"],
            base_url=base_url,
            release_id=str(release_id),
        )
    except suites_mod.SuiteRunActive:
        raise HTTPException(
            status_code=409, detail="this suite already has a run in flight"
        )
    suites_mod.launch(
        settings,
        {
            "run_id": run_id,
            "org_id": suite["org_id"],
            "suite": suite,
            "deployment": deployment,
            "server": server,
            "repo_full_name": project.get("repo_full_name") or "",
            "project": project,
            "release": release,
            "commit_sha": release["commit_sha"],
            "base_url": base_url,
        },
    )
    return {"run_id": run_id}


class WaiveBody(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/releases/{release_id}/suites/{suite_id}/waive")
async def waive_release_suite(
    release_id: UUID,
    suite_id: UUID,
    body: WaiveBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-81.4: waive the latest run's verdict for sign-off, with a reason on
    the record. The waiver stamps the RUN — a re-run produces a fresh,
    unwaived verdict."""
    await _one(
        settings, user.token, "releases",
        {"select": "id", "id": f"eq.{release_id}"}, "release",
    )
    runs = await postgrest_get(
        settings,
        user.token,
        "suite_runs",
        {
            "select": "id,status",
            "release_id": f"eq.{release_id}",
            "suite_id": f"eq.{suite_id}",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if not runs:
        raise HTTPException(
            status_code=404, detail="this suite has no run for this release"
        )
    run = runs[0]
    if run["status"] in ("queued", "running"):
        raise HTTPException(
            status_code=409, detail="the run is still in flight — nothing to waive yet"
        )
    if run["status"] == "succeeded":
        raise HTTPException(status_code=409, detail="a succeeded run needs no waiver")
    await asyncio.to_thread(
        suites_mod.waive_run,
        settings,
        run_id=run["id"],
        waived_by=user.id,
        reason=body.reason.strip(),
    )
    blocker = await rpc(
        settings, user.token, "release_signoff_blocker", {"p_release": str(release_id)}
    )
    return {"waived_run_id": run["id"], "signoff_blocker": blocker}
