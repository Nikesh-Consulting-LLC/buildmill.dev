# apps/api/app/routers/admin.py
"""Platform admin console — org/user management (US-1.27).

Every route here depends on require_platform_admin, which does a live
is_platform_admin() RPC check against the caller's own JWT. Once
authorized, reads/writes use the service-role key to bypass RLS — safe
because authorization already happened above; this is the one
deliberate "build less API" exception this story calls for.
"""

import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .. import admin_auth
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    PostgrestError,
    RpcError,
    admin_delete,
    admin_get,
    admin_patch,
    admin_post,
    admin_rpc,
    admin_upsert,
    rpc,
)

router = APIRouter(prefix="/admin", tags=["admin"])


async def _reject_if_platform_admin_org(settings: Settings, org_id: str) -> None:
    # The seed migration creates exactly one platform-admin org and never
    # runs again — archiving or deleting it would permanently lock every
    # superadmin out of /admin, with no way back in.
    rows = await admin_get(
        settings, "organizations", {"select": "is_platform_admin", "id": f"eq.{org_id}", "limit": "1"}
    )
    if rows and rows[0].get("is_platform_admin"):
        raise HTTPException(status_code=400, detail="Cannot archive or delete the platform admin organization.")


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
    orgs = await admin_get(
        settings,
        "organizations",
        {
            "select": "id,name,shortname,archived_at,is_platform_admin,created_at,max_agents,organization_members(count)",
            "order": "created_at.asc",
        },
    )
    # Owner per org: the 'owner'-role membership resolved to its principal
    # (email/display_name). Done as a second read + merge because the count
    # embed above can't also carry a filtered to-one join.
    owner_rows = await admin_get(
        settings,
        "organization_members",
        {"select": "org_id,principals(email,display_name)", "role": "eq.owner"},
    )
    owner_by_org: dict[str, dict[str, Any]] = {}
    for row in owner_rows:
        principal = row.get("principals") or {}
        # Oldest membership wins if an org somehow has more than one owner.
        owner_by_org.setdefault(
            row["org_id"],
            {"email": principal.get("email"), "display_name": principal.get("display_name")},
        )
    # US-57.2: how many of each org's quota is already spent.
    agent_rows = await admin_get(
        settings,
        "organization_members",
        {"select": "org_id,principals!inner(kind)", "principals.kind": "eq.agent"},
    )
    agent_count_by_org: dict[str, int] = {}
    for row in agent_rows:
        agent_count_by_org[row["org_id"]] = agent_count_by_org.get(row["org_id"], 0) + 1
    for org in orgs:
        org["owner"] = owner_by_org.get(org["id"])
        org["agent_count"] = agent_count_by_org.get(org["id"], 0)
    return orgs


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


# Mirrors the DB check constraint from migration 042 (<=24 chars, lowercase
# alphanumeric with single interior hyphens).
_SHORTNAME_RE = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")


class UpdateOrgBody(BaseModel):
    name: str | None = None
    shortname: str | None = None
    max_agents: int | None = None


@router.patch("/orgs/{org_id}")
async def update_org(
    org_id: str,
    body: UpdateOrgBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    updates: dict[str, Any] = {}
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=400, detail="Name is required")
        updates["name"] = body.name.strip()
    if body.shortname is not None:
        slug = body.shortname.strip().lower()
        if not _SHORTNAME_RE.match(slug) or len(slug) > 24:
            raise HTTPException(
                status_code=400,
                detail="Slug must be 1–24 chars: lowercase letters, numbers, and single interior hyphens.",
            )
        updates["shortname"] = slug
    if body.max_agents is not None:
        # US-57.2: the DB check constraint (1–100000) is the hard backstop;
        # this is the friendlier 400 in front of it.
        if not (1 <= body.max_agents <= 100000):
            raise HTTPException(
                status_code=400, detail="Agent quota must be between 1 and 100000."
            )
        updates["max_agents"] = body.max_agents
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")
    try:
        rows = await admin_patch(
            settings, "organizations", {"id": f"eq.{org_id}"}, updates
        )
    except PostgrestError as e:
        # Most likely the system-wide unique-shortname constraint.
        raise HTTPException(status_code=409, detail=e.message)
    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")
    return rows[0]


class ArchiveOrgBody(BaseModel):
    archived: bool


@router.get("/orgs/{org_id}/members")
async def list_org_members(
    org_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """US-1.27 follow-up: the org list's member count had nowhere to drill
    into — an org showing hundreds of members (usually agent workers from
    load-testing) gave a superadmin no way to see who they actually are."""
    rows = await admin_get(
        settings,
        "organization_members",
        {
            "select": "role,created_at,principals(id,kind,email,display_name)",
            "org_id": f"eq.{org_id}",
            "order": "created_at.asc",
        },
    )
    return [
        {
            "principal_id": (row.get("principals") or {}).get("id"),
            "kind": (row.get("principals") or {}).get("kind"),
            "email": (row.get("principals") or {}).get("email"),
            "display_name": (row.get("principals") or {}).get("display_name"),
            "role": row["role"],
            "joined_at": row["created_at"],
        }
        for row in rows
    ]


@router.post("/orgs/{org_id}/archive")
async def archive_org(
    org_id: str,
    body: ArchiveOrgBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    await _reject_if_platform_admin_org(settings, org_id)
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
    force: bool = False,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    await _reject_if_platform_admin_org(settings, org_id)
    # US-2.16: name the blocking work items up front, mirroring the
    # project-delete pre-check, instead of surfacing the raw trigger error.
    # `force` skips this guard for a superadmin who deliberately wants to
    # nuke the org anyway — every table FKs org_id ON DELETE CASCADE, so the
    # delete below still takes projects, issues, runs, and members with it.
    if not force:
        active = await admin_get(
            settings,
            "issues",
            {
                "select": "title,status",
                "org_id": f"eq.{org_id}",
                "status": "in.(queued,running,planning)",
                "limit": "5",
            },
        )
        if active:
            names = ", ".join(f"“{i['title']}” ({i['status']})" for i in active)
            raise HTTPException(
                status_code=409,
                detail=(
                    "This organization has work in progress — cancel or let it "
                    f"finish before deleting: {names}."
                ),
            )
    try:
        if force:
            # Plain admin_delete still trips the queued/running guard trigger
            # (046_force_delete_issues.sql) on the cascaded issue deletes —
            # this RPC sets its transaction-local escape hatch first, in the
            # same transaction as the org delete. See migration 222: it's the
            # service-role counterpart of force_delete_issues, which requires
            # org membership the platform admin very often doesn't have.
            await admin_rpc(settings, "admin_force_delete_org", {"p_org_id": org_id})
        else:
            await admin_delete(settings, "organizations", {"id": f"eq.{org_id}"})
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return {"ok": True}


@router.get("/users")
async def list_users(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await admin_get(
        settings,
        "profiles",
        {
            "select": "id,email,display_name,created_at,approved_at,organization_members(org_id,role,organizations(name))",
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

    original_email: str | None = None
    if body.email is not None:
        existing = await admin_get(
            settings, "profiles", {"select": "email", "id": f"eq.{user_id}", "limit": "1"}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        original_email = existing[0]["email"]

        try:
            await admin_auth.update_user(settings, user_id, email=body.email)
        except admin_auth.AdminAuthError as e:
            raise HTTPException(status_code=502, detail=e.message)
        patch_body["email"] = body.email

    if not patch_body:
        raise HTTPException(status_code=400, detail="Nothing to update")

    # If the profiles patch fails after the auth email already changed,
    # revert the auth email so the two stores don't drift out of sync.
    try:
        rows = await admin_patch(settings, "profiles", {"id": f"eq.{user_id}"}, patch_body)
    except PostgrestError as e:
        if original_email is not None:
            try:
                await admin_auth.update_user(settings, user_id, email=original_email)
            except admin_auth.AdminAuthError:
                pass
        raise HTTPException(status_code=502, detail=f"Profile update failed after email change: {e.message}")

    if not rows:
        if original_email is not None:
            try:
                await admin_auth.update_user(settings, user_id, email=original_email)
            except admin_auth.AdminAuthError:
                pass
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


@router.post("/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """us-94.1: open the beta gate for a waiting account. The patch is
    filtered to still-pending rows, so a double-click or two admins racing
    can't rewrite who actually approved it or when."""
    rows = await admin_patch(
        settings,
        "profiles",
        {"id": f"eq.{user_id}", "approved_at": "is.null"},
        {
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": admin.id,
        },
    )
    if not rows:
        existing = await admin_get(
            settings, "profiles", {"select": "approved_at", "id": f"eq.{user_id}", "limit": "1"}
        )
        if not existing:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "already_approved": True}
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


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: str,
    force: bool = False,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """Hard-deletes the auth identity — cascades to profiles, principals,
    and every organization_members row for them (all FK ON DELETE CASCADE
    off auth.users/principals). `force` skips the active-work guard below,
    the same escape hatch delete_org offers."""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    if not force:
        principal = await admin_get(
            settings,
            "principals",
            {"select": "id", "auth_user_id": f"eq.{user_id}", "limit": "1"},
        )
        principal_id = principal[0]["id"] if principal else None
        if principal_id:
            active = await admin_get(
                settings,
                "issues",
                {
                    "select": "title,status",
                    "assignee_id": f"eq.{principal_id}",
                    "status": "in.(queued,running,planning)",
                    "limit": "5",
                },
            )
            if active:
                names = ", ".join(f"“{i['title']}” ({i['status']})" for i in active)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "This user has work in progress — cancel or let it "
                        f"finish before deleting: {names}."
                    ),
                )
    try:
        await admin_auth.delete_user(settings, user_id)
    except admin_auth.AdminAuthError as e:
        raise HTTPException(status_code=502, detail=e.message)
    return {"ok": True}


# US-9.2: the six-role set replaces owner|member.
ORG_ROLES = ("owner", "admin", "lead", "developer", "reviewer", "viewer")


async def _resolve_human_principal(settings: Settings, user_id: str) -> str:
    """US-9.1: memberships key off principal_id. Resolve the human principal for
    an auth user, creating one (from their profile) if it doesn't exist yet."""
    existing = await admin_get(
        settings,
        "principals",
        {"select": "id", "auth_user_id": f"eq.{user_id}", "limit": "1"},
    )
    if existing:
        return existing[0]["id"]
    profile = await admin_get(
        settings,
        "profiles",
        {"select": "email,display_name", "id": f"eq.{user_id}", "limit": "1"},
    )
    email = profile[0]["email"] if profile else None
    display = (profile[0].get("display_name") if profile else None) or (
        email.split("@")[0] if email else None
    )
    created = await admin_post(
        settings,
        "principals",
        {
            "kind": "human",
            "email": email,
            "display_name": display,
            "auth_user_id": user_id,
        },
    )
    return created[0]["id"]


class LinkMembershipBody(BaseModel):
    org_id: str
    user_id: str
    role: str = "developer"


@router.post("/memberships")
async def link_membership(
    body: LinkMembershipBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    if body.role not in ORG_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")
    try:
        principal_id = await _resolve_human_principal(settings, body.user_id)
        rows = await admin_post(
            settings,
            "organization_members",
            {
                "org_id": body.org_id,
                "principal_id": principal_id,
                "user_id": body.user_id,
                "role": body.role,
            },
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
    if body.role not in ORG_ROLES:
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


# ------------------------------------------- role capability matrix (US-9.3)
# The role->capability grid is data (public.role_capabilities, US-9.2) and the
# Superadmin owns it. This service-role path is the ONLY writer; RLS grants no
# client write. Guardrails are re-enforced here (not just disabled in the UI):
# `owner` always keeps manage_org, and `view` stays true for every role.

CAPABILITY_KEYS = (
    "manage_org",
    "manage_members",
    "manage_project",
    "manage_work",
    "review_work",
    "develop",
    "view",
    # us-95.1: the Costs section's door key. Gates the section, not the ledger.
    "view_costs",
)

# The shipped default matrix (mirrors migrations 087 + 254) — used by "reset".
DEFAULT_ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "owner": CAPABILITY_KEYS,
    "admin": ("manage_members", "manage_project", "manage_work", "review_work", "develop", "view", "view_costs"),
    "lead": ("manage_work", "review_work", "develop", "view"),
    "developer": ("develop", "view"),
    "reviewer": ("review_work", "view"),
    "viewer": ("view",),
}


class RoleCapabilityCell(BaseModel):
    role: str
    capability: str
    allowed: bool


class RoleCapabilitiesBody(BaseModel):
    matrix: list[RoleCapabilityCell]


def _guarded_rows(cells: list[RoleCapabilityCell]) -> list[dict]:
    grid: dict[tuple[str, str], bool] = {}
    for c in cells:
        if c.role not in ORG_ROLES or c.capability not in CAPABILITY_KEYS:
            raise HTTPException(status_code=400, detail=f"Unknown role/capability: {c.role}/{c.capability}")
        grid[(c.role, c.capability)] = c.allowed
    # Re-enforce guardrails server-side.
    grid[("owner", "manage_org")] = True
    for role in ORG_ROLES:
        grid[(role, "view")] = True
    return [
        {"role": role, "capability": cap, "allowed": allowed}
        for (role, cap), allowed in grid.items()
    ]


async def _write_matrix(settings: Settings, rows: list[dict]) -> list[dict]:
    try:
        return await admin_upsert(settings, "role_capabilities", rows, "role,capability")
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)


@router.put("/role-capabilities")
async def set_role_capabilities(
    body: RoleCapabilitiesBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await _write_matrix(settings, _guarded_rows(body.matrix))


@router.post("/role-capabilities/reset")
async def reset_role_capabilities(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    rows = [
        {"role": role, "capability": cap, "allowed": cap in DEFAULT_ROLE_CAPABILITIES[role]}
        for role in ORG_ROLES
        for cap in CAPABILITY_KEYS
    ]
    return await _write_matrix(settings, rows)


# ------------------------------------------------ prompt templates (US-5.17)
# Every PLATFORM-GENERIC template the factory serves — a thinking-function
# prompt that isn't about a project's own conventions. Phase 67 (us-67.2)
# moved worker-instruction and guideline-section defaults, plus the three
# project-shaped thinking prompts (story_breakdown, test_case_elaborate,
# deploy_script_generate), to /admin/project-templates — editing the Default
# project template now covers those. The registry (keys, labels, variables)
# and all context composition stay code-owned; only template TEXT is data.

_PROJECT_TEMPLATE_OWNED_FUNCTIONS = {
    "story_breakdown", "test_case_elaborate", "deploy_script_generate",
}


def _template_catalog(settings: Settings) -> list[dict]:
    from .. import help_content, llm

    items: list[dict] = []
    for key, entry in llm.LLM_FUNCTIONS.items():
        if entry.get("template") is None:
            continue  # prd_draft: pool-dispatched, its text is the us-5.14 template
        if key in _PROJECT_TEMPLATE_OWNED_FUNCTIONS:
            continue  # now edited on the Default project template (us-67.2)
        items.append(
            {
                "key": key,
                "group": "thinking",
                "label": entry["label"],
                "description": entry["description"],
                "variables": entry.get("variables", []),
                "default": entry["template"],
            }
        )
    # US-2.30: help-page text units — defaults live in help_content.py.
    items.extend(help_content.help_catalog())
    return items


@router.get("/prompt-templates")
async def list_prompt_templates(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    overrides = {r["prompt_key"]: r for r in db.list_prompt_overrides(settings)}
    editor_ids = {
        str(r["updated_by"]) for r in overrides.values() if r.get("updated_by")
    }
    editors: dict[str, str] = {}
    if editor_ids:
        rows = await admin_get(
            settings,
            "profiles",
            {"select": "id,email,display_name", "id": f"in.({','.join(editor_ids)})"},
        )
        editors = {
            str(p["id"]): p.get("display_name") or p.get("email") or "admin"
            for p in rows
        }

    out = []
    for item in _template_catalog(settings):
        row = overrides.get(item["key"])
        out.append(
            {
                **item,
                "override": (
                    {
                        "content": row["content"],
                        "updated_at": str(row["updated_at"]),
                        "updated_by": editors.get(str(row.get("updated_by") or "")),
                    }
                    if row and row["content"].strip()
                    else None
                ),
            }
        )
    return out


class TemplateBody(BaseModel):
    content: str


@router.put("/prompt-templates/{key:path}")
async def upsert_prompt_template(
    key: str,
    body: TemplateBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db, llm

    known = {item["key"]: item for item in _template_catalog(settings)}
    if key not in known:
        raise HTTPException(status_code=404, detail="unknown template key")
    if not body.content.strip():
        raise HTTPException(
            status_code=422,
            detail="content is required — use reset to return to the factory default",
        )
    # Placeholder validation applies to thinking prompts only: worker and
    # guideline templates take no variables and are copied verbatim, so
    # braces in their markdown are fine.
    if known[key]["group"] == "thinking":
        unknown = llm.extract_placeholders(body.content) - set(known[key]["variables"])
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    "unknown placeholders: "
                    + ", ".join(f"{{{v}}}" for v in sorted(unknown))
                    + " — allowed: "
                    + (
                        ", ".join(f"{{{v}}}" for v in known[key]["variables"])
                        or "(none)"
                    )
                ),
            )
    row = db.upsert_prompt_override(settings, key, body.content, admin.id)
    return {
        "key": key,
        "override": {
            "content": row["content"],
            "updated_at": str(row["updated_at"]),
            "updated_by": None,
        },
    }


@router.delete("/prompt-templates/{key:path}")
async def reset_prompt_template(
    key: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    db.delete_prompt_override(settings, key)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Phase 67 (us-67.2): project templates — the superadmin-authored bundle a
# project silently inherits a copy of at creation (us-67.1). Plain PostgREST
# CRUD via the service role (same pattern as orgs/users above), gated on
# require_platform_admin like every other /admin/* route.
# ---------------------------------------------------------------------------

# us-100.4: a template holds the Agent Instructions document (a column,
# migration 265) and the per-task instructions — the files a project will
# publish. `guideline` sections became the document (us-100.1) and `prompt`
# sections are platform-global LLM prompts that no agent reads (they live at
# /admin/prompt-templates); neither may be written any more. Existing rows of
# both types stay in the database as a rollback (migration 265 deletes
# nothing) — this guard narrows what can be WRITTEN, not what exists.
_TEMPLATE_SECTION_TYPES = ("worker_instruction",)
_RETIRED_SECTION_TYPES = {
    "guideline": "guideline sections became the template's agent_instructions document (us-100.1)",
    "prompt": "prompt sections retired from templates; platform prompts live at /admin/prompt-templates (us-100.4)",
}


def _reject_retired_section_type(section_type: str) -> None:
    if section_type in _TEMPLATE_SECTION_TYPES:
        return
    reason = _RETIRED_SECTION_TYPES.get(section_type)
    if reason:
        raise HTTPException(status_code=422, detail=f"retired section_type: {reason}")
    raise HTTPException(status_code=422, detail=f"unknown section_type: {section_type}")


@router.get("/project-templates")
async def list_project_templates(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    templates = await admin_get(
        settings,
        "project_templates",
        {"select": "*", "order": "sort_order,name"},
    )
    # file_count: how many of the template's files carry content — the
    # document counts as one, and only worker_instruction sections are files.
    sections = await admin_get(
        settings,
        "project_template_sections",
        {
            "select": "template_id",
            "section_type": "eq.worker_instruction",
            "content": "neq.",
        },
    )
    counts: dict[str, int] = {}
    for s in sections:
        counts[s["template_id"]] = counts.get(s["template_id"], 0) + 1
    return [
        {
            **t,
            "file_count": counts.get(t["id"], 0)
            + (1 if (t.get("agent_instructions") or "").strip() else 0),
        }
        for t in templates
    ]


class ProjectTemplateBody(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    category: str = Field(default="", max_length=100)


@router.post("/project-templates")
async def create_project_template(
    body: ProjectTemplateBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    try:
        rows = await admin_post(
            settings,
            "project_templates",
            {
                "key": body.key.strip(),
                "name": body.name.strip(),
                "description": body.description,
                "category": body.category,
            },
        )
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    return rows[0]


class ProjectTemplatePatchBody(BaseModel):
    key: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    category: str | None = Field(default=None, max_length=100)
    is_default: bool | None = None
    is_disabled: bool | None = None
    sort_order: int | None = None
    # us-100.4: the AGENTS.md body a project created from this template
    # starts with. Same ceiling as a project's own document.
    agent_instructions: str | None = Field(default=None, max_length=200000)


@router.patch("/project-templates/{template_id}")
async def patch_project_template(
    template_id: str,
    body: ProjectTemplatePatchBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items()}
    if not patch:
        raise HTTPException(status_code=422, detail="nothing to update")
    if patch.get("name") is not None:
        patch["name"] = patch["name"].strip()
    if patch.get("key") is not None:
        patch["key"] = patch["key"].strip()
        if not patch["key"]:
            raise HTTPException(status_code=422, detail="key cannot be blank")

    existing = await admin_get(
        settings, "project_templates", {"id": f"eq.{template_id}", "select": "is_default,is_disabled"}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="template not found")
    current = existing[0]

    # The default template can never be disabled — every project without an
    # explicit template silently falls back to it (us-67.1).
    will_be_default = patch.get("is_default", current["is_default"])
    will_be_disabled = patch.get("is_disabled", current["is_disabled"])
    if will_be_default and will_be_disabled:
        raise HTTPException(
            status_code=409, detail="the default template cannot be disabled"
        )

    # Exactly one default template — clear the flag on every other row first
    # (the partial unique index refuses two trues in the same statement).
    if patch.get("is_default") is True:
        await admin_patch(
            settings,
            "project_templates",
            {"id": f"neq.{template_id}", "is_default": "eq.true"},
            {"is_default": False},
        )
    try:
        rows = await admin_patch(
            settings, "project_templates", {"id": f"eq.{template_id}"}, patch
        )
    except PostgrestError as e:
        raise HTTPException(status_code=409, detail=e.message)
    if not rows:
        raise HTTPException(status_code=404, detail="template not found")
    return rows[0]


@router.post("/project-templates/{template_id}/duplicate")
async def duplicate_project_template(
    template_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    existing = await admin_get(
        settings, "project_templates", {"id": f"eq.{template_id}", "select": "*"}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="template not found")
    source = existing[0]

    all_keys = {
        t["key"]
        for t in await admin_get(settings, "project_templates", {"select": "key"})
    }
    base_key = f"{source['key']}-copy"
    new_key = base_key
    n = 2
    while new_key in all_keys:
        new_key = f"{base_key}-{n}"
        n += 1

    created = await admin_post(
        settings,
        "project_templates",
        {
            "key": new_key,
            "name": f"{source['name']} (copy)",
            "description": source["description"],
            "category": source["category"],
            # us-100.4: the document is part of what a duplicate carries.
            "agent_instructions": source.get("agent_instructions") or "",
        },
    )
    new_template = created[0]

    # Only the files: retired guideline/prompt rows are rollback data on the
    # source, not content a new template should inherit.
    sections = await admin_get(
        settings,
        "project_template_sections",
        {
            "template_id": f"eq.{template_id}",
            "section_type": "eq.worker_instruction",
            "select": "section_type,section_key,title,content,sort_order",
        },
    )
    if sections:
        await admin_post(
            settings,
            "project_template_sections",
            [{**s, "template_id": new_template["id"]} for s in sections],
        )

    return new_template


@router.delete("/project-templates/{template_id}")
async def delete_project_template(
    template_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    existing = await admin_get(
        settings, "project_templates", {"id": f"eq.{template_id}", "select": "is_default"}
    )
    if existing and existing[0]["is_default"]:
        raise HTTPException(
            status_code=409, detail="cannot delete the default template"
        )
    await admin_delete(settings, "project_templates", {"id": f"eq.{template_id}"})
    return {"ok": True}


@router.get("/project-templates/{template_id}/sections")
async def list_project_template_sections(
    template_id: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await admin_get(
        settings,
        "project_template_sections",
        {
            "template_id": f"eq.{template_id}",
            "select": "*",
            "order": "section_type,sort_order",
        },
    )


class ProjectTemplateSectionBody(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=20000)
    sort_order: int = Field(default=0)


@router.put("/project-templates/{template_id}/sections/{section_type}/{section_key:path}")
async def upsert_project_template_section(
    template_id: str,
    section_type: str,
    section_key: str,
    body: ProjectTemplateSectionBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    _reject_retired_section_type(section_type)
    rows = await admin_upsert(
        settings,
        "project_template_sections",
        [
            {
                "template_id": template_id,
                "section_type": section_type,
                "section_key": section_key,
                "title": body.title,
                "content": body.content,
                "sort_order": body.sort_order,
            }
        ],
        "template_id,section_type,section_key",
    )
    return rows[0]


@router.delete("/project-templates/{template_id}/sections/{section_type}/{section_key:path}")
async def delete_project_template_section(
    template_id: str,
    section_type: str,
    section_key: str,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    await admin_delete(
        settings,
        "project_template_sections",
        {
            "template_id": f"eq.{template_id}",
            "section_type": f"eq.{section_type}",
            "section_key": f"eq.{section_key}",
        },
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# US-57.6: how every agent runs, and which modules exist to choose from
# ---------------------------------------------------------------------------


@router.get("/run-config")
async def get_platform_run_config(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    rows = await admin_get(settings, "platform_run_config", {"limit": "1"})
    return rows[0]


class PlatformRunConfigBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_routes: dict[str, Any] | None = None
    run_routes: dict[str, Any] | None = None
    autonomy_policy: dict[str, Any] | None = None
    max_run_minutes: int | None = None
    max_total_run_minutes: int | None = None
    max_item_attempts: int | None = None


@router.put("/run-config")
async def set_platform_run_config(
    body: PlatformRunConfigBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """One row, cascaded to every agent's runner_config by migration 204's
    trigger the instant this commits — no per-agent write, ever."""
    from .runner_socket import _validate_run_routes, POLICY_MODES

    updates: dict[str, Any] = {}
    if body.model_routes is not None:
        for k, v in body.model_routes.items():
            if v is not None and not isinstance(v, str):
                raise HTTPException(
                    status_code=422, detail=f"model route '{k}' must be a model name string"
                )
        updates["model_routes"] = body.model_routes
    if body.run_routes is not None:
        _validate_run_routes(body.run_routes)
        updates["run_routes"] = body.run_routes
    if body.autonomy_policy is not None:
        pol = body.autonomy_policy
        mode = pol.get("mode", "allow")
        if mode not in POLICY_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"autonomy mode '{mode}' is not one of: " + ", ".join(POLICY_MODES),
            )
        for key in ("deny_patterns", "allow_patterns"):
            for pat in pol.get(key) or []:
                try:
                    re.compile(str(pat))
                except re.error as e:
                    raise HTTPException(
                        status_code=422, detail=f"invalid regex in {key}: '{pat}' ({e})"
                    )
        updates["autonomy_policy"] = body.autonomy_policy
    for field in ("max_run_minutes", "max_total_run_minutes"):
        value = getattr(body, field)
        if value is not None and not (1 <= value <= 1440):
            raise HTTPException(
                status_code=422, detail=f"{field} must be between 1 and 1440 minutes, or null"
            )
        if field in body.model_fields_set:
            updates[field] = value
    if body.max_item_attempts is not None:
        if not (1 <= body.max_item_attempts <= 20):
            raise HTTPException(status_code=422, detail="max_item_attempts must be between 1 and 20")
        updates["max_item_attempts"] = body.max_item_attempts
    if not updates:
        raise HTTPException(status_code=400, detail="Nothing to update")
    rows = await admin_patch(settings, "platform_run_config", {"id": "eq.true"}, updates)
    return rows[0]


@router.get("/modules")
async def list_agent_modules(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return await admin_get(settings, "agent_modules", {"order": "key.asc"})


class ModuleAvailabilityBody(BaseModel):
    available: bool


@router.patch("/modules/{key}")
async def set_module_availability(
    key: str,
    body: ModuleAvailabilityBody,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """Hiding a module gates new creation only (agent_pools/agent_servers
    validate against this catalog) — an agent already on it keeps running."""
    rows = await admin_patch(
        settings, "agent_modules", {"key": f"eq.{key}"}, {"available": body.available}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Unknown module")
    return rows[0]


# ---------------------------------------------------------------------------
# US-60.2: usage across every org — the superadmin's own view of what
# `db.spend_breakdown` already computes for one org's own Spend page.
# ---------------------------------------------------------------------------


@router.get("/usage")
async def platform_usage(
    group_by: str = "org",
    days: int = 30,
    org_id: str | None = None,
    project_id: str | None = None,
    worker_id: str | None = None,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """The same grain and dimensions `/llm/orgs/{org_id}/spend` offers one
    org, minus the org filter — the only caller allowed to omit it, since
    `require_platform_admin` (not `is_org_member`) gates this route."""
    from .. import db

    return db.spend_breakdown(
        settings,
        org_id,
        group_by=group_by,
        days=days,
        project_id=project_id,
        worker_id=worker_id,
    )


# ---------------------------------------------------------------------------
# US-62.1: task-run analytics — count/outcome/duration spread, sliced by
# kind, project, org or agent, for tuning timeouts and other run settings.
# ---------------------------------------------------------------------------


@router.get("/run-analytics")
async def run_analytics(
    group_by: str = "kind",
    days: int = 30,
    org_id: str | None = None,
    project_id: str | None = None,
    worker_id: str | None = None,
    kind: str | None = None,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return db.run_analytics(
        settings,
        org_id,
        group_by=group_by,
        days=days,
        project_id=project_id,
        worker_id=worker_id,
        kind=kind,
    )


@router.get("/user-activity")
async def user_activity(
    days: int = 30,
    org_id: str | None = None,
    project_id: str | None = None,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return db.user_activity(settings, org_id, days=days, project_id=project_id)


@router.get("/gate-latency")
async def gate_latency(
    days: int = 30,
    org_id: str | None = None,
    project_id: str | None = None,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return db.gate_latency(settings, org_id, days=days, project_id=project_id)


@router.get("/performance")
async def performance_summary(
    days: int = 7,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return db.performance_summary(settings, days=days)


@router.get("/performance/detail")
async def performance_detail(
    layer: str,
    days: int = 7,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return {"rows": db.performance_detail(settings, layer, days=days)}


@router.get("/run-analytics/detail")
async def run_analytics_detail(
    group_by: str,
    key: str,
    days: int = 30,
    org_id: str | None = None,
    project_id: str | None = None,
    worker_id: str | None = None,
    kind: str | None = None,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    from .. import db

    return {
        "runs": db.run_analytics_detail(
            settings,
            org_id,
            group_by=group_by,
            key=key,
            days=days,
            project_id=project_id,
            worker_id=worker_id,
            kind=kind,
        )
    }
