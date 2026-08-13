"""Cutting a release: what is in it, and what it is called (US-21.1).

A release is pinned to one commit at creation. Everything downstream — the
notes, the UAT deploy, the promotion to production — reads that SHA, never the
branch head at the time it runs. Between cutting a release and an agent
claiming it, the default branch moves; without the pin the notes would describe
one build while the deploy shipped another.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import github, github_tokens
from .config import Settings
from .github import GitHubError
from .supabase import postgrest_get, rpc

# The readable id prefix per work-item type (us-7.10).
_PREFIX = {"feature": "FEAT", "story": "US", "bug": "BUG", "chore": "CHORE"}

# How far back a first release looks. GitHub's compare API needs two endpoints,
# and a project's first release has no previous commit to compare against, so
# the range is the branch's own recent history — capped, and reported as such.
FIRST_RELEASE_COMMIT_CAP = 250


def release_branch_name(version: str) -> str:
    """US-50.4: the branch a cut creates at the pinned commit.

    An external environment is deployed *from a branch* — the other system's
    trigger is "something landed on prod" — so a release needs one, and it is
    the pin: `release/<version>` points at `commit_sha` and is never moved.
    Promotion merges that branch, so production ships the build UAT tested
    even when the default branch moved during testing.
    """
    return f"release/{version}"


def display_id(row: dict[str, Any]) -> str | None:
    epic = row.get("epic_number")
    item = row.get("item_no")
    if epic is None or item is None:
        return None
    sub = row.get("sub_no")
    tail = f"{epic}.{item}.{sub}" if sub is not None else f"{epic}.{item}"
    return f"{_PREFIX.get(row.get('type') or '', 'US')}-{tail}"


async def load_project(settings: Settings, token: str, project_id: str) -> dict:
    rows = await postgrest_get(
        settings,
        token,
        "projects",
        {
            "select": (
                "id,org_id,name,repo_full_name,default_branch,"
                "release_uat_deployment_id,release_prod_deployment_id"
            ),
            "id": f"eq.{project_id}",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(status_code=404, detail="project not found")
    return rows[0]


async def previous_release(settings: Settings, token: str, project_id: str) -> dict | None:
    """The last release that is currently live in production.

    Deliberately not "the last release cut": a `rejected` release never
    shipped, and a `rolled-back` one was taken back out, so their commits are
    still unreleased and the next release must include them again.
    """
    rows = await postgrest_get(
        settings,
        token,
        "releases",
        {
            "select": "id,version,commit_sha,released_at",
            "project_id": f"eq.{project_id}",
            "status": "eq.released",
            "order": "released_at.desc",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def items_in_range(
    settings: Settings, token: str, project_id: str, shas: list[str]
) -> list[dict[str, Any]]:
    """Resolve the work items whose merge commit is in this range.

    The mapping lives on `runs.merge_commit_sha` — which is why us-21.7 can
    drop `release_records` without losing it.
    """
    if not shas:
        return []
    quoted = ",".join(f'"{s}"' for s in shas)
    runs = await postgrest_get(
        settings,
        token,
        "runs",
        {
            "select": "merge_commit_sha,issue_id",
            "project_id": f"eq.{project_id}",
            "merge_commit_sha": f"in.({quoted})",
        },
    )
    issue_ids = sorted({r["issue_id"] for r in runs or [] if r.get("issue_id")})
    if not issue_ids:
        return []
    issues = await postgrest_get(
        settings,
        token,
        "issues",
        {
            "select": "id,title,type,item_no,sub_no,epics(number)",
            "id": f"in.({','.join(issue_ids)})",
        },
    )
    items = []
    for row in issues or []:
        epic = row.get("epics")
        if isinstance(epic, list):
            epic = epic[0] if epic else None
        items.append(
            {
                "issue_id": row["id"],
                "title": row["title"],
                "type": row["type"],
                "display_id": display_id(
                    {
                        "epic_number": (epic or {}).get("number"),
                        "item_no": row.get("item_no"),
                        "sub_no": row.get("sub_no"),
                        "type": row.get("type"),
                    }
                ),
            }
        )
    items.sort(key=lambda i: (i["display_id"] or "~", i["title"]))
    return items


async def build_preview(
    settings: Settings, token: str, project_id: str
) -> dict[str, Any]:
    """What cutting a release right now would produce — creating nothing.

    Also returns `blockers`: the reasons a cut would be refused, so the dialog
    can say what to fix instead of failing on submit.
    """
    project = await load_project(settings, token, project_id)
    blockers: list[str] = []

    if not project.get("release_uat_deployment_id"):
        blockers.append(
            "This project has no UAT deployment designated for releases — "
            "set one on the Deployments tab."
        )

    in_flight = await postgrest_get(
        settings,
        token,
        "releases",
        {
            "select": "id,version,status",
            "project_id": f"eq.{project_id}",
            "status": "in.(queued,running,uat-deployed,uat-signed-off,promoting)",
            "limit": "1",
        },
    )
    if in_flight:
        blockers.append(
            f"Release {in_flight[0]['version']} is still in flight "
            f"({in_flight[0]['status']}) — finish or reject it first."
        )

    repo_full = project.get("repo_full_name") or ""
    if "/" not in repo_full:
        blockers.append("This project has no connected repository.")
        return {
            "version": None,
            "commit_sha": None,
            "branch": project.get("default_branch"),
            "previous": None,
            "items": [],
            "first_release": True,
            "truncated": False,
            "blockers": blockers,
        }

    owner, repo = repo_full.split("/", 1)
    branch_name = (project.get("default_branch") or "main").strip()
    gh_token = await github_tokens.token_for_user(
        settings, token, project["org_id"], repo_full
    )
    try:
        branch = await github.get_branch(gh_token, owner, repo, branch_name)
    except GitHubError as e:
        raise HTTPException(status_code=422, detail=e.message)
    head_sha = branch["commit"]["sha"]

    prev = await previous_release(settings, token, project_id)
    truncated = False
    if prev:
        try:
            compare = await github.compare_commits(
                gh_token, owner, repo, prev["commit_sha"], head_sha
            )
        except GitHubError as e:
            raise HTTPException(status_code=422, detail=e.message)
        shas = [c["sha"] for c in compare.get("commits") or []]
        if not shas:
            blockers.append(
                f"Nothing has merged to {branch_name} since "
                f"{prev['version']} — there is nothing to release."
            )
    else:
        # No previous release: everything currently on the branch is in range.
        try:
            commits = await github.list_branch_commits(
                gh_token, owner, repo, branch_name, limit=FIRST_RELEASE_COMMIT_CAP
            )
        except GitHubError as e:
            raise HTTPException(status_code=422, detail=e.message)
        shas = [c["sha"] for c in commits]
        truncated = len(shas) >= FIRST_RELEASE_COMMIT_CAP

    items = await items_in_range(settings, token, project_id, shas)
    version = await rpc(
        settings, token, "next_release_version", {"p_project": project_id}
    )

    return {
        "version": version,
        "commit_sha": head_sha,
        "branch": branch_name,
        "previous": (
            {"version": prev["version"], "commit_sha": prev["commit_sha"]}
            if prev
            else None
        ),
        "items": items,
        "commit_count": len(shas),
        "first_release": prev is None,
        "truncated": truncated,
        "blockers": blockers,
    }
