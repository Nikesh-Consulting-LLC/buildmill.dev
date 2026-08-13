"""GitHub App integration: JWT/installation-token minting and thin GitHub
REST/GraphQL calls (US-1.19). One App (this service's own registration,
apps/api/.env), many per-org installations (github_connections table,
method='app', US-3.15). Installation tokens are minted fresh per
request — no cache.
"""

import base64
import logging
import time
from typing import Any

import httpx
import jwt

from .config import Settings

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
STATE_TTL_SECONDS = 600


class GitHubError(Exception):
    """`upstream_status` carries GitHub's HTTP status when the raise site knows
    it (US-79.2): 401 is the credential failing, 404 is the PR or repo being
    invisible — truths a caller can only branch on by the number, never by
    parsing GitHub's prose out of the message."""

    def __init__(self, message: str, upstream_status: int | None = None):
        super().__init__(message)
        self.message = message
        self.upstream_status = upstream_status


class GitHubNotConfigured(GitHubError):
    """US-5.24 taxonomy (a): the org has no GitHub connection at all (and
    no env-token fallback). Only a manager can fix it — connect in
    Settings → GitHub."""


class GitHubCredentialError(GitHubError):
    """US-5.24 taxonomy (b): a connection exists but its credential fails
    against GitHub (App/installation mismatch, bad key, revoked or
    missing PAT). Never worker-fixable — the manager must reconnect.
    upstream_status carries GitHub's HTTP status for the log line."""

    def __init__(self, message: str, upstream_status: int | None = None):
        super().__init__(message)
        self.upstream_status = upstream_status


class GitHubPermissionError(GitHubCredentialError):
    """US-5.24 taxonomy (b′): the credential authenticates but lacks a
    permission for this operation (HTTP 403) — e.g. an App installation
    without Checks: read. Manager-only fix: grant the permission on the
    GitHub App (or reconnect with a credential that has it); no retry
    or push can help. Subclasses GitHubCredentialError so every
    existing manager-must-fix path treats it as such."""

    def __init__(self, message: str, permission: str = ""):
        super().__init__(message, upstream_status=403)
        self.permission = permission


def permission_error(operation: str, permission: str) -> GitHubPermissionError:
    return GitHubPermissionError(
        f"GitHub refused to {operation} (HTTP 403): the connection lacks "
        f"the {permission} permission — the manager must grant it to the "
        "GitHub App (or reconnect with a credential that has it)",
        permission=permission,
    )


def make_state(settings: Settings, org_id: str, user_id: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"org_id": org_id, "user_id": user_id, "iat": now, "exp": now + STATE_TTL_SECONDS},
        settings.github_app_state_secret,
        algorithm="HS256",
    )


def verify_state(settings: Settings, state: str) -> tuple[str, str]:
    try:
        claims = jwt.decode(
            state, settings.github_app_state_secret, algorithms=["HS256"]
        )
    except Exception as e:
        raise GitHubError(f"invalid state: {e}")
    return claims["org_id"], claims["user_id"]


def mint_app_jwt(settings: Settings) -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": settings.github_app_id}
    return jwt.encode(payload, settings.github_app_private_key_pem, algorithm="RS256")


def _app_headers(settings: Settings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {mint_app_jwt(settings)}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _token_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def get_installation(settings: Settings, installation_id: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}",
            headers=_app_headers(settings),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"installation not found or inaccessible ({resp.status_code})")
    return resp.json()


def mint_error(status_code: int) -> GitHubCredentialError:
    """Map a token-mint failure to the credential taxonomy: 404 means the
    installation doesn't belong to the authenticated App, 401 means the
    App id or private key is wrong — both manager-only fixes."""
    if status_code == 404:
        return GitHubCredentialError(
            "GitHub App credentials mismatch: the installation does not "
            "belong to the configured App (HTTP 404) — the manager must "
            "reconnect GitHub in Settings → GitHub",
            upstream_status=404,
        )
    if status_code == 401:
        return GitHubCredentialError(
            "GitHub App authentication failed: bad App id or private key "
            "(HTTP 401) — the manager must reconnect GitHub in "
            "Settings → GitHub",
            upstream_status=401,
        )
    return GitHubCredentialError(
        f"could not mint a GitHub App token (HTTP {status_code}) — the "
        "manager must reconnect GitHub in Settings → GitHub",
        upstream_status=status_code,
    )


async def mint_installation_token(settings: Settings, installation_id: int) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers=_app_headers(settings),
        )
    if resp.status_code >= 400:
        raise mint_error(resp.status_code)
    return resp.json()["token"]


async def uninstall(settings: Settings, installation_id: int) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}",
            headers=_app_headers(settings),
        )
    if resp.status_code >= 400 and resp.status_code != 404:
        raise GitHubError(f"could not uninstall ({resp.status_code})")


async def list_installation_repos(token: str) -> list[dict[str, Any]]:
    repos: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            resp = await client.get(
                f"{GITHUB_API_BASE}/installation/repositories",
                params={"per_page": 100, "page": page},
                headers=_token_headers(token),
            )
            if resp.status_code >= 400:
                raise GitHubError(f"could not list repos ({resp.status_code})")
            batch = resp.json().get("repositories", [])
            repos.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return repos


async def list_branches(token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """All branches with their head commit (US-1.31 deployment form)."""
    branches: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches",
                params={"per_page": 100, "page": page},
                headers=_token_headers(token),
            )
            if resp.status_code >= 400:
                raise GitHubError(f"could not list branches ({resp.status_code})")
            batch = resp.json()
            branches.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return branches


async def get_branch(token: str, owner: str, repo: str, branch: str) -> dict[str, Any]:
    """One branch with its head commit (US-1.32 head resolution)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/branches/{branch}",
            headers=_token_headers(token),
        )
    if resp.status_code == 404:
        raise GitHubError(f"branch '{branch}' not found")
    if resp.status_code >= 400:
        raise GitHubError(f"could not resolve branch ({resp.status_code})")
    return resp.json()


async def list_branch_commits(
    token: str, owner: str, repo: str, branch: str, limit: int = 250
) -> list[dict[str, Any]]:
    """US-21.1: a branch's recent commits, newest first, capped at `limit`.

    Used only for a project's FIRST release, where there is no previous
    commit to compare against. The cap is reported to the caller rather than
    applied silently — a range that claims to be complete and isn't is worse
    than one that says where it stops.
    """
    out: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=20) as client:
        while len(out) < limit:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits",
                headers=_token_headers(token),
                params={"sha": branch, "per_page": 100, "page": page},
            )
            if resp.status_code == 404:
                raise GitHubError(f"branch '{branch}' not found")
            if resp.status_code == 409:
                return []  # empty repository — no commits at all
            if resp.status_code >= 400:
                raise GitHubError(f"could not list commits ({resp.status_code})")
            batch = resp.json()
            if not batch:
                break
            out.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return out[:limit]


async def get_commit(token: str, owner: str, repo: str, ref: str) -> dict[str, Any]:
    """Resolve any ref — branch name or commit SHA — to a commit (US-1.50)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}",
            headers=_token_headers(token),
        )
    if resp.status_code in (404, 422):
        raise GitHubError(f"ref '{ref}' not found in this repo")
    if resp.status_code >= 400:
        raise GitHubError(f"could not resolve ref ({resp.status_code})")
    return resp.json()


async def compare_commits(
    token: str, owner: str, repo: str, base: str, head: str
) -> dict[str, Any]:
    """Compare a deployed commit (base) to a branch head (US-1.34 drift).
    GitHub answers status identical/ahead/behind/diverged + the commits."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base}...{head}",
            headers=_token_headers(token),
        )
    if resp.status_code == 404:
        raise GitHubError("commits not comparable (rewritten history?)")
    if resp.status_code >= 400:
        raise GitHubError(f"could not compare commits ({resp.status_code})")
    return resp.json()


async def get_compare_diff(
    token: str, owner: str, repo: str, base: str, head: str
) -> str:
    """Unified diff between base and head (US-3.2 code-submit verification
    — the review panel no longer trusts a worker-posted diff)."""
    headers = _token_headers(token) | {"Accept": "application/vnd.github.diff"}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/compare/{base}...{head}",
            headers=headers,
        )
    if resp.status_code == 404:
        raise GitHubError("branches not comparable")
    if resp.status_code >= 400:
        raise GitHubError(f"could not fetch diff ({resp.status_code})")
    return resp.text


async def get_tree(
    token: str, owner: str, repo: str, ref: str
) -> dict[str, Any]:
    """Full recursive tree at a ref (US-5.20 repo browsing). Returns
    GitHub's payload: {tree: [{path, type, size?, ...}], truncated}."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/{ref}",
            params={"recursive": "1"},
            headers=_token_headers(token),
        )
    if resp.status_code in (404, 422):
        raise GitHubError(f"ref '{ref}' not found in this repo")
    if resp.status_code >= 400:
        raise GitHubError(f"could not list tree ({resp.status_code})")
    return resp.json()


async def get_content(
    token: str, owner: str, repo: str, path: str, ref: str
) -> dict[str, Any]:
    """One path's contents-API entry at a ref (US-5.20 file reads):
    {type, size, encoding, content} for files."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers=_token_headers(token),
        )
    if resp.status_code == 404:
        raise GitHubError(f"path '{path}' not found at '{ref}'")
    if resp.status_code >= 400:
        raise GitHubError(f"could not read {path} ({resp.status_code})")
    return resp.json()


async def get_file_sha(
    token: str, owner: str, repo: str, path: str, ref: str
) -> str | None:
    """SHA of a file at a ref, or None if it doesn't exist yet (US-1.52
    Save Instructions — needed to update rather than blind-create)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers=_token_headers(token),
        )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubError(f"could not read {path} ({resp.status_code})")
    return resp.json()["sha"]


async def create_or_update_file(
    token: str,
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
    sha: str | None = None,
) -> dict[str, Any]:
    """Contents API create-or-update (US-1.52 Save Instructions)."""
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode()).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.put(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}",
            json=body,
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        detail = resp.json().get("message", "commit failed")
        raise GitHubError(f"could not write {path}: {detail}")
    return resp.json()


async def create_pull(
    token: str,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str = "",
) -> dict[str, Any]:
    """Open a PR as the App (US-3.2) — workers never open PRs themselves."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
            json={"title": title, "head": head, "base": base, "body": body},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        # US-50.2: an external deployment run puts GitHub's own words in the
        # run log, so the reason travels rather than the status code alone.
        try:
            detail = resp.json().get("message") or ""
        except ValueError:
            detail = ""
        raise GitHubError(
            f"could not open pull request ({resp.status_code})"
            + (f": {detail}" if detail else "")
        )
    return resp.json()


async def find_open_pull(
    token: str, owner: str, repo: str, head: str, base: str
) -> dict[str, Any] | None:
    """The open PR from `head` into `base`, or None (US-50.2).

    An external deployment reuses the PR a refused merge left standing rather
    than opening a second one — the open PR is the artifact that explains why
    the last run stopped.
    """
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
            params={
                "state": "open",
                "head": f"{owner}:{head}",
                "base": base,
                "per_page": 1,
            },
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not list pull requests ({resp.status_code})")
    rows = resp.json()
    return rows[0] if rows else None


async def download_tarball(
    token: str, owner: str, repo: str, ref: str, dest_path: str
) -> int:
    """Stream a commit's tarball to a local file; returns byte count
    (US-1.32 payload fetch). GitHub answers with a redirect to codeload."""
    total = 0
    timeout = httpx.Timeout(300.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream(
            "GET",
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/tarball/{ref}",
            headers=_token_headers(token),
        ) as resp:
            if resp.status_code >= 400:
                raise GitHubError(f"could not download archive ({resp.status_code})")
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)
                    total += len(chunk)
    return total


# --- git-data primitives (US-5.26 server-side commit construction) ---


async def create_blob(
    token: str, owner: str, repo: str, content_b64: str
) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/blobs",
            json={"content": content_b64, "encoding": "base64"},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not create blob ({resp.status_code})")
    return resp.json()["sha"]


async def create_tree(
    token: str,
    owner: str,
    repo: str,
    base_tree: str,
    entries: list[dict[str, Any]],
) -> str:
    """A tree on top of base_tree; an entry with sha=None deletes."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees",
            json={"base_tree": base_tree, "tree": entries},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        detail = resp.json().get("message", "tree failed")
        raise GitHubError(f"could not build tree: {detail}")
    return resp.json()["sha"]


async def create_commit(
    token: str,
    owner: str,
    repo: str,
    message: str,
    tree_sha: str,
    parent_sha: str,
    author_name: str,
    author_email: str,
) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/commits",
            json={
                "message": message,
                "tree": tree_sha,
                "parents": [parent_sha],
                "author": {"name": author_name, "email": author_email},
            },
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        detail = resp.json().get("message", "commit failed")
        raise GitHubError(f"could not create commit: {detail}")
    return resp.json()["sha"]


async def get_ref(
    token: str, owner: str, repo: str, branch: str
) -> dict[str, Any] | None:
    """The branch ref object, or None when the branch doesn't exist."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/ref/heads/{branch}",
            headers=_token_headers(token),
        )
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        raise GitHubError(f"could not read ref ({resp.status_code})")
    return resp.json()


async def create_ref(
    token: str, owner: str, repo: str, branch: str, sha: str
) -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": sha},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        detail = resp.json().get("message", "ref create failed")
        raise GitHubError(f"could not create branch: {detail}")


async def create_tag(
    token: str, owner: str, repo: str, tag: str, sha: str
) -> None:
    """US-7.14: create a lightweight tag refs/tags/<tag> at <sha>. A tag that
    already exists is treated as success (idempotent re-cut of the same head)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs",
            json={"ref": f"refs/tags/{tag}", "sha": sha},
            headers=_token_headers(token),
        )
    if resp.status_code == 422:
        # Reference already exists — the same version tag; treat as done.
        detail = resp.json().get("message", "")
        if "already exists" in detail.lower():
            return
        raise GitHubError(f"could not create tag: {detail}")
    if resp.status_code >= 400:
        detail = resp.json().get("message", "tag create failed")
        raise GitHubError(f"could not create tag: {detail}")


async def update_ref(
    token: str, owner: str, repo: str, branch: str, sha: str
) -> None:
    """Fast-forward only (force never set) — a concurrent push loses
    cleanly instead of being overwritten (US-5.26 stale-base guard)."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.patch(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": sha, "force": False},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        detail = resp.json().get("message", "ref update failed")
        raise GitHubError(f"could not update branch: {detail}")


async def delete_ref(token: str, owner: str, repo: str, branch: str) -> None:
    """US-15.17: delete refs/heads/<branch>. A branch that's already gone
    (404/422) is treated as success — the reset's intent is "this branch does
    not exist", so an already-absent branch satisfies it idempotently."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.delete(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/refs/heads/{branch}",
            headers=_token_headers(token),
        )
    if resp.status_code in (404, 422):
        return
    if resp.status_code >= 400:
        detail = resp.json().get("message", "ref delete failed")
        raise GitHubError(f"could not delete branch: {detail}")


async def download_zipball(
    token: str, owner: str, repo: str, ref: str, max_bytes: int
) -> bytes | None:
    """A commit's zip archive in memory, or None when it exceeds
    max_bytes (US-5.25 hand-off to the git remote — never truncated).
    GitHub's zipball carries one top-level folder and no .git."""
    total = 0
    chunks: list[bytes] = []
    timeout = httpx.Timeout(300.0, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream(
            "GET",
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/zipball/{ref}",
            headers=_token_headers(token),
        ) as resp:
            if resp.status_code in (404, 422):
                raise GitHubError(f"ref '{ref}' not found in this repo")
            if resp.status_code >= 400:
                raise GitHubError(
                    f"could not download archive ({resp.status_code})"
                )
            async for chunk in resp.aiter_bytes(65536):
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
    return b"".join(chunks)


async def list_open_pulls(token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls",
            params={"state": "open", "per_page": 100},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not list pull requests ({resp.status_code})")
    return resp.json()


async def list_open_issues(token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    """Open issues only — GitHub's issues endpoint also returns pull
    requests, so those are filtered out (US-1.20)."""
    issues: list[dict[str, Any]] = []
    page = 1
    async with httpx.AsyncClient(timeout=10) as client:
        while True:
            resp = await client.get(
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues",
                params={"state": "open", "per_page": 100, "page": page},
                headers=_token_headers(token),
            )
            if resp.status_code >= 400:
                raise GitHubError(f"could not list issues ({resp.status_code})")
            batch = resp.json()
            issues.extend(i for i in batch if "pull_request" not in i)
            if len(batch) < 100:
                break
            page += 1
    return issues


async def set_issue_state(
    token: str, owner: str, repo: str, number: int, state: str
) -> None:
    """state is 'open' or 'closed' (US-1.20)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues/{number}",
            json={"state": state},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not set issue state ({resp.status_code})")


PROJECTS_V2_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 20) {
      nodes { id title url }
    }
  }
}
"""


async def list_projects_v2(token: str, owner: str, repo: str) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            GITHUB_GRAPHQL_URL,
            json={"query": PROJECTS_V2_QUERY, "variables": {"owner": owner, "name": repo}},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not list projects ({resp.status_code})")
    data = resp.json()
    if data.get("errors"):
        raise GitHubError(str(data["errors"]))
    return data["data"]["repository"]["projectsV2"]["nodes"]


async def list_check_runs(
    token: str, owner: str, repo: str, ref: str
) -> list[dict[str, Any]]:
    """Check runs for a commit (US-5.22 PR status): name/status/conclusion."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits/{ref}/check-runs",
            params={"per_page": 50},
            headers=_token_headers(token),
        )
    if resp.status_code == 403:
        logger.warning(
            "GitHub permission failure: list check-runs on %s/%s@%s -> 403 "
            "(Checks: read not granted)",
            owner,
            repo,
            ref,
        )
        raise permission_error("list checks", "Checks: read")
    if resp.status_code >= 400:
        raise GitHubError(f"could not list checks ({resp.status_code})")
    return resp.json().get("check_runs", [])


REVIEW_THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 50) {
        nodes {
          isResolved
          comments(first: 3) {
            nodes { author { login } path line body }
          }
        }
      }
    }
  }
}
"""


async def list_review_threads(
    token: str, owner: str, repo: str, number: int
) -> list[dict[str, Any]]:
    """Review threads with resolution state (US-5.22) — the REST comments
    list can't say what's still unresolved; GraphQL can."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            GITHUB_GRAPHQL_URL,
            json={
                "query": REVIEW_THREADS_QUERY,
                "variables": {"owner": owner, "name": repo, "number": number},
            },
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not list review threads ({resp.status_code})")
    data = resp.json()
    if data.get("errors"):
        raise GitHubError(str(data["errors"]))
    pr = (data["data"]["repository"] or {}).get("pullRequest") or {}
    return (pr.get("reviewThreads") or {}).get("nodes") or []


async def get_pull(token: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(
            f"pull request not found ({resp.status_code})",
            upstream_status=resp.status_code,
        )
    return resp.json()


async def list_pull_files(
    token: str, owner: str, repo: str, number: int
) -> list[dict[str, Any]]:
    """Files changed by a PR (filename/status) — used as a proxy for "likely
    conflicting files" when a merge fails as dirty; GitHub's API doesn't
    expose actual conflicting hunks without a local merge."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/files",
            params={"per_page": 100},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(
            f"could not list PR files ({resp.status_code})",
            upstream_status=resp.status_code,
        )
    return resp.json()


REVERT_MUTATION = """
mutation($id: ID!, $title: String) {
  revertPullRequest(input: {pullRequestId: $id, title: $title}) {
    revertPullRequest { url }
  }
}
"""


async def revert_pull_request(token: str, node_id: str, title: str) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            GITHUB_GRAPHQL_URL,
            json={"query": REVERT_MUTATION, "variables": {"id": node_id, "title": title}},
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not revert pull request ({resp.status_code})")
    data = resp.json()
    if data.get("errors"):
        raise GitHubError(str(data["errors"]))
    return data["data"]["revertPullRequest"]["revertPullRequest"]["url"]


async def merge_pull_request(
    token: str, owner: str, repo: str, number: int, merge_method: str = "squash"
) -> str | None:
    """Merge a PR; returns the merge commit SHA (US-1.48 traceability).

    Squash by default — that is what a work item's PR wants. US-50.2 passes
    `merge` instead: an external deployment must leave every source commit
    reachable from the target branch, or the next run's "what would ship"
    comparison stops being true.
    """
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/merge",
            json={"merge_method": merge_method},
            headers=_token_headers(token),
        )
    if resp.status_code >= 300:
        try:
            detail = resp.json().get("message", "merge failed")
        except ValueError:
            detail = "merge failed"
        raise GitHubError(
            f"GitHub merge failed: {detail}", upstream_status=resp.status_code
        )
    return resp.json().get("sha")


async def get_authenticated_user(token: str) -> tuple[dict[str, Any], str | None]:
    """GET /user — PAT validation (US-3.15). Returns the user payload and
    GitHub's fine-grained-PAT expiry header value when present."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/user", headers=_token_headers(token)
        )
    if resp.status_code >= 400:
        raise GitHubError(f"token rejected by GitHub ({resp.status_code})")
    return resp.json(), resp.headers.get("github-authentication-token-expiration")


async def get_repo(token: str, owner: str, repo: str) -> dict[str, Any]:
    """GET /repos/{owner}/{repo} — proves a hand-entered repo is reachable
    with the pasted PAT before it's saved (US-3.15)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}",
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(
            f"repository {owner}/{repo} not reachable with this token"
            f" ({resp.status_code})"
        )
    return resp.json()


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """https://github.com/{owner}/{repo}/pull/{n} -> (owner, repo, n)."""
    try:
        _, _, _, owner, repo, _, number = pr_url.rstrip("/").split("/")
        return owner, repo, int(number)
    except ValueError:
        raise GitHubError(f"unrecognized PR URL: {pr_url}")
