# US-1.19 GitHub App Connection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the free-text `owner/name` repo field and static `GITHUB_TOKEN` with a real per-org GitHub App connection — install flow, installation storage, repo picker, PR/Projects-v2 reads, and installation-token-based merge/revert.

**Architecture:** One platform-level GitHub App (credentials in `apps/api/.env`, never Supabase) that any org can install; `github_installations` (org-scoped RLS) stores only the numeric `installation_id`. A new `app/github.py` module mints App JWTs and per-request installation tokens and wraps the handful of GitHub REST/GraphQL calls needed. Everything user-facing goes through PostgREST + RLS with the caller's own JWT (per `db.py`'s existing architecture comment); only the unauthenticated install callback (no user JWT available — it's a GitHub redirect) writes via the existing `db.py` direct-Postgres pattern, trust-anchored by a signed state token instead of a shared secret.

**Tech Stack:** FastAPI + httpx + PyJWT[crypto] (already installed, RS256 support included) for the API side; Next.js client components (`apiFetch`) for the web side — no new dependencies either side.

## Global Constraints

- User-facing endpoints authenticate every GitHub-affecting read/write through the caller's own Supabase JWT (`postgrest_get`/`postgrest_post`/`postgrest_delete` — RLS scopes automatically, cross-org rows are simply invisible). Only `GET /github/install/callback` (no user JWT exists for a GitHub-initiated redirect) uses the `db.py` direct-Postgres pattern, and only because its request is trust-anchored by a signed, expiring state token minted earlier by an authenticated user.
- Installation tokens are minted fresh per request — no cache, no token store.
- The GitHub App's private key lives only in `apps/api/.env` (`GITHUB_APP_PRIVATE_KEY`), never in Supabase. `apps/api/.env*` files are permission-blocked from direct edit by this session — Task 12 documents the exact lines for the user to add by hand.
- No toast library in this codebase — errors/notices are inline text, matching every existing form (`task-dialog.tsx`, `settings-form.tsx`).
- shadcn/ui here is Base UI: triggers use `render={<Button />}`, not `asChild`.
- GitHub's REST API has no revert-PR endpoint — revert uses the GraphQL `revertPullRequest` mutation.
- `npm run build` (typecheck) and the full `apps/api` pytest suite must pass before this story moves to Testing. No frontend test framework exists in this repo — frontend correctness is build-verified, not unit-tested, matching prior stories. The user does UI/browser testing themselves.
- Do not mark the story `Completed` — this plan moves it to `Testing` only.

---

### Task 1: Migration — `github_installations` table

**Files:**
- Create: `infra/supabase/migrations/010_github_installations.sql`

**Interfaces:**
- Produces: table `public.github_installations(id, org_id, installation_id, account_login, account_type, connected_by, created_at, updated_at)`.

- [ ] **Step 1: Write the migration**

```sql
-- 010_github_installations: per-org GitHub App installations (US-1.19).
-- One platform-level GitHub App (credentials in apps/api/.env, never
-- here); this table stores only the installation id per org — no token,
-- no private key material.

create table public.github_installations (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references public.organizations(id) on delete cascade,
  installation_id bigint not null unique,
  account_login text not null,
  account_type text not null check (account_type in ('User', 'Organization')),
  connected_by uuid not null references auth.users(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index github_installations_org_idx on public.github_installations (org_id);

alter table public.github_installations enable row level security;

create policy "members manage their org github installations"
  on public.github_installations for all
  using (public.is_org_member(org_id))
  with check (public.is_org_member(org_id));

create trigger github_installations_updated_at
  before update on public.github_installations
  for each row execute function public.touch_updated_at();
```

- [ ] **Step 2: Apply the migration to the live Supabase project**

Use the Supabase MCP tool `apply_migration` with `project_id` = `wdudmfhhqxrqzoyhuzwx`, `name` = `010_github_installations`, and the SQL from Step 1.

- [ ] **Step 3: Verify with `list_tables` / `get_advisors`**

`list_tables` (schema `public`) confirms `github_installations` with RLS enabled. `get_advisors` (type `security`) shows no new finding beyond the same pre-existing `function_search_path_mutable` class already present for every other trigger-adjacent function — nothing new caused by this table.

- [ ] **Step 4: Regenerate TypeScript types**

`generate_typescript_types` for project `wdudmfhhqxrqzoyhuzwx`; overwrite `apps/web/src/lib/supabase/database.types.ts` with the full result (always a full-file replace).

- [ ] **Step 5: Commit**

```bash
git add infra/supabase/migrations/010_github_installations.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat: add github_installations table"
```

---

### Task 2: Backend config + PostgREST client additions

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/supabase.py`

**Interfaces:**
- Produces: `Settings.github_app_id`, `.github_app_slug`, `.github_app_private_key`, `.github_app_private_key_pem` (property, `\n`-unescaped), `.github_app_state_secret`, `.web_base_url`; `postgrest_post(settings, user_token, path, body) -> Any`; `postgrest_delete(settings, user_token, path, params) -> None`.

- [ ] **Step 1: Add GitHub App settings fields**

In `apps/api/app/config.py`, add to the `Settings` class (after `github_token: str = ""`):

```python
    github_app_id: str = ""
    github_app_slug: str = ""
    # PEM contents with literal \n (single-line .env value); use the
    # .github_app_private_key_pem property below, never this field raw.
    github_app_private_key: str = ""
    github_app_state_secret: str = ""
    web_base_url: str = "http://localhost:3000"
```

And add a property after the existing `rest_url` property:

```python
    @property
    def github_app_private_key_pem(self) -> str:
        return self.github_app_private_key.replace("\\n", "\n")
```

- [ ] **Step 2: Add `postgrest_post` and `postgrest_delete` to the thin PostgREST client**

In `apps/api/app/supabase.py`, add after `postgrest_get`:

```python
async def postgrest_post(
    settings: Settings, user_token: str, path: str, body: dict[str, Any]
) -> Any:
    headers = _headers(settings, user_token)
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.rest_url}/{path}", json=body, headers=headers
        )
    resp.raise_for_status()
    return resp.json()


async def postgrest_delete(
    settings: Settings, user_token: str, path: str, params: dict[str, str]
) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{settings.rest_url}/{path}",
            params=params,
            headers=_headers(settings, user_token),
        )
    resp.raise_for_status()
```

- [ ] **Step 3: Run the existing suite to confirm nothing broke**

```bash
apps/api/.venv/Scripts/python -m pytest -v
```
Expected: all existing tests still pass (pure additions, no behavior change yet).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/config.py apps/api/app/supabase.py
git commit -m "feat: add GitHub App settings and postgrest_post/delete helpers"
```

---

### Task 3: `app/github.py` — JWT/token minting + GitHub REST/GraphQL calls

**Files:**
- Create: `apps/api/app/github.py`
- Create: `apps/api/tests/test_github_module.py`

**Interfaces:**
- Consumes: `Settings` (Task 2).
- Produces: `GitHubError`; `make_state(settings, org_id, user_id) -> str`; `verify_state(settings, state) -> tuple[str, str]`; `mint_app_jwt(settings) -> str`; `get_installation(settings, installation_id) -> dict`; `mint_installation_token(settings, installation_id) -> str`; `uninstall(settings, installation_id) -> None`; `list_installation_repos(token) -> list[dict]`; `list_open_pulls(token, owner, repo) -> list[dict]`; `list_projects_v2(token, owner, repo) -> list[dict]`; `get_pull(token, owner, repo, number) -> dict`; `revert_pull_request(token, node_id, title) -> str`; `merge_pull_request(token, owner, repo, number) -> None`; `parse_pr_url(pr_url) -> tuple[str, str, int]`.

- [ ] **Step 1: Write failing unit tests for the pure functions (state token + JWT + URL parsing)**

```python
"""app/github.py — state token, App JWT, and pure helpers (US-1.19)."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from app import github
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        github_app_id="123456",
        github_app_slug="software-factory-dev",
        github_app_state_secret="test-state-secret",
    )
    base.update(overrides)
    return Settings(**base)


def _rsa_pem() -> tuple[str, str]:
    """Returns (private_pem, public_pem) as strings."""
    key = generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def test_state_roundtrip():
    settings = _settings()
    state = github.make_state(settings, org_id="org-1", user_id="user-1")
    org_id, user_id = github.verify_state(settings, state)
    assert org_id == "org-1"
    assert user_id == "user-1"


def test_state_tampered_is_rejected():
    settings = _settings()
    state = github.make_state(settings, org_id="org-1", user_id="user-1")
    with pytest.raises(github.GitHubError):
        github.verify_state(_settings(github_app_state_secret="different"), state)


def test_state_expired_is_rejected():
    settings = _settings()
    now = int(time.time())
    expired = jwt.encode(
        {"org_id": "org-1", "user_id": "user-1", "iat": now - 1000, "exp": now - 1},
        settings.github_app_state_secret,
        algorithm="HS256",
    )
    with pytest.raises(github.GitHubError):
        github.verify_state(settings, expired)


def test_mint_app_jwt_is_valid_rs256():
    private_pem, public_pem = _rsa_pem()
    settings = _settings(github_app_private_key=private_pem)
    token = github.mint_app_jwt(settings)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert claims["iss"] == "123456"


def test_private_key_pem_unescapes_literal_newlines():
    settings = _settings(github_app_private_key="line1\\nline2")
    assert settings.github_app_private_key_pem == "line1\nline2"


def test_parse_pr_url():
    owner, repo, number = github.parse_pr_url(
        "https://github.com/acme/webshop/pull/42"
    )
    assert (owner, repo, number) == ("acme", "webshop", 42)


def test_parse_pr_url_invalid_raises():
    with pytest.raises(github.GitHubError):
        github.parse_pr_url("not-a-url")
```

Save as `apps/api/tests/test_github_module.py`.

- [ ] **Step 2: Run to verify it fails**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_github_module.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.github'`.

- [ ] **Step 3: Implement `app/github.py`**

```python
"""GitHub App integration: JWT/installation-token minting and thin GitHub
REST/GraphQL calls (US-1.19). One App (this service's own registration,
apps/api/.env), many per-org installations (github_installations table).
Installation tokens are minted fresh per request — no cache.
"""

import time
from typing import Any

import httpx
import jwt

from .config import Settings

GITHUB_API_BASE = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
STATE_TTL_SECONDS = 600


class GitHubError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


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


async def mint_installation_token(settings: Settings, installation_id: int) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens",
            headers=_app_headers(settings),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"could not mint installation token ({resp.status_code})")
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


async def get_pull(token: str, owner: str, repo: str, number: int) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}",
            headers=_token_headers(token),
        )
    if resp.status_code >= 400:
        raise GitHubError(f"pull request not found ({resp.status_code})")
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


async def merge_pull_request(token: str, owner: str, repo: str, number: int) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.put(
            f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{number}/merge",
            json={"merge_method": "squash"},
            headers=_token_headers(token),
        )
    if resp.status_code >= 300:
        detail = resp.json().get("message", "merge failed")
        raise GitHubError(f"GitHub merge failed: {detail}")


def parse_pr_url(pr_url: str) -> tuple[str, str, int]:
    """https://github.com/{owner}/{repo}/pull/{n} -> (owner, repo, n)."""
    try:
        _, _, _, owner, repo, _, number = pr_url.rstrip("/").split("/")
        return owner, repo, int(number)
    except ValueError:
        raise GitHubError(f"unrecognized PR URL: {pr_url}")
```

- [ ] **Step 4: Run to verify it passes**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_github_module.py -v
```
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/github.py apps/api/tests/test_github_module.py
git commit -m "feat: add GitHub App JWT/token minting and REST/GraphQL helpers"
```

---

### Task 4: `db.py` — install-callback write path

**Files:**
- Modify: `apps/api/app/db.py`

**Interfaces:**
- Produces: `upsert_github_installation(settings, org_id, installation_id, account_login, account_type, connected_by) -> None`.

- [ ] **Step 1: Add the function**

In `apps/api/app/db.py`, add after `complete_run`:

```python
def upsert_github_installation(
    settings: Settings,
    org_id: str,
    installation_id: int,
    account_login: str,
    account_type: str,
    connected_by: str,
) -> None:
    """Install callback has no user JWT (GitHub redirects the browser here
    directly) — trust comes from the signed state token verified by the
    caller, not RLS. Same direct-Postgres pattern as runner claim/callback."""
    with _connect(settings) as conn:
        conn.execute(
            """
            insert into public.github_installations
              (org_id, installation_id, account_login, account_type, connected_by)
            values (%s, %s, %s, %s, %s)
            on conflict (installation_id) do update set
              org_id = excluded.org_id,
              account_login = excluded.account_login,
              account_type = excluded.account_type,
              connected_by = excluded.connected_by,
              updated_at = now()
            """,
            (org_id, installation_id, account_login, account_type, connected_by),
        )
        conn.commit()
```

- [ ] **Step 2: Commit**

```bash
git add apps/api/app/db.py
git commit -m "feat: add upsert_github_installation for the install callback"
```

---

### Task 5: `routers/github.py` — connect/disconnect/repos/pulls/projects

**Files:**
- Create: `apps/api/app/routers/github.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_github.py`

**Interfaces:**
- Consumes: `app.db.upsert_github_installation` (Task 4); `app.github.*` (Task 3); `postgrest_get`/`postgrest_delete` (Task 2); `AuthUser`/`verify_token` (existing `app/auth.py`).
- Produces: router `github.router` (prefix `/github`) with `GET /install/callback`, `GET /connect-url`, `POST /installations/{installation_id}/disconnect`, `GET /repos`, `GET /repos/{owner}/{repo}/pulls`, `GET /repos/{owner}/{repo}/projects` — registered in `main.py` under `/api/v1`.

- [ ] **Step 1: Write the failing tests**

```python
"""GitHub App connect/disconnect + repo/PR/Projects-v2 reads (US-1.19)."""

INSTALLATION_ID = 987654


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_install_callback_valid_state_upserts_and_redirects(
    client, make_token, monkeypatch
):
    called = {}

    def fake_verify_state(settings, state):
        assert state == "good-state"
        return ("org-1", "user-1")

    async def fake_get_installation(settings, installation_id):
        assert installation_id == INSTALLATION_ID
        return {"account": {"login": "acme", "type": "Organization"}}

    def fake_upsert(settings, **kwargs):
        called.update(kwargs)

    monkeypatch.setattr("app.routers.github.github.verify_state", fake_verify_state)
    monkeypatch.setattr(
        "app.routers.github.github.get_installation", fake_get_installation
    )
    monkeypatch.setattr("app.routers.github.db.upsert_github_installation", fake_upsert)

    resp = client.get(
        f"/api/v1/github/install/callback"
        f"?installation_id={INSTALLATION_ID}&setup_action=install&state=good-state",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "github=connected" in resp.headers["location"]
    assert called["org_id"] == "org-1"
    assert called["connected_by"] == "user-1"
    assert called["account_login"] == "acme"


def test_install_callback_invalid_state_redirects_to_error(client, monkeypatch):
    def fake_verify_state(settings, state):
        raise __import__("app.github", fromlist=["GitHubError"]).GitHubError("bad")

    monkeypatch.setattr("app.routers.github.github.verify_state", fake_verify_state)

    resp = client.get(
        f"/api/v1/github/install/callback"
        f"?installation_id={INSTALLATION_ID}&setup_action=install&state=bad",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert "github=error" in resp.headers["location"]


def test_connect_url_returns_install_link(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        assert path == "organization_members"
        return [{"org_id": "org-1"}]

    def fake_make_state(settings, org_id, user_id):
        assert org_id == "org-1"
        return "signed-state"

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.make_state", fake_make_state)
    monkeypatch.setattr("app.routers.github.settings_slug", lambda s: "my-app")

    resp = client.get("/api/v1/github/connect-url", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["url"].endswith("/installations/new?state=signed-state")


def test_repos_merges_across_installations(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        assert path == "github_installations"
        return [{"installation_id": 1}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_list(token):
        return [{"full_name": "acme/webshop", "default_branch": "main"}]

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.github.github.list_installation_repos", fake_list)

    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == [{"full_name": "acme/webshop", "default_branch": "main"}]


def test_repos_cross_org_is_empty(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []  # RLS hides other orgs' installations

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == []


def test_disconnect_uninstalls_and_deletes(client, make_token, monkeypatch):
    called = {}

    async def fake_get(settings, token, path, params):
        return [{"id": "row-1"}]

    async def fake_uninstall(settings, installation_id):
        called["uninstalled"] = installation_id

    async def fake_delete(settings, token, path, params):
        called["deleted"] = params

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.uninstall", fake_uninstall)
    monkeypatch.setattr("app.routers.github.postgrest_delete", fake_delete)

    resp = client.post(
        f"/api/v1/github/installations/{INSTALLATION_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert called["uninstalled"] == INSTALLATION_ID


def test_disconnect_cross_org_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []  # RLS hides it

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/github/installations/{INSTALLATION_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


def test_repo_pulls_happy_path(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"installation_id": 1}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_pulls(token, owner, repo):
        assert (owner, repo) == ("acme", "webshop")
        return [
            {
                "number": 7,
                "title": "Fix bug",
                "user": {"login": "alice"},
                "html_url": "https://github.com/acme/webshop/pull/7",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.github.github.list_open_pulls", fake_pulls)

    resp = client.get(
        "/api/v1/github/repos/acme/webshop/pulls", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()[0]["author"] == "alice"


def test_repo_pulls_no_installation_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    resp = client.get(
        "/api/v1/github/repos/acme/webshop/pulls", headers=_auth(make_token)
    )
    assert resp.status_code == 404


def test_repo_projects_happy_path(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"installation_id": 1}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_projects(token, owner, repo):
        return [{"id": "PVT_1", "title": "Roadmap", "url": "https://github.com/orgs/acme/projects/1"}]

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.github.github.list_projects_v2", fake_projects)

    resp = client.get(
        "/api/v1/github/repos/acme/webshop/projects", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()[0]["title"] == "Roadmap"


def test_repos_without_token_is_401(client):
    resp = client.get("/api/v1/github/repos")
    assert resp.status_code == 401
```

Save as `apps/api/tests/test_github.py`.

- [ ] **Step 2: Run to verify it fails**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_github.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.routers.github'`.

- [ ] **Step 3: Implement the router**

```python
"""GitHub App connect/disconnect + repo/PR/Projects-v2 reads (US-1.19).

Connect/disconnect and reads use the caller's own installation token,
minted fresh per request (app/github.py) — no static shared token, no
cached installation tokens. The install callback is the one exception to
"everything goes through the caller's own JWT": GitHub redirects the
browser here directly, with no Supabase session attached, so trust comes
from the signed state token instead (see app/db.py).
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from .. import db, github
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import postgrest_delete, postgrest_get

router = APIRouter(prefix="/github", tags=["github"])


def settings_slug(settings: Settings) -> str:
    return settings.github_app_slug


@router.get("/install/callback")
async def install_callback(
    installation_id: int,
    setup_action: str = "",
    state: str = "",
    settings: Settings = Depends(get_settings),
):
    try:
        org_id, user_id = github.verify_state(settings, state)
        info = await github.get_installation(settings, installation_id)
    except github.GitHubError:
        return RedirectResponse(f"{settings.web_base_url}/settings?github=error")

    account = info.get("account", {})
    db.upsert_github_installation(
        settings,
        org_id=org_id,
        installation_id=installation_id,
        account_login=account.get("login", ""),
        account_type=account.get("type", "User"),
        connected_by=user_id,
    )
    return RedirectResponse(f"{settings.web_base_url}/settings?github=connected")


@router.get("/connect-url")
async def connect_url(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    memberships = await postgrest_get(
        settings, user.token, "organization_members", {"select": "org_id", "limit": "1"}
    )
    if not memberships:
        raise HTTPException(status_code=404, detail="No organization membership")
    state = github.make_state(settings, org_id=memberships[0]["org_id"], user_id=user.id)
    slug = settings_slug(settings)
    return {"url": f"https://github.com/apps/{slug}/installations/new?state={state}"}


@router.post("/installations/{installation_id}/disconnect")
async def disconnect(
    installation_id: int,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    rows = await postgrest_get(
        settings,
        user.token,
        "github_installations",
        {"select": "id", "installation_id": f"eq.{installation_id}"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Installation not found")

    try:
        await github.uninstall(settings, installation_id)
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)

    await postgrest_delete(
        settings,
        user.token,
        "github_installations",
        {"installation_id": f"eq.{installation_id}"},
    )
    return {"ok": True}


@router.get("/repos")
async def repos(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    installations = await postgrest_get(
        settings, user.token, "github_installations", {"select": "installation_id"}
    )
    all_repos: list[dict] = []
    for inst in installations:
        token = await github.mint_installation_token(settings, inst["installation_id"])
        for r in await github.list_installation_repos(token):
            all_repos.append(
                {"full_name": r["full_name"], "default_branch": r.get("default_branch", "main")}
            )
    return all_repos


async def _first_installation_token(settings: Settings, user_token: str) -> str:
    installations = await postgrest_get(
        settings,
        user_token,
        "github_installations",
        {"select": "installation_id", "limit": "1"},
    )
    if not installations:
        raise HTTPException(status_code=404, detail="No GitHub installation connected")
    return await github.mint_installation_token(settings, installations[0]["installation_id"])


@router.get("/repos/{owner}/{repo}/pulls")
async def repo_pulls(
    owner: str,
    repo: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    token = await _first_installation_token(settings, user.token)
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


@router.get("/repos/{owner}/{repo}/projects")
async def repo_projects(
    owner: str,
    repo: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    token = await _first_installation_token(settings, user.token)
    try:
        return await github.list_projects_v2(token, owner, repo)
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)
```

Save as `apps/api/app/routers/github.py`.

- [ ] **Step 4: Register the router in `main.py`**

Change:
```python
from .routers import auth, llm, projects, reviews, runner, tasks
```
to:
```python
from .routers import auth, github, llm, projects, reviews, runner, tasks
```
and after `app.include_router(projects.router, prefix="/api/v1")` add:
```python
app.include_router(github.router, prefix="/api/v1")
```

- [ ] **Step 5: Run to verify it passes**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_github.py -v
```
Expected: all 10 tests PASS.

- [ ] **Step 6: Run the full backend suite**

```bash
apps/api/.venv/Scripts/python -m pytest -v
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add apps/api/app/routers/github.py apps/api/app/main.py apps/api/tests/test_github.py
git commit -m "feat: add GitHub App connect/disconnect/repos/pulls/projects endpoints"
```

---

### Task 6: Merge flow swap (`reviews.py`)

**Files:**
- Modify: `apps/api/app/routers/reviews.py`
- Modify: `apps/api/tests/test_reviews.py`

**Interfaces:**
- Consumes: `github.parse_pr_url`, `github.merge_pull_request`, `github.mint_installation_token`, `github.GitHubError` (Task 3); `postgrest_get` (existing).
- Produces: `_merge_pr(settings, user_token, pr_url) -> str` (signature changes: adds `user_token`).

- [ ] **Step 1: Update `_merge_pr` and its call site**

In `apps/api/app/routers/reviews.py`, add the import:
```python
from .. import github
```
alongside the existing imports. Replace the whole `_merge_pr` function with:

```python
async def _merge_pr(settings: Settings, user_token: str, pr_url: str | None) -> str:
    """Merge the run's PR. Simulated PRs (and missing tokens) skip GitHub.
    Prefers the org's GitHub App installation token; falls back to the
    static GITHUB_TOKEN for orgs with no installation (US-1.19)."""
    if not pr_url or pr_url.startswith(SIMULATED_PREFIX):
        return "simulated"

    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    installations = await postgrest_get(
        settings, user_token, "github_installations", {"select": "installation_id", "limit": "1"}
    )
    if installations:
        token = await github.mint_installation_token(
            settings, installations[0]["installation_id"]
        )
    elif settings.github_token:
        token = settings.github_token
    else:
        raise HTTPException(
            status_code=409,
            detail="No GitHub App installation and GITHUB_TOKEN not configured — cannot merge a real PR",
        )

    try:
        await github.merge_pull_request(token, owner, repo, number)
    except github.GitHubError as e:
        raise HTTPException(status_code=409, detail=f"GitHub merge failed: {e.message}")
    return "merged"
```

Remove the now-unused `httpx` import if nothing else in the file uses it (check before removing — `httpx` was only used inside the old `_merge_pr`).

Update the call site in `approve()`:
```python
    merge_result = await _merge_pr(settings, user.token, runs[0]["pr_url"])
```

- [ ] **Step 2: Update existing tests for the new `postgrest_get` call**

`_merge_pr` now calls `postgrest_get` twice per merge attempt (once for `runs`, once for `github_installations`). Update `apps/api/tests/test_reviews.py`'s `fake_get` in the three tests that define one (`test_approve_simulated_pr`, `test_approve_unknown_run_is_404`, `test_approve_not_in_review_is_409`) to branch on `path`:

```python
async def fake_get(settings, token, path, params):
    if path == "runs":
        return [{"pr_url": "simulated://pr/health"}]
    if path == "github_installations":
        return []
    raise AssertionError(f"unexpected path {path}")
```
(adjust the `runs` return value per each test's existing intent — `test_approve_unknown_run_is_404` still returns `[]` for `runs`, `test_approve_not_in_review_is_409` still returns `[{"pr_url": "simulated://pr/x"}]` for `runs`). Since all three tests use simulated PR URLs, `_merge_pr` returns `"simulated"` before ever reaching the `github_installations` lookup for the 404/409 tests (those fail earlier), but `test_approve_simulated_pr`'s `fake_get` must handle both paths since simulated-URL short-circuit happens before any `postgrest_get` call at all — actually re-check: the simulated-prefix check happens first in `_merge_pr` (`if not pr_url or pr_url.startswith(SIMULATED_PREFIX): return "simulated"`), so `github_installations` is never queried for simulated URLs. The branching `fake_get` above is still correct and future-proof but not strictly required for these three tests to pass — add it anyway for clarity and to avoid `AssertionError` surprises if the simulated-URL fixture data changes later.

- [ ] **Step 3: Add a new test for the installation-token merge path**

Add to `apps/api/tests/test_reviews.py`:

```python
def test_approve_real_pr_uses_installation_token(client, make_token, monkeypatch):
    calls = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": "https://github.com/acme/webshop/pull/9"}]
        if path == "github_installations":
            return [{"installation_id": 42}]
        raise AssertionError(path)

    async def fake_mint(settings, installation_id):
        calls["installation_id"] = installation_id
        return "installation-token"

    async def fake_merge(token, owner, repo, number):
        calls["token"] = token
        calls["pr"] = (owner, repo, number)

    async def fake_rpc(settings, token, fn, args):
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fake_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["merge"] == "merged"
    assert calls["installation_id"] == 42
    assert calls["token"] == "installation-token"
    assert calls["pr"] == ("acme", "webshop", 9)


def test_approve_real_pr_falls_back_to_static_token(client, make_token, monkeypatch, settings_override):
    settings_override.github_token = "static-token"
    calls = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": "https://github.com/acme/webshop/pull/9"}]
        if path == "github_installations":
            return []
        raise AssertionError(path)

    async def fake_merge(token, owner, repo, number):
        calls["token"] = token

    async def fake_rpc(settings, token, fn, args):
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fake_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert calls["token"] == "static-token"


def test_approve_real_pr_no_installation_no_token_is_409(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": "https://github.com/acme/webshop/pull/9"}]
        if path == "github_installations":
            return []
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
```

- [ ] **Step 4: Run the reviews test file**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_reviews.py -v
```
Expected: all tests PASS (8 original + 3 new = 11... count follows from the file's actual final contents; confirm all green, none skipped).

- [ ] **Step 5: Run the full backend suite**

```bash
apps/api/.venv/Scripts/python -m pytest -v
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/reviews.py apps/api/tests/test_reviews.py
git commit -m "feat: merge flow prefers GitHub App installation token over static GITHUB_TOKEN"
```

---

### Task 7: Revert endpoint (`tasks.py`)

**Files:**
- Modify: `apps/api/app/routers/tasks.py`
- Modify: `apps/api/tests/test_dispatch.py` (rename intent unaffected — new tests appended for revert; file already covers the `tasks` router)

**Interfaces:**
- Consumes: `github.parse_pr_url`, `github.get_pull`, `github.revert_pull_request`, `github.mint_installation_token`, `github.GitHubError` (Task 3); `postgrest_get`, `postgrest_post` (Task 2).
- Produces: `POST /api/v1/tasks/{task_id}/revert -> {"revert_pr_url": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/api/tests/test_dispatch.py`:

```python
def _revert_get(rows_by_path):
    async def fake_get(settings, token, path, params):
        if path in rows_by_path:
            return rows_by_path[path]
        raise AssertionError(f"unexpected path {path}")

    return fake_get


def test_revert_happy_path(client, make_token, monkeypatch):
    posted = {}

    fake_get = _revert_get(
        {
            "tasks": [{"id": TASK_ID, "status": "merged", "title": "Add CSV export"}],
            "task_events": [
                {"org_id": "org-1", "payload": {"pr_url": "https://github.com/acme/webshop/pull/9"}}
            ],
            "github_installations": [{"installation_id": 42}],
        }
    )

    async def fake_mint(settings, installation_id):
        return "installation-token"

    async def fake_get_pull(token, owner, repo, number):
        return {"node_id": "PR_kwDOabc"}

    async def fake_revert(token, node_id, title):
        return "https://github.com/acme/webshop/pull/10"

    async def fake_post(settings, token, path, body):
        posted.update(path=path, body=body)
        return [body]

    monkeypatch.setattr("app.routers.tasks.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.tasks.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.tasks.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.tasks.github.get_pull", fake_get_pull)
    monkeypatch.setattr("app.routers.tasks.github.revert_pull_request", fake_revert)

    resp = client.post(
        f"/api/v1/tasks/{TASK_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"revert_pr_url": "https://github.com/acme/webshop/pull/10"}
    assert posted["path"] == "task_events"
    assert posted["body"]["type"] == "reverted"


def test_revert_not_merged_is_409(client, make_token, monkeypatch):
    fake_get = _revert_get({"tasks": [{"id": TASK_ID, "status": "in-review", "title": "x"}]})
    monkeypatch.setattr("app.routers.tasks.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/tasks/{TASK_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_revert_unknown_task_is_404(client, make_token, monkeypatch):
    fake_get = _revert_get({"tasks": []})
    monkeypatch.setattr("app.routers.tasks.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/tasks/{TASK_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_revert_simulated_pr_is_409(client, make_token, monkeypatch):
    fake_get = _revert_get(
        {
            "tasks": [{"id": TASK_ID, "status": "merged", "title": "x"}],
            "task_events": [{"org_id": "org-1", "payload": {"pr_url": "simulated://pr/health"}}],
        }
    )
    monkeypatch.setattr("app.routers.tasks.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/tasks/{TASK_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_revert_no_installation_is_409(client, make_token, monkeypatch):
    fake_get = _revert_get(
        {
            "tasks": [{"id": TASK_ID, "status": "merged", "title": "x"}],
            "task_events": [
                {"org_id": "org-1", "payload": {"pr_url": "https://github.com/acme/webshop/pull/9"}}
            ],
            "github_installations": [],
        }
    )
    monkeypatch.setattr("app.routers.tasks.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/tasks/{TASK_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run to verify it fails**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_dispatch.py -v -k revert
```
Expected: all 5 new tests FAIL (`404 Not Found` from the route not existing / `AttributeError` on `app.routers.tasks.github`).

- [ ] **Step 3: Implement the endpoint**

In `apps/api/app/routers/tasks.py`, update imports:
```python
from .. import github
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, postgrest_get, postgrest_post, rpc
```
Add after the existing `dispatch` endpoint:

```python
@router.post("/{task_id}/revert")
async def revert(
    task_id: UUID,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    tasks_rows = await postgrest_get(
        settings, user.token, "tasks", {"select": "id,status,title", "id": f"eq.{task_id}"}
    )
    if not tasks_rows:
        raise HTTPException(status_code=404, detail="Task not found")
    task = tasks_rows[0]
    if task["status"] != "merged":
        raise HTTPException(
            status_code=409, detail=f'task is not merged (status "{task["status"]}")'
        )

    events = await postgrest_get(
        settings,
        user.token,
        "task_events",
        {
            "select": "org_id,payload",
            "task_id": f"eq.{task_id}",
            "type": "eq.merged",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    if not events:
        raise HTTPException(status_code=409, detail="No merged PR recorded for this task")
    pr_url = events[0]["payload"].get("pr_url")
    org_id = events[0]["org_id"]
    if not pr_url or pr_url.startswith("simulated://"):
        raise HTTPException(status_code=409, detail="No real GitHub PR to revert (simulated)")

    try:
        owner, repo, number = github.parse_pr_url(pr_url)
    except github.GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))

    installations = await postgrest_get(
        settings, user.token, "github_installations", {"select": "installation_id", "limit": "1"}
    )
    if not installations:
        raise HTTPException(status_code=409, detail="No GitHub installation connected")
    token = await github.mint_installation_token(settings, installations[0]["installation_id"])

    try:
        pull = await github.get_pull(token, owner, repo, number)
        revert_url = await github.revert_pull_request(
            token, pull["node_id"], f'Revert "{task["title"]}"'
        )
    except github.GitHubError as e:
        raise HTTPException(status_code=502, detail=e.message)

    await postgrest_post(
        settings,
        user.token,
        "task_events",
        {
            "org_id": org_id,
            "task_id": str(task_id),
            "type": "reverted",
            "payload": {"revert_pr_url": revert_url},
        },
    )

    return {"revert_pr_url": revert_url}
```

- [ ] **Step 4: Run to verify it passes**

```bash
apps/api/.venv/Scripts/python -m pytest tests/test_dispatch.py -v -k revert
```
Expected: all 5 tests PASS.

- [ ] **Step 5: Run the full backend suite**

```bash
apps/api/.venv/Scripts/python -m pytest -v
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/api/app/routers/tasks.py apps/api/tests/test_dispatch.py
git commit -m "feat: add POST /tasks/{id}/revert (GraphQL revertPullRequest)"
```

---

### Task 8: Frontend — repo picker in the project dialog

**Files:**
- Modify: `apps/web/src/app/(app)/projects/project-dialog.tsx`

**Interfaces:**
- Consumes: `apiFetch` (`@/lib/api`); `Select`/`SelectContent`/`SelectItem`/`SelectTrigger`/`SelectValue` (`@/components/ui/select`).

- [ ] **Step 1: Replace the free-text repo `Input` with a fetched `Select`**

In `apps/web/src/app/(app)/projects/project-dialog.tsx`:

Add imports:
```typescript
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
```
(`useState` import already exists — merge into the single `"react"` import line rather than duplicating it; the same for any other pre-existing import from `"react"`.)

Remove `normalizeRepo` (no longer needed — the picker guarantees a valid `owner/repo` from the fetched list) and the `repo` free-text state; replace with:

```typescript
type GithubRepo = { full_name: string; default_branch: string };
```

Inside the component, add state and a fetch-on-open effect:

```typescript
  const [repos, setRepos] = useState<GithubRepo[] | null>(null);
  const [reposError, setReposError] = useState<string | null>(null);
  const [repoFullName, setRepoFullName] = useState(project?.repo_full_name ?? "");
  const [branch, setBranch] = useState(project?.default_branch ?? "main");

  useEffect(() => {
    if (!open) return;
    apiFetch("/api/v1/github/repos")
      .then((data: GithubRepo[]) => setRepos(data))
      .catch((e: Error) => setReposError(e.message));
  }, [open]);
```

(remove the old `const [repo, setRepo] = useState(...)` and `const [branch, setBranch] = useState(...)` declarations — `branch` stays, `repo` is replaced by `repoFullName` above; keep `open`/`setOpen` as already declared earlier in the component.)

Update `handleSave` to validate from `repoFullName` directly instead of calling `normalizeRepo`:

```typescript
  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError("Project name is required.");
      return;
    }
    if (!repoFullName) {
      setError("Select a repository.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const values = {
        name: name.trim(),
        description: description.trim() || null,
        repo_full_name: repoFullName,
        default_branch: branch.trim() || "main",
      };

      const { error: dbError } = isEdit
        ? await supabase.from("projects").update(values).eq("id", project.id)
        : await supabase.from("projects").insert({ ...values, org_id: orgId });

      if (dbError) {
        setError(dbError.message);
        return;
      }

      setOpen(false);
      if (!isEdit) {
        setName("");
        setDescription("");
        setRepoFullName("");
        setBranch("main");
      }
      router.refresh();
    } finally {
      setSaving(false);
    }
  }
```

Replace the repo `Input` field in the JSX with:

```tsx
          <div className="grid gap-2">
            <Label htmlFor="project-repo">GitHub repository</Label>
            {reposError ? (
              <p className="text-sm text-muted-foreground">
                Couldn&apos;t load repositories ({reposError}).{" "}
                <a href="/settings" className="underline underline-offset-4">
                  Check your GitHub connection
                </a>
                .
              </p>
            ) : repos && repos.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No repositories available.{" "}
                <a href="/settings" className="underline underline-offset-4">
                  Connect GitHub
                </a>{" "}
                and grant access to a repo first.
              </p>
            ) : (
              <Select
                items={(repos ?? []).map((r) => ({ value: r.full_name, label: r.full_name }))}
                value={repoFullName}
                onValueChange={(v) => {
                  if (typeof v !== "string") return;
                  setRepoFullName(v);
                  const match = repos?.find((r) => r.full_name === v);
                  if (match) setBranch(match.default_branch);
                }}
              >
                <SelectTrigger id="project-repo" className="w-full">
                  <SelectValue placeholder={repos ? "Select a repository" : "Loading…"} />
                </SelectTrigger>
                <SelectContent>
                  {(repos ?? []).map((r) => (
                    <SelectItem key={r.full_name} value={r.full_name}>
                      {r.full_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
```

- [ ] **Step 2: Typecheck**

```bash
npm run build
```
Expected: succeeds. Fix any leftover reference to the removed `repo`/`normalizeRepo` identifiers if the build flags them.

- [ ] **Step 3: Commit**

```bash
git add "apps/web/src/app/(app)/projects/project-dialog.tsx"
git commit -m "feat: replace free-text repo field with a fetched GitHub repo picker"
```

---

### Task 9: Frontend — Settings "GitHub" card

**Files:**
- Create: `apps/web/src/app/(app)/settings/github-settings.tsx`
- Modify: `apps/web/src/app/(app)/settings/page.tsx`

**Interfaces:**
- Consumes: `apiFetch` (`@/lib/api`); `Button`, `Card*`.
- Produces: `export function GithubSettings(props: { installations: { id: string; installation_id: number; account_login: string; account_type: string }[] }): JSX.Element`.

- [ ] **Step 1: Write `github-settings.tsx`**

```typescript
"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useRouter } from "next/navigation";
import { Github, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

export type GithubInstallation = {
  id: string;
  installation_id: number;
  account_login: string;
  account_type: string;
};

export function GithubSettings({
  installations,
}: {
  installations: GithubInstallation[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState(false);
  const [disconnectingId, setDisconnectingId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = searchParams.get("github");

  useEffect(() => {
    if (status) router.refresh();
    // Only re-run when the query param itself changes, not on every refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  async function handleConnect() {
    setError(null);
    setConnecting(true);
    try {
      const { url } = await apiFetch("/api/v1/github/connect-url");
      window.location.href = url;
    } catch (e) {
      setError((e as Error).message);
      setConnecting(false);
    }
  }

  async function handleDisconnect(installationId: number) {
    if (!confirm("Disconnect this GitHub installation? Projects linked to its repos will need to be reconnected.")) {
      return;
    }
    setError(null);
    setDisconnectingId(installationId);
    try {
      await apiFetch(`/api/v1/github/installations/${installationId}/disconnect`, {
        method: "POST",
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDisconnectingId(null);
    }
  }

  return (
    <div className="grid gap-3">
      {status === "connected" && (
        <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
          GitHub connected.
        </p>
      )}
      {status === "error" && (
        <p className="text-sm font-medium text-destructive">
          Could not complete the GitHub connection. Try again.
        </p>
      )}

      {installations.length === 0 ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            No GitHub account connected yet.
          </p>
          <Button onClick={handleConnect} disabled={connecting}>
            {connecting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Github className="size-4" />
            )}
            Connect GitHub
          </Button>
        </div>
      ) : (
        <ul className="grid gap-2">
          {installations.map((inst) => (
            <li
              key={inst.id}
              className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <span className="flex items-center gap-2">
                <Github className="size-4 text-muted-foreground" />
                {inst.account_login}
                <span className="text-xs text-muted-foreground">
                  ({inst.account_type})
                </span>
              </span>
              <Button
                variant="outline"
                size="sm"
                disabled={disconnectingId === inst.installation_id}
                onClick={() => handleDisconnect(inst.installation_id)}
              >
                {disconnectingId === inst.installation_id && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Disconnect
              </Button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Wire into `settings/page.tsx`**

Add imports:
```typescript
import { GithubSettings } from "./github-settings";
```
Fetch installations alongside the existing `llm_settings` query:
```typescript
  const { data: installations } = await supabase
    .from("github_installations")
    .select("id, installation_id, account_login, account_type")
    .eq("org_id", membership.org_id);
```
Add a new `Card` after the "LLM provider" card:
```tsx
      <Card>
        <CardHeader>
          <CardTitle>GitHub</CardTitle>
          <CardDescription>
            Connect the Software Factory GitHub App to link real repos and
            let the factory read/merge PRs on your org&apos;s behalf.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <GithubSettings installations={installations ?? []} />
        </CardContent>
      </Card>
```

- [ ] **Step 3: Typecheck**

```bash
npm run build
```
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add apps/web/src/app/\(app\)/settings/github-settings.tsx "apps/web/src/app/(app)/settings/page.tsx"
git commit -m "feat: add GitHub connect/disconnect settings card"
```

---

### Task 10: Frontend — GitHub tab on the project detail page

**Files:**
- Create: `apps/web/src/app/(app)/projects/[id]/github-tab.tsx`
- Modify: `apps/web/src/app/(app)/projects/[id]/page.tsx`

**Interfaces:**
- Consumes: `apiFetch`; `Tabs*` (already used by Overview/Guidelines tabs); `EmptyState`.
- Produces: `export function GithubTab(props: { repoFullName: string }): JSX.Element`.

- [ ] **Step 1: Write `github-tab.tsx`**

```typescript
"use client";

import { useEffect, useState } from "react";
import { ExternalLink, GitPullRequest, LayoutGrid, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { EmptyState } from "@/components/empty-state";

type PullRequest = {
  number: number;
  title: string;
  author: string;
  url: string;
  updated_at: string;
};

type ProjectV2 = { id: string; title: string; url: string };

export function GithubTab({ repoFullName }: { repoFullName: string }) {
  const [pulls, setPulls] = useState<PullRequest[] | null>(null);
  const [projects, setProjects] = useState<ProjectV2[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const [owner, repo] = repoFullName.split("/");
    Promise.all([
      apiFetch(`/api/v1/github/repos/${owner}/${repo}/pulls`),
      apiFetch(`/api/v1/github/repos/${owner}/${repo}/projects`),
    ])
      .then(([p, pr]) => {
        setPulls(p);
        setProjects(pr);
      })
      .catch((e: Error) => setError(e.message));
  }, [repoFullName]);

  if (error) {
    return (
      <p className="text-sm text-muted-foreground">
        Couldn&apos;t load GitHub data ({error}). Check your GitHub
        connection in Settings.
      </p>
    );
  }

  if (!pulls || !projects) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading…
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h3 className="mb-2 text-sm font-medium">Open pull requests</h3>
        {!pulls.length ? (
          <EmptyState icon={GitPullRequest} title="No open PRs" description="Nothing open right now." />
        ) : (
          <ul className="grid gap-1.5">
            {pulls.map((p) => (
              <li key={p.number}>
                <a
                  href={p.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                >
                  <span className="truncate">
                    #{p.number} {p.title}
                  </span>
                  <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                    {p.author}
                    <ExternalLink className="size-3" />
                  </span>
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-sm font-medium">Projects</h3>
        {!projects.length ? (
          <EmptyState icon={LayoutGrid} title="No linked projects" description="No GitHub Projects (v2) boards found for this repo." />
        ) : (
          <ul className="grid gap-1.5">
            {projects.map((proj) => (
              <li key={proj.id}>
                <a
                  href={proj.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                >
                  {proj.title}
                  <ExternalLink className="size-3 shrink-0" />
                </a>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wire into `projects/[id]/page.tsx`**

Add import:
```typescript
import { GithubTab } from "./github-tab";
```
Add a third `TabsTrigger` after "guidelines":
```tsx
          <TabsTrigger value="github">GitHub</TabsTrigger>
```
Add a matching `TabsContent` after the guidelines `TabsContent`:
```tsx
        <TabsContent value="github">
          <GithubTab repoFullName={project.repo_full_name} />
        </TabsContent>
```

- [ ] **Step 3: Typecheck**

```bash
npm run build
```
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add "apps/web/src/app/(app)/projects/[id]/github-tab.tsx" "apps/web/src/app/(app)/projects/[id]/page.tsx"
git commit -m "feat: add GitHub tab (open PRs + Projects v2) to project detail page"
```

---

### Task 11: Frontend — revert button on the task detail page

**Files:**
- Create: `apps/web/src/app/(app)/tasks/[id]/revert-button.tsx`
- Modify: `apps/web/src/app/(app)/tasks/[id]/page.tsx`

**Interfaces:**
- Consumes: `apiFetch`; `Dialog*`, `Button`.
- Produces: `export function RevertButton(props: { taskId: string }): JSX.Element`.

- [ ] **Step 1: Write `revert-button.tsx`**

```typescript
"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Undo2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

export function RevertButton({ taskId }: { taskId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRevert() {
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/tasks/${taskId}/revert`, { method: "POST" });
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" />}>
        <Undo2 className="size-4" />
        Revert
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revert this merge?</DialogTitle>
          <DialogDescription>
            Opens a new pull request that reverses the merged PR&apos;s
            changes — you still review and merge it separately.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleRevert} disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Open revert PR
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Wire into `tasks/[id]/page.tsx`**

Add import:
```typescript
import { RevertButton } from "./revert-button";
```
Add after the existing conditional "Review" button, before `<DispatchButton .../>`:
```tsx
          {task.status === "merged" && <RevertButton taskId={task.id} />}
```

- [ ] **Step 3: Typecheck**

```bash
npm run build
```
Expected: succeeds.

- [ ] **Step 4: Commit**

```bash
git add "apps/web/src/app/(app)/tasks/[id]/revert-button.tsx" "apps/web/src/app/(app)/tasks/[id]/page.tsx"
git commit -m "feat: add revert action to merged tasks"
```

---

### Task 12: Manual `.env` step, full verification, story bookkeeping

**Files:**
- Modify: `stories/us-1.19-github-app-connection.md`
- Modify: `stories/users.md`

**Interfaces:** none (documentation + operational note only).

- [ ] **Step 1: Hand the user the exact `.env` addition (manual — this session cannot edit `apps/api/.env*`)**

Report to the user, verbatim, that after registering the GitHub App per the design doc's bootstrap checklist (`docs/superpowers/specs/2026-07-13-github-app-connection-design.md`), they must add to `apps/api/.env`:
```
GITHUB_APP_ID=<app id from github>
GITHUB_APP_SLUG=<app slug from the github.com/settings/apps/<slug> URL>
GITHUB_APP_PRIVATE_KEY=<full .pem contents, with real newlines replaced by literal \n>
GITHUB_APP_STATE_SECRET=<openssl rand -hex 32>
WEB_BASE_URL=http://localhost:3000
```
This is a manual step — do not attempt to write `apps/api/.env` or `.env.example` directly; both are permission-blocked for this session, and the private key is a secret the user must generate themselves via GitHub's UI.

- [ ] **Step 2: Run the full backend suite one more time**

```bash
apps/api/.venv/Scripts/python -m pytest -v
```
Expected: all tests pass (this validates the whole feature's server-side logic even without real `.env` credentials, since every GitHub network call in tests is mocked).

- [ ] **Step 3: Run the frontend build one more time**

```bash
npm run build
```
Expected: succeeds with no type errors.

- [ ] **Step 4: Update the story's status line**

In `stories/us-1.19-github-app-connection.md`, change `**Status:** New` to `**Status:** Testing`.

- [ ] **Step 5: Update the index row**

In `stories/users.md`, change the us-1.19 row's status from `New` to `Testing`.

- [ ] **Step 6: Commit**

```bash
git add stories/us-1.19-github-app-connection.md stories/users.md
git commit -m "docs: move us-1.19 to Testing"
```

---

## Self-Review Notes

- **Spec coverage:** migration + RLS (Task 1); Settings GitHub card with connect/disconnect (Tasks 5, 9); `GET /github/repos` feeding the repo picker (Tasks 5, 8), which replaces the free-text field and validates access by construction (selecting from a fetched list); repo detail reads for PRs + Projects v2 (Tasks 5, 10); merge flow off the static token (Task 6); revert action via GraphQL (Tasks 7, 11); cross-org isolation via RLS on every user-facing call (Tasks 5–7, exercised by the `cross_org`-named tests); pytest coverage of install callback, repo listing, and merge/revert with GitHub mocked (Tasks 3, 5, 6, 7). Runner-side (US-1.15) wiring is explicitly deferred per the approved design — not built here.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code. The one manual step (Task 12, Step 1) is a real operational necessity (secret material this session cannot access), not a deferred implementation detail.
- **Type consistency:** `GithubRepo`/`GithubInstallation`/`PullRequest`/`ProjectV2` frontend types match the exact JSON shapes returned by Task 5's endpoints. `_merge_pr`'s new `user_token` parameter is threaded through its one call site in the same task. `app.routers.tasks.github`/`app.routers.github.github`/`app.routers.reviews.github` module-qualified monkeypatch targets match the `from .. import github` import style used consistently across Tasks 5–7.
- **Cross-org isolation:** every user-facing endpoint (`repos`, `repos/.../pulls`, `repos/.../projects`, `disconnect`, `revert`, the merge flow's installation lookup) reads through `postgrest_get` with the caller's own JWT — RLS makes cross-org rows structurally invisible, matching the pattern of every prior story's org-scoped table. Only the install callback bypasses RLS (no user JWT exists for that request), and it's trust-anchored by a signed, expiring, tamper-evident state token instead — covered by Task 3's dedicated state-token tests.
