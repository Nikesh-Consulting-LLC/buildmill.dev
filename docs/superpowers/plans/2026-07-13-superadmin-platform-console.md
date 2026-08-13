# Superadmin Platform Console (us-1.27) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the platform operator (member of the seeded "Nikesh Consulting LLC" org) a `/admin` area to manage every org and user on the platform: org CRUD, user edit/deactivate/reset-password, and cross-org membership linking.

**Architecture:** All admin actions go through a **single new FastAPI router** (`apps/api/app/routers/admin.py`), gated by one `require_platform_admin` dependency that does a live `is_platform_admin()` RPC check against the caller's own JWT. Once authorized, the router uses the Supabase **service-role key** to reach PostgREST directly (bypassing RLS — safe, since authorization already happened) for org/user/membership CRUD, and the Supabase Auth Admin API (`auth.admin.*`) for the operations that structurally require it (deactivate/reactivate a login, reset a password). This is the one deliberate, narrowly-scoped exception to "build less API" this story calls for — regular per-org RLS is untouched for everyone else, and no new cross-org RLS policies are added (the service-role key makes them unnecessary). Routing every admin action through one FastAPI router (rather than splitting some into client-side RLS calls) also gives the story's required "403 for every admin route" test coverage a single, consistent surface.

**Tech Stack:** FastAPI, httpx, Postgres (`security definer` function + seed migration), Next.js 16 App Router, Supabase JS SDK (for the `is_platform_admin()` read that gates the UI).

## Global Constraints

- Migration goes in `infra/supabase/migrations/`, next free number is **`015`** (after `014_org_membership.sql` from the [us-1.26 plan](2026-07-13-org-membership-management.md) — confirm via `list_migrations` before running, since this story depends on that one landing first). Apply via MCP `apply_migration`, then regenerate `apps/web/src/lib/supabase/database.types.ts` via MCP `generate_typescript_types`.
- **This story requires a real secret the implementer cannot obtain itself**: `SUPABASE_SERVICE_ROLE_KEY`. Supabase's MCP tools deliberately expose only publishable/anon keys, never the service-role key (CLAUDE.md: "Secrets are write-only"). Task 2 adds the config field and wires it through, but the actual value must be added to `apps/api/.env` by the human operator (from the Supabase dashboard → Project Settings → API → `service_role` secret) — the implementer for Task 2 must call this out explicitly as a manual step and use a placeholder-safe default (`""`) so the app doesn't crash locally when it's unset; endpoints that need it should fail loudly and clearly (not silently) if the key is empty.
- No test runner exists for `apps/web`; frontend verification is build + manual check. `apps/api` DOES have pytest, and this story explicitly requires it: **every admin route must have a test asserting a non-superadmin JWT gets 403**, following the existing `apps/api/tests/conftest.py` (`make_token`) pattern. Note: `conftest.py`'s default `make_token()` email is `kaushlesh@nikesh.llc` — the same email the migration seeds as the platform admin — so 403 tests must mint a token with a **different** email/sub (e.g. `make_token(sub="...", email="not-admin@example.com")`) and mock `is_platform_admin` to return `False` for that caller, since these tests won't have a live database to check against.
- shadcn here is Base UI: triggers use `render={<Button />}`, not `asChild`.
- No comments in code unless explaining a genuinely non-obvious constraint.
- Never let a service-role response leak back to the client without going through a route that already checked `require_platform_admin` — every new endpoint in `admin.py` must depend on it.

---

### Task 1: Migration — `is_platform_admin` column, seed org, `is_platform_admin()` function

**Files:**
- Create: `infra/supabase/migrations/015_platform_admin.sql`
- Modify (regenerate): `apps/web/src/lib/supabase/database.types.ts`

**Interfaces:**
- Produces: `public.organizations.is_platform_admin boolean not null default false`, a seeded `Nikesh Consulting LLC` org with `is_platform_admin = true` and an `owner` membership row for `kaushlesh@nikesh.llc` (if that account exists yet), and `public.is_platform_admin() returns boolean` (`security definer`, mirrors `is_org_member`).

- [ ] **Step 1: Write the migration**

```sql
-- 015_platform_admin: seeds the platform-admin org and the
-- is_platform_admin() check every admin capability gates on (US-1.27).
-- Deploy precondition: kaushlesh@nikesh.llc must already have an
-- auth.users account when this migration runs, or the seed membership
-- is skipped (with a warning) and must be added manually afterward.

alter table public.organizations add column is_platform_admin boolean not null default false;

create or replace function public.is_platform_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
  select exists (
    select 1
    from public.organization_members m
    join public.organizations o on o.id = m.org_id
    where m.user_id = (select auth.uid())
      and o.is_platform_admin = true
  );
$$;

revoke execute on function public.is_platform_admin() from public, anon;
grant execute on function public.is_platform_admin() to authenticated;

do $$
declare
  v_org_id uuid;
  v_user_id uuid;
begin
  select id into v_org_id
  from public.organizations
  where is_platform_admin = true
  limit 1;

  if v_org_id is null then
    insert into public.organizations (name, is_platform_admin)
    values ('Nikesh Consulting LLC', true)
    returning id into v_org_id;
  end if;

  select id into v_user_id from auth.users where lower(email) = lower('kaushlesh@nikesh.llc') limit 1;

  if v_user_id is null then
    raise warning 'kaushlesh@nikesh.llc has no auth.users account yet — platform admin org created with no members. Add them to organization_members manually (role=owner) once the account exists.';
  else
    insert into public.organization_members (org_id, user_id, role)
    values (v_org_id, v_user_id, 'owner')
    on conflict (org_id, user_id) do nothing;
  end if;
end $$;
```

- [ ] **Step 2: Apply the migration to the live Supabase project**

Use the Supabase MCP `apply_migration` tool, project id `wdudmfhhqxrqzoyhuzwx`, name `platform_admin`, with the SQL above. Before applying, run `list_migrations` to confirm `015` is free.

- [ ] **Step 3: Regenerate TypeScript types**

Use the Supabase MCP `generate_typescript_types` tool for project `wdudmfhhqxrqzoyhuzwx` and overwrite `apps/web/src/lib/supabase/database.types.ts`.

- [ ] **Step 4: Verify with direct SQL**

Use the Supabase MCP `execute_sql` tool:

```sql
select id, name, is_platform_admin from public.organizations where is_platform_admin = true;
-- expect: exactly one row, name = 'Nikesh Consulting LLC'

select m.role, u.email
from public.organization_members m
join auth.users u on u.id = m.user_id
join public.organizations o on o.id = m.org_id
where o.is_platform_admin = true;
-- expect: one row, role = 'owner', email = 'kaushlesh@nikesh.llc' (or zero rows
-- with a note if that account genuinely doesn't exist yet in this environment —
-- check auth.users directly to confirm which case this is)
```

- [ ] **Step 5: Commit**

```bash
git add infra/supabase/migrations/015_platform_admin.sql apps/web/src/lib/supabase/database.types.ts
git commit -m "feat: seed platform-admin org and add is_platform_admin() check"
```

---

### Task 2: FastAPI — service-role helpers, admin auth module, org endpoints

**Files:**
- Modify: `apps/api/app/config.py`
- Modify: `apps/api/app/supabase.py`
- Create: `apps/api/app/admin_auth.py`
- Create: `apps/api/app/routers/admin.py`
- Modify: `apps/api/app/main.py`
- Create: `apps/api/tests/test_admin_orgs.py`

**Interfaces:**
- Produces: `Settings.supabase_service_role_key: str` (default `""`); `supabase.PostgrestError(Exception)`, `supabase.admin_get/admin_post/admin_patch/admin_delete(settings, path, ...)`; `admin_auth.AdminAuthError(Exception)`, `admin_auth.update_user/set_password/set_ban(settings, user_id, ...)`; `admin.router` with `require_platform_admin` dependency and org endpoints (`GET/POST /admin/orgs`, `PATCH /admin/orgs/{id}`, `POST /admin/orgs/{id}/archive`, `DELETE /admin/orgs/{id}`).
- Consumes: `is_platform_admin()` RPC (Task 1), existing `rpc()`/`RpcError` from `supabase.py`, `AuthUser`/`verify_token` from `auth.py`.

- [ ] **Step 1: Add the service-role key to `Settings`**

In `apps/api/app/config.py`, add one field to the `Settings` class (alongside the other optional-default fields like `runner_shared_secret`):

```python
supabase_service_role_key: str = ""  # US-1.27 admin console — never sent to the browser; set manually in apps/api/.env from the Supabase dashboard
```

- [ ] **Step 2: Add service-role PostgREST helpers to `supabase.py`**

Append to `apps/api/app/supabase.py` (after the existing `rpc()` function, keep everything above it unchanged):

```python
def _service_headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


class PostgrestError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _extract_message(resp: httpx.Response) -> str:
    try:
        return resp.json().get("message", resp.text)
    except ValueError:
        return resp.text


async def admin_get(settings: Settings, path: str, params: dict[str, str]) -> Any:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{settings.rest_url}/{path}", params=params, headers=_service_headers(settings)
        )
    if resp.status_code >= 400:
        raise PostgrestError(_extract_message(resp))
    return resp.json()


async def admin_post(settings: Settings, path: str, body: dict[str, Any]) -> Any:
    headers = _service_headers(settings)
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{settings.rest_url}/{path}", json=body, headers=headers)
    if resp.status_code >= 400:
        raise PostgrestError(_extract_message(resp))
    return resp.json()


async def admin_patch(
    settings: Settings, path: str, params: dict[str, str], body: dict[str, Any]
) -> Any:
    headers = _service_headers(settings)
    headers["Prefer"] = "return=representation"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.patch(
            f"{settings.rest_url}/{path}", params=params, json=body, headers=headers
        )
    if resp.status_code >= 400:
        raise PostgrestError(_extract_message(resp))
    return resp.json()


async def admin_delete(settings: Settings, path: str, params: dict[str, str]) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{settings.rest_url}/{path}", params=params, headers=_service_headers(settings)
        )
    if resp.status_code >= 400:
        raise PostgrestError(_extract_message(resp))
```

`httpx` and `Settings`/`Any` are already imported at the top of this file — no new imports needed.

- [ ] **Step 3: `admin_auth.py` — Supabase Auth Admin API calls**

```python
# apps/api/app/admin_auth.py
"""Supabase Auth Admin API calls (US-1.27) — service-role only.

These exist because RLS/RPCs structurally cannot manage auth identities
(email, password, ban status) — only the GoTrue admin API can, and it
requires the service-role key, which must never reach the browser.
"""

import httpx

from .config import Settings


class AdminAuthError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


async def update_user(settings: Settings, user_id: str, **fields) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.put(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            json=fields,
            headers=_headers(settings),
        )
    if resp.status_code >= 400:
        try:
            message = resp.json().get("msg", resp.text)
        except ValueError:
            message = resp.text
        raise AdminAuthError(message)
    return resp.json()


async def set_password(settings: Settings, user_id: str, password: str) -> None:
    await update_user(settings, user_id, password=password)


async def set_ban(settings: Settings, user_id: str, banned: bool) -> None:
    # GoTrue has no boolean "banned" field — "none" un-bans, a long
    # duration bans indefinitely (there's no literal "forever" value).
    await update_user(settings, user_id, ban_duration="876000h" if banned else "none")
```

- [ ] **Step 4: `admin.py` router — `require_platform_admin` + org endpoints**

```python
# apps/api/app/routers/admin.py
"""Platform admin console — org/user management (US-1.27).

Every route here depends on require_platform_admin, which does a live
is_platform_admin() RPC check against the caller's own JWT. Once
authorized, reads/writes use the service-role key to bypass RLS — safe
because authorization already happened above; this is the one
deliberate "build less API" exception this story calls for.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    PostgrestError,
    RpcError,
    admin_delete,
    admin_get,
    admin_patch,
    admin_post,
    rpc,
)

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_platform_admin(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    try:
        is_admin = await rpc(settings, user.token, "is_platform_admin", {})
    except RpcError:
        is_admin = False
    if not is_admin:
        raise HTTPException(status_code=403, detail="Not a platform admin")
    return user


@router.get("/orgs")
async def list_orgs(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await admin_get(
        settings,
        "organizations",
        {
            "select": "id,name,archived_at,is_platform_admin,created_at,organization_members(count)",
            "order": "created_at.asc",
        },
    )


class CreateOrgBody(BaseModel):
    name: str


@router.post("/orgs")
async def create_org(
    body: CreateOrgBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    rows = await admin_post(settings, "organizations", {"name": body.name.strip()})
    return rows[0]


class RenameOrgBody(BaseModel):
    name: str


@router.patch("/orgs/{org_id}")
async def rename_org(
    org_id: str,
    body: RenameOrgBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    rows = await admin_patch(
        settings, "organizations", {"id": f"eq.{org_id}"}, {"name": body.name.strip()}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")
    return rows[0]


class ArchiveOrgBody(BaseModel):
    archived: bool


@router.post("/orgs/{org_id}/archive")
async def archive_org(
    org_id: str,
    body: ArchiveOrgBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    value = datetime.now(timezone.utc).isoformat() if body.archived else None
    rows = await admin_patch(
        settings, "organizations", {"id": f"eq.{org_id}"}, {"archived_at": value}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")
    return rows[0]


@router.delete("/orgs/{org_id}")
async def delete_org(
    org_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    try:
        await admin_delete(settings, "organizations", {"id": f"eq.{org_id}"})
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return {"ok": True}
```

- [ ] **Step 5: Register the router in `main.py`**

In `apps/api/app/main.py`, add `admin` to the router import and registration:

```python
from .routers import admin, auth, github, llm, projects, reviews, runner, tasks
```

```python
app.include_router(admin.router, prefix="/api/v1")
```

(Add this line alongside the other `app.include_router(...)` calls, order doesn't matter.)

- [ ] **Step 6: pytest — 403 coverage for org routes + one happy path**

```python
# apps/api/tests/test_admin_orgs.py
"""Platform admin org endpoints (US-1.27)."""

import pytest

NON_ADMIN_EMAIL = "not-admin@example.com"


def _non_admin_auth(make_token):
    return {"Authorization": f"Bearer {make_token(email=NON_ADMIN_EMAIL)}"}


@pytest.fixture(autouse=True)
def deny_platform_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        assert fn == "is_platform_admin"
        return False

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("GET", "/api/v1/admin/orgs", None),
        ("POST", "/api/v1/admin/orgs", {"name": "x"}),
        ("PATCH", "/api/v1/admin/orgs/org-1", {"name": "x"}),
        ("POST", "/api/v1/admin/orgs/org-1/archive", {"archived": True}),
        ("DELETE", "/api/v1/admin/orgs/org-1", None),
    ],
)
def test_non_admin_gets_403(client, make_token, method, path, json):
    resp = client.request(method, path, json=json, headers=_non_admin_auth(make_token))
    assert resp.status_code == 403


def test_list_orgs_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    async def fake_admin_get(settings, path, params):
        assert path == "organizations"
        return [{"id": "org-1", "name": "Acme", "archived_at": None, "is_platform_admin": False}]

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)

    resp = client.get(
        "/api/v1/admin/orgs", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    assert resp.json() == [
        {"id": "org-1", "name": "Acme", "archived_at": None, "is_platform_admin": False}
    ]
```

- [ ] **Step 7: Run pytest**

Run: `cd apps/api && SUPABASE_URL=https://test.supabase.co SUPABASE_PUBLISHABLE_KEY=sb_publishable_test CORS_ORIGINS=http://localhost:3000 DATABASE_URL=postgresql://test RUNNER_SHARED_SECRET=test-secret python -m pytest tests/test_admin_orgs.py -v`
Expected: all tests pass (6 tests — 5 parametrized 403 cases + 1 happy path).

- [ ] **Step 8: Commit**

```bash
git add apps/api/app/config.py apps/api/app/supabase.py apps/api/app/admin_auth.py apps/api/app/routers/admin.py apps/api/app/main.py apps/api/tests/test_admin_orgs.py
git commit -m "feat: add platform-admin FastAPI router with org CRUD endpoints"
```

**IMPORTANT — manual follow-up for the human operator:** `apps/api/.env` needs `SUPABASE_SERVICE_ROLE_KEY=<value>` added manually (from the Supabase dashboard → Project Settings → API → `service_role` secret). Without it, every admin endpoint will fail with a 401/403 from PostgREST/GoTrue when actually exercised — this is expected and cannot be fixed by the implementer, since the key cannot be obtained through any tool available here. State this in the final report.

---

### Task 3: FastAPI — user endpoints (edit/deactivate/reset-password) + membership link/unlink

**Files:**
- Modify: `apps/api/app/routers/admin.py`
- Create: `apps/api/tests/test_admin_users.py`

**Interfaces:**
- Consumes: `require_platform_admin` (Task 2), `admin_auth.update_user/set_password/set_ban` (Task 2), `PostgrestError` (Task 2).
- Produces: `GET/PATCH /admin/users`, `POST /admin/users/{id}/deactivate`, `POST /admin/users/{id}/reset-password`, `POST/PATCH/DELETE /admin/memberships`.

- [ ] **Step 1: Add user + membership endpoints to `admin.py`**

Append to `apps/api/app/routers/admin.py` (add `.. import admin_auth` to the existing import block at the top, alongside the `..auth`/`..config`/`..supabase` imports):

```python
from .. import admin_auth
```

```python
@router.get("/users")
async def list_users(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await admin_get(
        settings,
        "profiles",
        {
            "select": "id,email,display_name,created_at,organization_members(org_id,role,organizations(name))",
            "order": "created_at.asc",
        },
    )


class EditUserBody(BaseModel):
    display_name: str | None = None
    email: str | None = None


@router.patch("/users/{user_id}")
async def edit_user(
    user_id: str,
    body: EditUserBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    patch_body: dict = {}
    if body.display_name is not None:
        patch_body["display_name"] = body.display_name
    if body.email is not None:
        try:
            await admin_auth.update_user(settings, user_id, email=body.email)
        except admin_auth.AdminAuthError as e:
            raise HTTPException(status_code=502, detail=e.message)
        patch_body["email"] = body.email

    if not patch_body:
        raise HTTPException(status_code=400, detail="Nothing to update")

    rows = await admin_patch(settings, "profiles", {"id": f"eq.{user_id}"}, patch_body)
    if not rows:
        raise HTTPException(status_code=404, detail="User not found")
    return rows[0]


class DeactivateBody(BaseModel):
    deactivated: bool


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: str,
    body: DeactivateBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    try:
        await admin_auth.set_ban(settings, user_id, body.deactivated)
    except admin_auth.AdminAuthError as e:
        raise HTTPException(status_code=502, detail=e.message)
    return {"ok": True}


class ResetPasswordBody(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    body: ResetPasswordBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    try:
        await admin_auth.set_password(settings, user_id, body.new_password)
    except admin_auth.AdminAuthError as e:
        raise HTTPException(status_code=502, detail=e.message)
    return {"ok": True}


class LinkMembershipBody(BaseModel):
    org_id: str
    user_id: str
    role: str = "member"


@router.post("/memberships")
async def link_membership(
    body: LinkMembershipBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if body.role not in ("owner", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    try:
        rows = await admin_post(
            settings,
            "organization_members",
            {"org_id": body.org_id, "user_id": body.user_id, "role": body.role},
        )
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return rows[0]


class UpdateMembershipRoleBody(BaseModel):
    org_id: str
    user_id: str
    role: str


@router.patch("/memberships")
async def update_membership_role(
    body: UpdateMembershipRoleBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if body.role not in ("owner", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")
    try:
        rows = await admin_patch(
            settings,
            "organization_members",
            {"org_id": f"eq.{body.org_id}", "user_id": f"eq.{body.user_id}"},
            {"role": body.role},
        )
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    if not rows:
        raise HTTPException(status_code=404, detail="Membership not found")
    return rows[0]


@router.delete("/memberships")
async def unlink_membership(
    org_id: str,
    user_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    try:
        await admin_delete(
            settings,
            "organization_members",
            {"org_id": f"eq.{org_id}", "user_id": f"eq.{user_id}"},
        )
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return {"ok": True}
```

Note: `unlink_membership`/`update_membership_role` hitting the [us-1.26](2026-07-13-org-membership-management.md) `guard_last_owner` trigger's last-owner exception will surface here as `PostgrestError` → `409` with the trigger's own message ("Cannot remove/demote the last remaining owner...") — this is what satisfies this story's AC that a superadmin can't remove the platform admin org's own last owner, since that trigger applies to every org including the platform-admin one.

- [ ] **Step 2: pytest — 403 coverage for user/membership routes + one happy path each for deactivate and reset-password**

```python
# apps/api/tests/test_admin_users.py
"""Platform admin user + membership endpoints (US-1.27)."""

import pytest

NON_ADMIN_EMAIL = "not-admin@example.com"


def _non_admin_auth(make_token):
    return {"Authorization": f"Bearer {make_token(email=NON_ADMIN_EMAIL)}"}


@pytest.fixture(autouse=True)
def deny_platform_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        assert fn == "is_platform_admin"
        return False

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("GET", "/api/v1/admin/users", None),
        ("PATCH", "/api/v1/admin/users/user-1", {"display_name": "x"}),
        ("POST", "/api/v1/admin/users/user-1/deactivate", {"deactivated": True}),
        ("POST", "/api/v1/admin/users/user-1/reset-password", {"new_password": "x" * 10}),
        ("POST", "/api/v1/admin/memberships", {"org_id": "o", "user_id": "u", "role": "member"}),
        ("PATCH", "/api/v1/admin/memberships", {"org_id": "o", "user_id": "u", "role": "owner"}),
        ("DELETE", "/api/v1/admin/memberships?org_id=o&user_id=u", None),
    ],
)
def test_non_admin_gets_403(client, make_token, method, path, json):
    resp = client.request(method, path, json=json, headers=_non_admin_auth(make_token))
    assert resp.status_code == 403


def test_deactivate_user_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    called = {}

    async def fake_set_ban(settings, user_id, banned):
        called["user_id"] = user_id
        called["banned"] = banned

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_auth.set_ban", fake_set_ban)

    resp = client.post(
        "/api/v1/admin/users/user-1/deactivate",
        json={"deactivated": True},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert called == {"user_id": "user-1", "banned": True}


def test_reset_password_rejects_short_password(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)

    resp = client.post(
        "/api/v1/admin/users/user-1/reset-password",
        json={"new_password": "short"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 3: Run pytest**

Run: `cd apps/api && SUPABASE_URL=https://test.supabase.co SUPABASE_PUBLISHABLE_KEY=sb_publishable_test CORS_ORIGINS=http://localhost:3000 DATABASE_URL=postgresql://test RUNNER_SHARED_SECRET=test-secret python -m pytest tests/test_admin_orgs.py tests/test_admin_users.py -v`
Expected: all tests pass (6 from Task 2 + 9 from this task).

- [ ] **Step 4: Commit**

```bash
git add apps/api/app/routers/admin.py apps/api/tests/test_admin_users.py
git commit -m "feat: add platform-admin user and membership-linking endpoints"
```

---

### Task 4: Frontend — sidebar Admin entry + `/admin/orgs`

**Files:**
- Modify: `apps/web/src/app/(app)/layout.tsx`
- Modify: `apps/web/src/components/app-sidebar.tsx`
- Create: `apps/web/src/app/(app)/admin/layout.tsx`
- Create: `apps/web/src/app/(app)/admin/orgs/page.tsx`

**Interfaces:**
- Consumes: `is_platform_admin()` RPC (Task 1), `/api/v1/admin/orgs*` endpoints (Task 2), `apiFetch` from `@/lib/api`.
- Produces: `AppSidebar({ isSuperadmin }: { isSuperadmin: boolean })`.

- [ ] **Step 1: `layout.tsx` — check superadmin status, pass to sidebar**

In `apps/web/src/app/(app)/layout.tsx`, after the existing `membership` query, add:

```tsx
const { data: isSuperadmin } = await supabase.rpc("is_platform_admin");
```

Update the `<AppSidebar />` call to `<AppSidebar isSuperadmin={isSuperadmin ?? false} />`.

- [ ] **Step 2: `app-sidebar.tsx` — conditional Admin entry**

```tsx
// apps/web/src/components/app-sidebar.tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Factory,
  FlaskConical,
  LayoutDashboard,
  FolderGit2,
  ListTodo,
  GitPullRequest,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/projects", label: "Projects", icon: FolderGit2 },
  { href: "/tasks", label: "Tasks", icon: ListTodo },
  { href: "/review", label: "Review", icon: GitPullRequest },
  { href: "/tests", label: "Tests", icon: FlaskConical },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function AppSidebar({ isSuperadmin }: { isSuperadmin: boolean }) {
  const pathname = usePathname();
  const items = isSuperadmin
    ? [...NAV_ITEMS, { href: "/admin", label: "Admin", icon: ShieldCheck }]
    : NAV_ITEMS;

  return (
    <aside className="hidden w-56 shrink-0 flex-col border-r bg-sidebar md:flex">
      <div className="flex h-14 items-center gap-2 border-b px-4">
        <div className="flex size-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <Factory className="size-4" />
        </div>
        <span className="text-sm font-semibold tracking-tight">
          Software Factory
        </span>
      </div>
      <nav className="flex flex-1 flex-col gap-1 p-3">
        {items.map(({ href, label, icon: Icon }) => {
          const active = pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground"
              )}
            >
              <Icon className="size-4" />
              {label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
```

- [ ] **Step 3: `/admin` layout guard**

```tsx
// apps/web/src/app/(app)/admin/layout.tsx
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export default async function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: isSuperadmin } = await supabase.rpc("is_platform_admin");
  if (!isSuperadmin) redirect("/dashboard");

  return <>{children}</>;
}
```

- [ ] **Step 4: `/admin/orgs` page**

```tsx
// apps/web/src/app/(app)/admin/orgs/page.tsx
"use client";

import { useEffect, useState } from "react";
import { Archive, ArchiveRestore, Loader2, Plus, Trash2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ConfirmDialog } from "@/components/confirm-dialog";

type AdminOrg = {
  id: string;
  name: string;
  archived_at: string | null;
  is_platform_admin: boolean;
  created_at: string;
  organization_members: { count: number }[];
};

export default function AdminOrgsPage() {
  const [orgs, setOrgs] = useState<AdminOrg[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);

  async function load() {
    try {
      const data = await apiFetch("/api/v1/admin/orgs");
      setOrgs(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setCreating(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/orgs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: newName.trim() }),
      });
      setNewName("");
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }

  async function handleArchiveToggle(org: AdminOrg) {
    setBusyId(org.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/orgs/${org.id}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !org.archived_at }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(orgId: string) {
    await apiFetch(`/api/v1/admin/orgs/${orgId}`, { method: "DELETE" });
    await load();
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Organizations</h1>
        <p className="text-sm text-muted-foreground">Every org on the platform.</p>
      </div>

      <form onSubmit={handleCreate} className="flex gap-2">
        <Input
          placeholder="New org name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <Button type="submit" disabled={creating}>
          {creating ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
          Create
        </Button>
      </form>

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {!orgs ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="grid gap-2">
          {orgs.map((org) => (
            <li
              key={org.id}
              className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
            >
              <span className="flex min-w-0 flex-col">
                <span className="truncate font-medium">
                  {org.name}
                  {org.is_platform_admin && (
                    <span className="ml-2 text-xs text-muted-foreground">(platform admin)</span>
                  )}
                  {org.archived_at && (
                    <span className="ml-2 text-xs text-muted-foreground">(archived)</span>
                  )}
                </span>
                <span className="text-xs text-muted-foreground">
                  {org.organization_members?.[0]?.count ?? 0} members
                </span>
              </span>
              <span className="flex shrink-0 items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyId === org.id}
                  onClick={() => handleArchiveToggle(org)}
                >
                  {busyId === org.id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : org.archived_at ? (
                    <ArchiveRestore className="size-4" />
                  ) : (
                    <Archive className="size-4" />
                  )}
                  {org.archived_at ? "Restore" : "Archive"}
                </Button>
                <ConfirmDialog
                  trigger={
                    <Button variant="outline" size="sm">
                      <Trash2 className="size-4" />
                      Delete
                    </Button>
                  }
                  title={`Delete "${org.name}"?`}
                  description="This permanently deletes the org and all of its projects, tasks, and members. This can't be undone."
                  confirmLabel="Delete org"
                  onConfirm={() => handleDelete(org.id)}
                />
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

`ConfirmDialog` already exists at `apps/web/src/components/confirm-dialog.tsx` from the earlier archive/delete work — reuse it, don't recreate it.

Add a Rename action too, using the same lightweight `prompt()`/`confirm()` pattern already used elsewhere in this codebase (e.g. `github-settings.tsx`'s `confirm()` for disconnect) rather than a full dialog — add this handler above the `return`:

```tsx
async function handleRename(org: AdminOrg) {
  const name = window.prompt("New organization name", org.name);
  if (!name || !name.trim() || name.trim() === org.name) return;
  setBusyId(org.id);
  setError(null);
  try {
    await apiFetch(`/api/v1/admin/orgs/${org.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    });
    await load();
  } catch (e) {
    setError((e as Error).message);
  } finally {
    setBusyId(null);
  }
}
```

And add a Rename button in the actions `<span>` for each org, before the Archive button:

```tsx
<Button variant="outline" size="sm" disabled={busyId === org.id} onClick={() => handleRename(org)}>
  Rename
</Button>
```

- [ ] **Step 5: Build check**

Run: `npm run build`. Note: this will fail to fully exercise `/admin/orgs` against a live backend without `SUPABASE_SERVICE_ROLE_KEY` set (see Task 2's note) — a clean TypeScript/Next build is what this step verifies, not live behavior.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/\(app\)/layout.tsx apps/web/src/components/app-sidebar.tsx apps/web/src/app/\(app\)/admin/layout.tsx apps/web/src/app/\(app\)/admin/orgs/page.tsx
git commit -m "feat: add superadmin sidebar entry and /admin/orgs console"
```

---

### Task 5: Frontend — `/admin/users`

**Files:**
- Create: `apps/web/src/app/(app)/admin/users/page.tsx`

**Interfaces:**
- Consumes: `/api/v1/admin/users*`, `/api/v1/admin/memberships*` (Task 3), `apiFetch`.

- [ ] **Step 1: `/admin/users` page**

```tsx
// apps/web/src/app/(app)/admin/users/page.tsx
"use client";

import { useEffect, useState } from "react";
import { KeyRound, Loader2, Ban, CheckCircle2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type AdminUser = {
  id: string;
  email: string;
  display_name: string | null;
  created_at: string;
  organization_members: {
    org_id: string;
    role: string;
    organizations: { name: string } | null;
  }[];
};

export default function AdminUsersPage() {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [passwordDrafts, setPasswordDrafts] = useState<Record<string, string>>({});

  async function load() {
    try {
      const data = await apiFetch("/api/v1/admin/users");
      setUsers(data);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDeactivateToggle(user: AdminUser, deactivated: boolean) {
    setBusyId(user.id);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/users/${user.id}/deactivate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deactivated }),
      });
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleResetPassword(userId: string) {
    const newPassword = passwordDrafts[userId];
    if (!newPassword || newPassword.length < 8) {
      setError("New password must be at least 8 characters.");
      return;
    }
    setBusyId(userId);
    setError(null);
    try {
      await apiFetch(`/api/v1/admin/users/${userId}/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ new_password: newPassword }),
      });
      setPasswordDrafts((prev) => ({ ...prev, [userId]: "" }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Users</h1>
        <p className="text-sm text-muted-foreground">Every user on the platform.</p>
      </div>

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {!users ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : (
        <ul className="grid gap-3">
          {users.map((u) => (
            <li key={u.id} className="rounded-md border p-3 text-sm">
              <div className="flex items-center justify-between gap-3">
                <span className="flex min-w-0 flex-col">
                  <span className="truncate font-medium">{u.display_name || u.email}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {u.email} ·{" "}
                    {u.organization_members
                      .map((m) => `${m.organizations?.name ?? m.org_id} (${m.role})`)
                      .join(", ") || "no orgs"}
                  </span>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyId === u.id}
                  onClick={() => handleDeactivateToggle(u, true)}
                >
                  {busyId === u.id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Ban className="size-4" />
                  )}
                  Deactivate
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyId === u.id}
                  onClick={() => handleDeactivateToggle(u, false)}
                >
                  {busyId === u.id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <CheckCircle2 className="size-4" />
                  )}
                  Reactivate
                </Button>
              </div>
              <div className="mt-2 flex items-center gap-2">
                <Input
                  type="password"
                  placeholder="New password"
                  value={passwordDrafts[u.id] ?? ""}
                  onChange={(e) =>
                    setPasswordDrafts((prev) => ({ ...prev, [u.id]: e.target.value }))
                  }
                  className="max-w-48"
                />
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busyId === u.id}
                  onClick={() => handleResetPassword(u.id)}
                >
                  <KeyRound className="size-4" />
                  Reset password
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

This task scopes to the list, deactivate/reactivate, and reset-password actions to keep the task reviewable. Editing display name/email and link/unlink-to-org UI (the endpoints already exist from Task 3) are built next, in Task 6.

- [ ] **Step 2: Build check**

Run: `npm run build`.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/app/\(app\)/admin/users/page.tsx
git commit -m "feat: add /admin/users console — list, deactivate, reset password"
```

---

### Task 6: Frontend — edit user (display name/email) + link/unlink org membership

**Files:**
- Create: `apps/web/src/app/(app)/admin/users/edit-user-dialog.tsx`
- Create: `apps/web/src/app/(app)/admin/users/membership-editor.tsx`
- Modify: `apps/web/src/app/(app)/admin/users/page.tsx`
- Modify: `apps/web/src/app/(app)/admin/orgs/page.tsx`

**Interfaces:**
- Consumes: `PATCH /api/v1/admin/users/{id}`, `POST/PATCH/DELETE /api/v1/admin/memberships`, `GET /api/v1/admin/orgs` (for the org picker in the membership editor).
- Produces: `EditUserDialog({ user, onSaved }: { user: AdminUser; onSaved: () => void })`, `MembershipEditor({ userId, memberships, onChanged }: { userId: string; memberships: AdminUser["organization_members"]; onChanged: () => void })`.

- [ ] **Step 1: `EditUserDialog`**

```tsx
// apps/web/src/app/(app)/admin/users/edit-user-dialog.tsx
"use client";

import { useState } from "react";
import { Loader2, Pencil } from "lucide-react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export function EditUserDialog({
  user,
  onSaved,
}: {
  user: { id: string; email: string; display_name: string | null };
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [email, setEmail] = useState(user.email);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSaving(true);
    try {
      await apiFetch(`/api/v1/admin/users/${user.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          display_name: displayName.trim() || null,
          email: email.trim() !== user.email ? email.trim() : undefined,
        }),
      });
      setOpen(false);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size="sm" />}>
        <Pencil className="size-4" />
        Edit
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit user</DialogTitle>
          <DialogDescription>
            Changing email updates their login identity via the Supabase Admin API.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="edit-display-name">Display name</Label>
            <Input
              id="edit-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="edit-email">Email</Label>
            <Input
              id="edit-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          {error && <p className="text-sm font-medium text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
```

`email: ... : undefined` above means an unchanged email is omitted from the PATCH body entirely — the backend's `edit_user` (Task 3) only calls the Admin API and includes `email` in the profiles patch when `body.email is not None`, so an omitted field must actually be absent from the JSON, not `null`. `JSON.stringify` drops `undefined` values, so this is correct as written.

- [ ] **Step 2: `MembershipEditor`**

```tsx
// apps/web/src/app/(app)/admin/users/membership-editor.tsx
"use client";

import { useEffect, useState } from "react";
import { Loader2, Link2, Unlink } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

type OrgOption = { id: string; name: string };
type Membership = { org_id: string; role: string; organizations: { name: string } | null };

export function MembershipEditor({
  userId,
  memberships,
  onChanged,
}: {
  userId: string;
  memberships: Membership[];
  onChanged: () => void;
}) {
  const [orgs, setOrgs] = useState<OrgOption[] | null>(null);
  const [selectedOrgId, setSelectedOrgId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/v1/admin/orgs")
      .then((data: OrgOption[]) => setOrgs(data))
      .catch((e: Error) => setError(e.message));
  }, []);

  async function handleLink() {
    if (!selectedOrgId) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch("/api/v1/admin/memberships", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: selectedOrgId, user_id: userId, role: "member" }),
      });
      setSelectedOrgId("");
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleRoleToggle(m: Membership) {
    setBusy(true);
    setError(null);
    try {
      const nextRole = m.role === "owner" ? "member" : "owner";
      await apiFetch("/api/v1/admin/memberships", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ org_id: m.org_id, user_id: userId, role: nextRole }),
      });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUnlink(orgId: string) {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(
        `/api/v1/admin/memberships?org_id=${encodeURIComponent(orgId)}&user_id=${encodeURIComponent(userId)}`,
        { method: "DELETE" }
      );
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const unlinkedOrgs = (orgs ?? []).filter(
    (o) => !memberships.some((m) => m.org_id === o.id)
  );

  return (
    <div className="mt-2 grid gap-2">
      <ul className="grid gap-1">
        {memberships.map((m) => (
          <li key={m.org_id} className="flex items-center justify-between gap-2 text-xs">
            <span>
              {m.organizations?.name ?? m.org_id} — {m.role}
            </span>
            <span className="flex gap-1">
              <Button
                variant="outline"
                size="sm"
                disabled={busy}
                onClick={() => handleRoleToggle(m)}
              >
                Make {m.role === "owner" ? "member" : "owner"}
              </Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => handleUnlink(m.org_id)}>
                <Unlink className="size-3" />
              </Button>
            </span>
          </li>
        ))}
      </ul>
      <div className="flex items-center gap-2">
        <Select
          items={unlinkedOrgs.map((o) => ({ value: o.id, label: o.name }))}
          value={selectedOrgId}
          onValueChange={(v) => typeof v === "string" && setSelectedOrgId(v)}
        >
          <SelectTrigger className="h-8 max-w-56 text-xs">
            <SelectValue placeholder="Link to org…" />
          </SelectTrigger>
          <SelectContent>
            {unlinkedOrgs.map((o) => (
              <SelectItem key={o.id} value={o.id}>
                {o.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button variant="outline" size="sm" disabled={busy || !selectedOrgId} onClick={handleLink}>
          {busy ? <Loader2 className="size-3 animate-spin" /> : <Link2 className="size-3" />}
          Link
        </Button>
      </div>
      {error && <p className="text-xs font-medium text-destructive">{error}</p>}
    </div>
  );
}
```

- [ ] **Step 3: Wire both into `admin/users/page.tsx`**

Import `EditUserDialog` and `MembershipEditor`. In the per-user `<li>` block (from Task 5), add `<EditUserDialog user={u} onSaved={load} />` next to the Deactivate/Reactivate buttons, and render `<MembershipEditor userId={u.id} memberships={u.organization_members} onChanged={load} />` below the password-reset row.

- [ ] **Step 4: Build check**

Run: `npm run build`.

- [ ] **Step 5: Manual verification**

As the seeded superadmin: edit a test user's display name and confirm it updates; link a test user to a second org and confirm it appears; toggle their role in that org; unlink them; attempt to unlink the platform admin org's sole owner from their own org and confirm the guard trigger's error surfaces as a clean inline message (not a raw 500/409 JSON dump).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/\(app\)/admin/users/edit-user-dialog.tsx apps/web/src/app/\(app\)/admin/users/membership-editor.tsx apps/web/src/app/\(app\)/admin/users/page.tsx
git commit -m "feat: add user edit and org membership linking to /admin/users"
```
