"""GitHub App connect/disconnect + repo/PR/Projects-v2 reads (US-1.19).

Connect/disconnect and reads use the caller's own installation token,
minted fresh per request (app/github.py) — no static shared token, no
cached installation tokens. The install callback is the one exception to
"everything goes through the caller's own JWT": GitHub redirects the
browser here directly, with no Supabase session attached, so trust comes
from the signed state token instead, recorded via a service-role RPC
(US-3.19) rather than a direct Postgres connection.
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from .. import github, github_tokens, issue_sync
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    PostgrestError,
    RpcError,
    admin_get,
    admin_rpc,
    postgrest_get,
    postgrest_patch,
    postgrest_post,
    rpc,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


def settings_slug(settings: Settings) -> str:
    return settings.github_app_slug


async def active_org_for(settings: Settings, user: AuthUser) -> str:
    """The workspace this caller is actually looking at (US-76.3).

    Every org-scoped page resolves through `principals.active_org_id` (US-9.7).
    The connect flow used to take an arbitrary membership — `limit 1` with no
    `order` — so a manager in several workspaces connected GitHub to whichever
    org PostgREST returned first, and the settings page then read the *active*
    org and found nothing. That is how one page came to say "GitHub connected."
    and "No GitHub account connected yet." at the same time.

    The fallback is ordered by `created_at`, so an unset active org is at least
    deterministic rather than arbitrary.
    """
    memberships = await postgrest_get(
        settings,
        user.token,
        "organization_members",
        {"select": "org_id", "status": "eq.active", "order": "created_at.asc"},
    )
    org_ids = [m["org_id"] for m in memberships]
    if not org_ids:
        raise HTTPException(status_code=404, detail="No organization membership")

    principals = await postgrest_get(
        settings,
        user.token,
        "principals",
        {"select": "active_org_id", "auth_user_id": f"eq.{user.id}", "limit": "1"},
    )
    stored = principals[0]["active_org_id"] if principals else None
    # Membership is re-checked rather than trusted: a stored org the caller has
    # since left must not be connectable.
    return stored if stored in org_ids else org_ids[0]


@router.get("/install/callback")
async def install_callback(
    installation_id: int,
    setup_action: str = "",
    state: str = "",
    settings: Settings = Depends(get_settings),
):
    try:
        org_id, user_id = github.verify_state(settings, state)
        # US-76.3: the state is signed, but it was minted before the redirect to
        # GitHub — membership can have changed in between, and a signature only
        # proves we issued it, not that it is still true. Service-role because
        # GitHub sends the browser here with no Supabase session attached.
        members = await admin_get(
            settings,
            "organization_members",
            {
                "select": "org_id",
                "org_id": f"eq.{org_id}",
                "user_id": f"eq.{user_id}",
                "status": "eq.active",
                "limit": "1",
            },
        )
        if not members:
            logger.warning(
                "GitHub install callback: %s is no longer an active member of %s",
                user_id,
                org_id,
            )
            return RedirectResponse(
                f"{settings.web_base_url}/settings/github?github=error"
            )
        info = await github.get_installation(settings, installation_id)
        account = info.get("account", {})
        await admin_rpc(
            settings,
            "record_github_app_installation",
            {
                "p_org": org_id,
                "p_installation_id": installation_id,
                "p_account_login": account.get("login", ""),
                "p_account_type": account.get("type", "User"),
                "p_connected_by": user_id,
            },
        )
    except (github.GitHubError, PostgrestError) as e:
        logger.warning("GitHub install callback failed: %s", e)
        return RedirectResponse(f"{settings.web_base_url}/settings/github?github=error")

    return RedirectResponse(f"{settings.web_base_url}/settings/github?github=connected")


@router.get("/connect-url")
async def connect_url(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    org_id = await active_org_for(settings, user)
    state = github.make_state(settings, org_id=org_id, user_id=user.id)
    slug = settings_slug(settings)
    return {"url": f"https://github.com/apps/{slug}/installations/new?state={state}"}


class PatConnectRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    token: str
    repos: list[str]


def _parse_expiry(header: str | None) -> str | None:
    """GitHub's header looks like '2027-01-15 10:30:00 UTC'."""
    if not header:
        return None
    try:
        dt = datetime.strptime(header.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        return None


@router.post("/connections/pat")
async def connect_pat(
    body: PatConnectRequest,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Paste-a-token connect (US-3.15). The PAT is validated against
    GitHub (identity + every entered repo) before anything is stored;
    storage happens inside the write-only connect_github_pat RPC. The
    token appears in no response and no log."""
    repo_names = [r.strip() for r in body.repos if r.strip()]
    if not repo_names:
        raise HTTPException(status_code=400, detail="Enter at least one repository")

    try:
        gh_user, expiry_header = await github.get_authenticated_user(body.token)
        repo_entries = []
        for full_name in repo_names:
            if "/" not in full_name:
                raise HTTPException(
                    status_code=400,
                    detail=f"'{full_name}' is not an owner/name repository",
                )
            owner, name = full_name.split("/", 1)
            info = await github.get_repo(body.token, owner, name)
            repo_entries.append(
                {"full_name": info["full_name"],
                 "default_branch": info.get("default_branch", "main")}
            )
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=e.message)

    # US-76.3: the same arbitrary-membership bug the App flow had. A pasted
    # token must land in the workspace the manager is looking at.
    org_id = await active_org_for(settings, user)

    expires_at = _parse_expiry(expiry_header)
    try:
        connection_id = await rpc(
            settings, user.token, "connect_github_pat",
            {
                "p_org": org_id,
                "p_token": body.token,
                "p_account_login": gh_user.get("login", ""),
                "p_account_type": gh_user.get("type", "User"),
                "p_expires_at": expires_at,
                "p_repos": repo_entries,
            },
        )
    except RpcError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {
        "id": connection_id,
        "account_login": gh_user.get("login", ""),
        "pat_last4": body.token[-4:],
        "pat_expires_at": expires_at,
        "repos": repo_entries,
    }


@router.post("/connections/{connection_id}/disconnect")
async def disconnect_connection(
    connection_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    rows = await postgrest_get(
        settings, user.token, "github_connections",
        {"select": "id,method,installation_id", "id": f"eq.{connection_id}"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Connection not found")
    row = rows[0]
    if row["method"] == "app":
        try:
            await github.uninstall(settings, row["installation_id"])
        except github.GitHubError as e:
            raise HTTPException(status_code=502, detail=e.message)
    try:
        await rpc(settings, user.token, "delete_github_connection", {"p_id": connection_id})
    except RpcError as e:
        raise HTTPException(status_code=400, detail=e.message)
    return {"ok": True}


@router.get("/repos")
async def repos(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    # US-76.4: this workspace's connections, not every workspace the caller
    # belongs to. RLS alone would list another org's repos in this org's repo
    # picker.
    org_id = await active_org_for(settings, user)
    connections = await postgrest_get(
        settings, user.token, "github_connections",
        {"select": "method,installation_id,repos", "org_id": f"eq.{org_id}"},
    )
    all_repos: list[dict] = []
    seen: set[str] = set()
    for conn_row in connections:
        if conn_row["method"] == "app":
            # US-5.24: a mint/list failure becomes a real error response
            # with the credential message — not an unhandled 500 that
            # reaches the browser CORS-stripped as "Failed to fetch".
            try:
                token = await github.mint_installation_token(
                    settings, conn_row["installation_id"]
                )
                entries = [
                    {"full_name": r["full_name"],
                     "default_branch": r.get("default_branch", "main")}
                    for r in await github.list_installation_repos(token)
                ]
            except github.GitHubError as e:
                logger.warning("GitHub repos listing failed: %s", e.message)
                raise HTTPException(status_code=502, detail=e.message)
        else:
            entries = conn_row.get("repos") or []
        for entry in entries:
            key = entry["full_name"].lower()
            if key not in seen:
                seen.add(key)
                all_repos.append(entry)
    return all_repos


async def _org_github_token(
    settings: Settings,
    user: AuthUser,
    repo_full_name: str | None = None,
    *,
    org_id: str | None = None,
) -> str:
    """US-3.15 resolver, HTTP-shaped (see github_tokens' preference order).
    US-5.24: credential failures are 502 (connection exists, GitHub said
    no), not-configured stays 404 — both carry the taxonomy message.

    US-76.4: `org_id` is the org that owns the work — pass it whenever a
    project, deployment or run is in hand. Only the repo-browsing endpoints
    omit it: they show *this workspace's* connected repos, so there is no
    owning row and the active org genuinely is the answer.
    """
    try:
        org = org_id or await active_org_for(settings, user)
        return await github_tokens.token_for_user(
            settings, user.token, org, repo_full_name
        )
    except github.GitHubCredentialError as e:
        raise HTTPException(status_code=502, detail=e.message)
    except github.GitHubError as e:
        raise HTTPException(status_code=404, detail=e.message)


@router.get("/repos/{owner}/{repo}/pulls")
async def repo_pulls(
    owner: str,
    repo: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    token = await _org_github_token(settings, user, f"{owner}/{repo}")
    try:
        pulls = await github.list_open_pulls(token, owner, repo)
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)
    return [
        {
            "number": p["number"],
            "title": p["title"],
            "author": p["user"]["login"],
            "url": p["html_url"],
            "updated_at": p["updated_at"],
        }
        for p in pulls
    ]


@router.get("/repos/{owner}/{repo}/branches")
async def repo_branches(
    owner: str,
    repo: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    token = await _org_github_token(settings, user, f"{owner}/{repo}")
    try:
        branches = await github.list_branches(token, owner, repo)
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)
    return [{"name": b["name"], "commit_sha": b["commit"]["sha"]} for b in branches]


class CreateBranchBody(BaseModel):
    name: str
    base_branch: str | None = None


# Git ref names may not contain spaces, ~, ^, :, ?, *, [, \, or control
# chars, start/end with /, or contain '..'. A light guard mirrors that.
_BAD_BRANCH = re.compile(r"[ ~^:?*\[\\\x00-\x1f]|\.\.|^/|/$|@\{")


@router.post("/repos/{owner}/{repo}/branches")
async def create_branch(
    owner: str,
    repo: str,
    body: CreateBranchBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-7.3: create a branch from the repo's default branch head (or an
    explicit base), via the GitHub App create-ref. Used by the release-branch
    pickers to make a UAT/Production branch without leaving Build Mill."""
    name = body.name.strip()
    if not name or _BAD_BRANCH.search(name) or name.endswith(".lock"):
        raise HTTPException(status_code=422, detail="invalid branch name")
    token = await _org_github_token(settings, user, f"{owner}/{repo}")
    try:
        base = (body.base_branch or "").strip()
        if not base:
            info = await github.get_repo(token, owner, repo)
            base = info.get("default_branch") or "main"
        base_branch = await github.get_branch(token, owner, repo, base)
        sha = base_branch["commit"]["sha"]
        await github.create_ref(token, owner, repo, name, sha)
    except github.GitHubError as e:
        raise HTTPException(status_code=422, detail=e.message)
    return {"name": name, "commit_sha": sha}


@router.post("/projects/{project_id}/issues/pull")
async def pull_issues(
    project_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-7.6: retired. GitHub Issue sync is gone — requirements live in
    Build Mill only. Kept as a 410 so any stale client fails clearly."""
    raise HTTPException(
        status_code=410,
        detail="GitHub Issue sync has been retired — author work items in Build Mill.",
    )


@router.get("/repos/{owner}/{repo}/projects")
async def repo_projects(
    owner: str,
    repo: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    token = await _org_github_token(settings, user, f"{owner}/{repo}")
    try:
        return await github.list_projects_v2(token, owner, repo)
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)
