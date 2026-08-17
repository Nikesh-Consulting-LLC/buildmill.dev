"""Tenant-facing pool placement (US-57.3).

A shared machine's `agent_servers` row, its probe data, and its job log are
visible only to the platform org (agent_pools.py's RLS story) — a tenant's
only window is the `available_agent_pools()` RPC. Everything here therefore
reads the pool with the SERVICE ROLE after checking the caller's own org
capability, exactly the "build less API" exception `routers/admin.py`
documents for the same reason: authorization already happened above.

This is deliberately a separate router from `agent_servers.py`, which stays
scoped to a caller acting on a host THEY can read via RLS — an org's own
machine, or (for the platform org) a pool itself. A tenant placing an agent
onto someone else's machine is a different shape of request, not a variant
of that one.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import agent_provision
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, admin_get, postgrest_get, postgrest_patch, rpc
from . import runner_socket

router = APIRouter(prefix="/agent-pools", tags=["agent-pools"])


async def _require_develop(org_id: str, user: AuthUser, settings: Settings) -> None:
    """The same gate `create_worker` enforces for minting the identity —
    placement is not a stronger action than creation was."""
    try:
        ok = await rpc(settings, user.token, "has_org_capability", {"p_org": org_id, "p_capability": "develop"})
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Not authorized for that agent's org")


async def _get_pool(settings: Settings, pool_id: str) -> dict[str, Any]:
    rows = await admin_get(
        settings,
        "agent_servers",
        {"select": "id,org_id,status,shared,pool_name,capacity", "id": f"eq.{pool_id}", "limit": "1"},
    )
    if not rows or not rows[0]["shared"]:
        raise HTTPException(status_code=404, detail="That pool does not exist")
    if rows[0]["status"] != "ready":
        raise HTTPException(status_code=409, detail="That pool is not ready yet")
    return rows[0]


async def _free_slots(settings: Settings, pool_id: str, capacity: int) -> int:
    live = await admin_get(
        settings,
        "agent_slots",
        {"select": "id", "agent_server_id": f"eq.{pool_id}", "status": "eq.active"},
    )
    return max(capacity - len(live), 0)


class PlaceBody(BaseModel):
    worker_id: str
    # us-116.6: the state the placed slot lands in — see AddSlotsBody.
    desired_state: str = "paused"


@router.post("/{pool_id}/place")
async def place(
    pool_id: str,
    body: PlaceBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Bind an already-created agent identity to a slot on this pool.

    The identity comes from `create_worker` (the org's own quota already
    gated it, us-57.2); this step only places it, and refuses hard — no
    confirmable override — when the pool has no room. A pool's capacity is
    the superadmin's own decision, not advisory the way a bare machine's
    CPU/disk headroom is (`_capacity_warning` in agent_servers.py).
    """
    worker_rows = await postgrest_get(
        settings, user.token, "workers", {"select": "id,org_id,status", "id": f"eq.{body.worker_id}", "limit": "1"}
    )
    if not worker_rows:
        raise HTTPException(status_code=404, detail="That agent does not exist")
    worker = worker_rows[0]
    await _require_develop(str(worker["org_id"]), user, settings)
    if body.desired_state not in ("enabled", "paused"):
        raise HTTPException(
            status_code=422, detail="desired_state must be 'enabled' or 'paused'"
        )

    live = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {"select": "id", "worker_id": f"eq.{body.worker_id}", "status": "eq.active", "limit": "1"},
    )
    if live:
        raise HTTPException(status_code=409, detail="That agent already runs on a machine.")

    pool = await _get_pool(settings, pool_id)
    free = await _free_slots(settings, pool_id, pool["capacity"] or 0)
    if free < 1:
        raise HTTPException(
            status_code=409,
            detail=f"{pool['pool_name'] or 'This pool'} is full "
            f"({pool['capacity']} of {pool['capacity']} agents placed).",
        )

    try:
        job = await asyncio.to_thread(
            agent_provision.create_job,
            settings,
            org_id=str(pool["org_id"]),
            agent_server_id=pool_id,
            kind="add_slot",
            by=user.id,
            by_email=user.email,
        )
    except agent_provision.JobActive:
        # The pool's one-job-per-host lock is held by someone else's job
        # right now — acknowledge the request instead of dead-ending the
        # wizard, and let `pool_placement_sweep` place it once the host
        # frees up (US-57.3 follow-on, 2026-07-31).
        await asyncio.to_thread(
            agent_provision.upsert_pool_placement_request,
            settings,
            org_id=str(pool["org_id"]),
            pool_id=pool_id,
            worker_id=body.worker_id,
            by=user.id,
            by_email=user.email,
            desired_state=body.desired_state,
        )
        return JSONResponse(
            status_code=202,
            content={"job_id": None, "pool_name": pool["pool_name"], "queued": True},
        )
    agent_provision.launch(
        settings,
        {
            "job_id": str(job["id"]),
            "agent_server_id": pool_id,
            "kind": "add_slot",
            "slots": 1,
            "adopt_worker_id": body.worker_id,
            "desired_state": body.desired_state,
        },
    )
    return {"job_id": str(job["id"]), "pool_name": pool["pool_name"], "queued": False}


class SlotStateBody(BaseModel):
    desired_state: str


@router.patch("/slots/{slot_id}")
async def set_pool_slot_state(
    slot_id: str,
    body: SlotStateBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Enable or pause a tenant's own agent on a shared pool.

    `agent_servers.py`'s `set_slot_state` needs the HOST only to authorize
    (`_require_manage_org(host.org_id)`) — the actual work (pausing the
    runner, pushing the live update, writing `desired_state`) touches only
    the slot and its worker. A tenant cannot read the pool's host row at
    all (US-57.4), so this authorizes on the SLOT's own org instead — the
    one thing `agent_slots` RLS already lets a tenant read and, per
    142/143's existing write policy, write directly.
    """
    if body.desired_state not in ("enabled", "paused"):
        raise HTTPException(status_code=422, detail="desired_state must be 'enabled' or 'paused'")
    rows = await postgrest_get(
        settings, user.token, "agent_slots", {"select": "*", "id": f"eq.{slot_id}", "limit": "1"}
    )
    if not rows or rows[0]["status"] != "active":
        raise HTTPException(status_code=404, detail="Agent not found")
    slot = rows[0]
    try:
        ok = await rpc(
            settings, user.token, "has_org_capability",
            {"p_org": str(slot["org_id"]), "p_capability": "manage_org"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Only an owner can manage this agent")

    paused = body.desired_state == "paused"
    await asyncio.to_thread(agent_provision.set_paused, settings, str(slot["worker_id"]), paused)
    pushed = await runner_socket.push_config_update(settings, str(slot["worker_id"]))
    await postgrest_patch(
        settings, user.token, "agent_slots", {"id": f"eq.{slot_id}"}, {"desired_state": body.desired_state}
    )
    return {"desired_state": body.desired_state, "pushed": pushed}
