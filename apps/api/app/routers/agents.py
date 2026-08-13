"""Agent-level configuration (Phase 32).

An agent is a principal, so reading it goes straight to Supabase under RLS from
the browser like everything else. What lives here is the writes that must fan
out across tables the browser must not be trusted to keep consistent — starting
with the rename (US-32.2), which touches three name columns and leaves the
infrastructure identity alone.

Gated on `manage_work`, the same capability the runner config PATCH uses: these
endpoints are the rest of the agent settings page, and a page whose fields need
two different permissions is a page that half-works for somebody.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, postgrest_get, rpc

router = APIRouter(prefix="/agents", tags=["agents"])

MAX_NAME = 80


async def _require_manage_work(
    org_id: str, user: AuthUser, settings: Settings
) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_work"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403, detail="Not authorized to configure agents"
        )


@router.get("/idle-reasons")
async def agent_idle_reasons(
    org: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-35.1: why each agent in this org is not working, keyed by worker id.

    The same computation `GET /agent-servers/{id}/slots/idle-reasons` serves,
    asked org-wide instead of host-wide. Team and the dashboard need it for
    agents that are not on a managed machine at all, which the host-scoped
    route cannot reach — and answering them from a second implementation is
    how two surfaces come to disagree about why an agent is idle.

    Org scoping is RLS, not a filter written here: the worker list is read with
    the caller's own token, so a non-member sees no workers and therefore gets
    no reasons. There is nothing to authorise beyond being able to see the
    agent, which is why this is a plain member read rather than `manage_work`.
    """
    rows = await postgrest_get(
        settings,
        user.token,
        "workers",
        {"select": "id,principal_id", "org_id": f"eq.{org}"},
    )
    out: dict[str, Any] = {}
    by_principal: dict[str, Any] = {}
    for row in rows:
        worker_id = str(row["id"])
        reason = await asyncio.to_thread(db.worker_idle_reason, settings, worker_id)
        out[worker_id] = reason
        # Team addresses agents by principal; the dashboard addresses them by
        # worker. Both keys, one computation — rather than each caller
        # rebuilding the mapping and one of them getting it wrong.
        if row.get("principal_id"):
            by_principal[str(row["principal_id"])] = reason
    return {"reasons": out, "by_principal": by_principal}


class RenameBody(BaseModel):
    name: str


@router.patch("/{principal_id}/name")
async def rename_agent(
    principal_id: str,
    body: RenameBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-32.2: rename an agent everywhere its name is stored.

    Names are deliberately not forced unique — humans in the roster may share
    one and agents are not a special case; every surface shows the slot or
    service id beside the name.
    """
    agent = db.agent_identity(settings, principal_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    if agent["kind"] != "agent":
        raise HTTPException(
            status_code=422,
            detail="only agents are renamed here — a person's name is theirs to change",
        )
    if not agent.get("org_id"):
        raise HTTPException(
            status_code=409,
            detail="this agent has no worker, so there is no org to authorise against",
        )
    await _require_manage_work(str(agent["org_id"]), user, settings)

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="a name cannot be empty")
    if len(name) > MAX_NAME:
        raise HTTPException(
            status_code=422, detail=f"a name may be at most {MAX_NAME} characters"
        )

    result = db.rename_agent(
        settings,
        principal_id,
        name,
        actor_id=user.id,
        actor_email=user.email or "",
        org_id=str(agent["org_id"]),
    )
    return {
        "name": result["name"],
        "previous_name": result["from"],
        # The identity the machine runs under, unchanged and said out loud so a
        # reader can see the rename did not touch it.
        "worker_id": str(agent["worker_id"]) if agent.get("worker_id") else None,
        "slot_index": agent.get("slot_index"),
        "service_name": agent.get("service_name"),
    }
