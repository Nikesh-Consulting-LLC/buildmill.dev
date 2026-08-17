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

from .. import agent_provision, db
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
        # us-116.4: the one status — presence in front of the idle reason. The
        # answer carries `state` (what every surface renders) and `reason`
        # (the idle-reason word, kept for existing readers).
        reason = await asyncio.to_thread(db.agent_status, settings, worker_id)
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


# ---------------------------------------------------------------------------
# us-116.5: Start means start.
#
# Three buttons used to be called start and none of them started: the roster's
# ▶ on an agent row was membership Reactivate (its ⏸ Suspend REVOKED the
# token); Enable flipped `runner_config.paused` and never touched a dead
# service; Restart was authorized on the host's org, so a tenant whose agent
# sits on a platform pool got a 404 the runner page swallowed. These two are the
# agent's own — authorized on the SLOT's org, exactly as the PATCH is and for
# the same reason its docstring gives — and Start does everything the word
# implies.
# ---------------------------------------------------------------------------


async def _require_manage_org(org_id: str, user: AuthUser, settings: Settings) -> None:
    try:
        ok = await rpc(
            settings, user.token, "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_org"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Only an owner can start or stop an agent")


def _agent_slot_or_409(settings: Settings, principal_id: str) -> dict[str, Any]:
    slot = agent_provision.slot_for_principal(settings, principal_id)
    if not slot or not slot.get("worker_id"):
        raise HTTPException(
            status_code=409,
            detail=(
                "This agent was installed outside Build Mill, so there are no "
                "start/stop controls for it here — its settings still apply."
            ),
        )
    return slot


@router.post("/{principal_id}/start")
async def start_agent(
    principal_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Leave the agent running and ready, and say what it took.

    1. `runner_config.paused = false`, `agent_slots.desired_state = enabled`,
       push `config.update` — today's Enable.
    2. If the agent is live (a heartbeat inside the window, us-116.4) — done:
       `{enabled: true, restarted: null}`.
    3. If it is not live, or the last probe found its unit anything but
       `active` — queue the existing restart job for the slot (drain, restart
       the unit, restore the desired state — which step 1 just made
       `enabled`): `{enabled: true, restarted: <job_id>}`.

    Refusals are sentences: a job already running on the host (409, naming
    it), a revoked token (409 — the repair is Re-issue, a different button,
    and the manager should be told which).
    """
    slot = _agent_slot_or_409(settings, principal_id)
    await _require_manage_org(str(slot["org_id"]), user, settings)
    worker_id = str(slot["worker_id"])

    worker = db.get_worker(settings, worker_id)
    if not worker or worker.get("status") != "active":
        raise HTTPException(
            status_code=409,
            detail=(
                "This agent's worker token has been revoked — starting it would "
                "run a service that can claim nothing. Re-issue its token from the "
                "machine page first."
            ),
        )

    await asyncio.to_thread(agent_provision.set_paused, settings, worker_id, False)
    await asyncio.to_thread(
        agent_provision.update_slot, settings, str(slot["id"]), {"desired_state": "enabled"}
    )
    await agent_provision.push_config(settings, worker_id)

    live = await asyncio.to_thread(db.worker_is_live, settings, worker_id)
    unit = str(slot.get("service_state") or "unknown")
    restarted: str | None = None
    if not live or unit not in ("active", "unknown"):
        try:
            job = await asyncio.to_thread(
                agent_provision.create_job,
                settings,
                org_id=str(slot["host_org_id"]),
                agent_server_id=str(slot["agent_server_id"]),
                kind="restart",
                slot_id=str(slot["id"]),
                by=user.id,
                by_email=user.email,
            )
        except agent_provision.JobActive as e:
            raise HTTPException(
                status_code=409,
                detail=f"{e} The agent is enabled; start it again once the machine is free.",
            )
        agent_provision.launch(
            settings,
            {
                "job_id": str(job["id"]),
                "agent_server_id": str(slot["agent_server_id"]),
                "kind": "restart",
                "slot_id": str(slot["id"]),
            },
        )
        restarted = str(job["id"])
    return {"enabled": True, "restarted": restarted, "live": live, "service_state": unit}


@router.post("/{principal_id}/stop")
async def stop_agent(
    principal_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Today's Pause, unchanged in meaning: the service keeps running, the
    socket stays up, the agent claims nothing, a run it holds finishes. It
    reads **Stopped** (us-116.4). Deliberately not `systemctl stop`: a stopped
    process cannot tell you it is stopped."""
    slot = _agent_slot_or_409(settings, principal_id)
    await _require_manage_org(str(slot["org_id"]), user, settings)
    worker_id = str(slot["worker_id"])
    await asyncio.to_thread(agent_provision.set_paused, settings, worker_id, True)
    await asyncio.to_thread(
        agent_provision.update_slot, settings, str(slot["id"]), {"desired_state": "paused"}
    )
    await agent_provision.push_config(settings, worker_id)
    busy = await asyncio.to_thread(agent_provision.worker_is_busy, settings, worker_id)
    return {
        "enabled": False,
        "finishing": (busy or {}).get("title") or ((busy or {}).get("id") and str(busy["id"])) if busy else None,
    }
