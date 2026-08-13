"""Notification endpoint registry (US-1.44).

Webhook URLs are secrets — they go browser -> api -> data bucket and are
never returned. The endpoint rows (name, host, format, delivery status)
are readable via the SDK under RLS; writes come through here so the URL
and the row stay consistent.
"""

import asyncio
from urllib.parse import urlparse

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import notify, storage
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import postgrest_get

router = APIRouter(prefix="/notifications", tags=["notifications"])


async def _assert_member(settings: Settings, user: AuthUser, org_id: str) -> None:
    rows = await postgrest_get(
        settings,
        user.token,
        "organization_members",
        {"select": "org_id", "org_id": f"eq.{org_id}", "user_id": f"eq.{user.id}"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Organization not found")


async def _get_endpoint_for_user(
    settings: Settings, user: AuthUser, endpoint_id: str
) -> dict:
    rows = await postgrest_get(
        settings,
        user.token,
        "notification_endpoints",
        {"select": "*", "id": f"eq.{endpoint_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return rows[0]


class CreateEndpointBody(BaseModel):
    org_id: str
    name: str
    url: str
    format: str = "json"


@router.post("/endpoints", status_code=201)
async def create_endpoint(
    body: CreateEndpointBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _assert_member(settings, user, body.org_id)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    parsed = urlparse(body.url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Enter a valid http(s) webhook URL")
    if body.format not in ("json", "slack"):
        raise HTTPException(status_code=400, detail="Format must be json or slack")

    try:
        row = await asyncio.to_thread(
            notify.create_endpoint,
            settings,
            body.org_id,
            body.name.strip(),
            parsed.hostname,
            body.format,
        )
    except psycopg.errors.UniqueViolation:
        raise HTTPException(
            status_code=409, detail="An endpoint with this name already exists"
        )
    await storage.put_object(
        settings,
        notify.endpoint_url_path(body.org_id, str(row["id"])),
        body.url.encode("utf-8"),
    )
    return row  # never includes the URL


@router.delete("/endpoints/{endpoint_id}")
async def delete_endpoint(
    endpoint_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    ep = await _get_endpoint_for_user(settings, user, endpoint_id)
    await asyncio.to_thread(notify.delete_endpoint, settings, endpoint_id)
    await storage.delete_object(
        settings, notify.endpoint_url_path(ep["org_id"], endpoint_id)
    )
    return {"ok": True}


@router.post("/endpoints/{endpoint_id}/test")
async def test_endpoint(
    endpoint_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    ep = await _get_endpoint_for_user(settings, user, endpoint_id)
    return await notify.send_test(settings, ep)
