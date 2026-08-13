# apps/api/app/admin_auth.py
"""Supabase Auth Admin API calls (US-1.27) — service-role only.

These exist because RLS/RPCs structurally cannot manage auth identities
(email, password, ban status) — only the GoTrue admin API can, and it
requires the service-role key, which must never reach the browser.
"""

import secrets
import string

import httpx

from .config import Settings


# US-9.4: admin-provisioned users get a generated one-time password shared
# offline. Strong by construction (>= 1 of each class), never chosen by a human.
_PW_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_"


def generate_password(length: int = 20) -> str:
    if length < 12:
        length = 12
    while True:
        pw = "".join(secrets.choice(_PW_ALPHABET) for _ in range(length))
        if (
            any(c.islower() for c in pw)
            and any(c.isupper() for c in pw)
            and any(c.isdigit() for c in pw)
            and any(c in "!@#$%^&*-_" for c in pw)
        ):
            return pw


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


async def create_user(
    settings: Settings, email: str, password: str, email_confirm: bool = True
) -> dict:
    """Create a confirmed auth identity (US-9.4). email_confirm=true so the
    provisioned user can log in immediately with the generated password.
    NB: this fires handle_new_user(), which provisions the principal/profile."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.supabase_url}/auth/v1/admin/users",
            json={"email": email, "password": password, "email_confirm": email_confirm},
            headers=_headers(settings),
        )
    if resp.status_code >= 400:
        try:
            message = resp.json().get("msg", resp.text)
        except ValueError:
            message = resp.text
        raise AdminAuthError(message)
    return resp.json()


async def delete_user(settings: Settings, user_id: str) -> None:
    """Hard-delete an auth identity — the rollback path when provisioning fails
    after the auth user was created (cascades to principal/profile/memberships)."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.delete(
            f"{settings.supabase_url}/auth/v1/admin/users/{user_id}",
            headers=_headers(settings),
        )
    if resp.status_code >= 400 and resp.status_code != 404:
        try:
            message = resp.json().get("msg", resp.text)
        except ValueError:
            message = resp.text
        raise AdminAuthError(message)


async def set_password(settings: Settings, user_id: str, password: str) -> None:
    await update_user(settings, user_id, password=password)


async def set_ban(settings: Settings, user_id: str, banned: bool) -> None:
    # GoTrue has no boolean "banned" field — "none" un-bans, a long
    # duration bans indefinitely (there's no literal "forever" value).
    await update_user(settings, user_id, ban_duration="876000h" if banned else "none")
