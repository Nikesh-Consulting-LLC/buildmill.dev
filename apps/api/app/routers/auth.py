"""GET /api/v1/auth/me (US-1.8)."""

from fastapi import APIRouter, Depends, HTTPException

from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import postgrest_get

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
async def me(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    memberships = await postgrest_get(
        settings,
        user.token,
        "organization_members",
        {"select": "org_id", "user_id": f"eq.{user.id}", "limit": "1"},
    )
    if not memberships:
        raise HTTPException(status_code=403, detail="No organization membership")

    return {
        "user_id": user.id,
        "email": user.email,
        "org_id": memberships[0]["org_id"],
    }
