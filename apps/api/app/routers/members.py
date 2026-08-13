# apps/api/app/routers/members.py
"""Org member provisioning (US-9.4) — capability-gated, service-role execution.

An org admin (has_org_capability manage_members) or the Superadmin can create a
human user directly instead of waiting for signup: generate a strong one-time
password, create the confirmed auth identity + human principal + org membership,
and flag must-change (US-9.5). The password is returned EXACTLY ONCE and shared
offline — the app sends nothing (no SMTP). Authorization is checked against the
caller's own JWT first; only then does the service role execute.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import admin_auth
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    PostgrestError,
    RpcError,
    admin_get,
    admin_patch,
    admin_post,
    rpc,
)

router = APIRouter(prefix="/orgs", tags=["members"])

ORG_ROLES = ("owner", "admin", "lead", "developer", "reviewer", "viewer")


async def _require_manage_members(org_id: str, user: AuthUser, settings: Settings) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_members"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Not authorized to manage members")


class ProvisionMemberBody(BaseModel):
    email: str
    display_name: str | None = None
    role: str = "developer"


@router.post("/{org_id}/members/provision")
async def provision_member(
    org_id: str,
    body: ProvisionMemberBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_manage_members(org_id, user, settings)

    email = body.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email is required")
    if body.role not in ORG_ROLES:
        raise HTTPException(status_code=400, detail="Invalid role")

    existing = await admin_get(
        settings,
        "principals",
        {"select": "id", "email": f"eq.{email}", "kind": "eq.human", "limit": "1"},
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="An account with that email already exists — add them to this org with “Add a member by email” instead.",
        )

    password = admin_auth.generate_password()
    try:
        created = await admin_auth.create_user(settings, email, password)
    except admin_auth.AdminAuthError as e:
        raise HTTPException(
            status_code=409,
            detail=f"Could not create the account (it may already exist): {e.message}",
        )

    user_id = created["id"]
    display = body.display_name or email.split("@")[0]
    try:
        # handle_new_user() already provisioned the principal on auth insert;
        # resolve it and flag must-change (create defensively if the trigger
        # somehow didn't run).
        prow = await admin_get(
            settings,
            "principals",
            {"select": "id", "auth_user_id": f"eq.{user_id}", "limit": "1"},
        )
        if prow:
            principal_id = prow[0]["id"]
            await admin_patch(
                settings,
                "principals",
                {"id": f"eq.{principal_id}"},
                {"must_change_password": True, "display_name": display},
            )
        else:
            made = await admin_post(
                settings,
                "principals",
                {
                    "kind": "human",
                    "email": email,
                    "display_name": display,
                    "auth_user_id": user_id,
                    "must_change_password": True,
                },
            )
            principal_id = made[0]["id"]

        await admin_post(
            settings,
            "organization_members",
            {
                "org_id": org_id,
                "principal_id": principal_id,
                "user_id": user_id,
                "role": body.role,
            },
        )
    except (PostgrestError, admin_auth.AdminAuthError) as e:
        # Roll back the auth identity so we never leave a half-provisioned user.
        try:
            await admin_auth.delete_user(settings, user_id)
        except admin_auth.AdminAuthError:
            pass
        message = getattr(e, "message", str(e))
        raise HTTPException(status_code=409, detail=f"Provisioning failed: {message}")

    return {"user_id": user_id, "email": email, "password": password}


@router.post("/{org_id}/members/{user_id}/reset-password")
async def reset_member_password(
    org_id: str,
    user_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_manage_members(org_id, user, settings)

    prow = await admin_get(
        settings,
        "principals",
        {"select": "id", "auth_user_id": f"eq.{user_id}", "limit": "1"},
    )
    if not prow:
        raise HTTPException(status_code=404, detail="User not found")
    principal_id = prow[0]["id"]

    member = await admin_get(
        settings,
        "organization_members",
        {
            "select": "principal_id",
            "org_id": f"eq.{org_id}",
            "principal_id": f"eq.{principal_id}",
            "limit": "1",
        },
    )
    if not member:
        raise HTTPException(status_code=404, detail="Not a member of this organization")

    password = admin_auth.generate_password()
    try:
        await admin_auth.set_password(settings, user_id, password)
    except admin_auth.AdminAuthError as e:
        raise HTTPException(status_code=502, detail=e.message)

    await admin_patch(
        settings,
        "principals",
        {"id": f"eq.{principal_id}"},
        {"must_change_password": True},
    )
    return {"password": password}
