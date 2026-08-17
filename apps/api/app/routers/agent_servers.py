"""Agent server registry and fleet actions (Phase 26, US-26.1–26.10).

Deliberately action-only. Reading the fleet — hosts, slots, job logs — goes
straight to Supabase under RLS from the browser (three read policies in
`142_agent_servers.sql`), per ARCHITECTURE's "build less API". What lives here
is what genuinely needs a server: opening SSH connections with credentials the
browser must never see, minting worker tokens, and running jobs that outlive
the request that started them.

Every write is gated on the `manage_org` capability. Registering a machine that
will be handed root-level install commands is org infrastructure, not project
work — and the same check is what lets the job path write with the service role
afterwards without re-deriving who authorised it.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import agent_provision, db, ssh
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import (
    RpcError,
    postgrest_get,
    postgrest_patch,
    postgrest_post,
    rpc,
)
from . import runner_socket, servers as servers_router

router = APIRouter(prefix="/agent-servers", tags=["agent-servers"])

VALID_MODULES = tuple(sorted(agent_provision.KNOWN_MODULES))
# US-55.1: a template entry means project ACCESS. 'access' is the canonical
# value; the seven historical kind names stay accepted so a template saved
# before the model change still validates (provisioning grants the same
# access either way and ignores the per-kind detail).
CAPABILITIES = (
    "access", "prd", "breakdown", "plan", "code", "test", "release", "deploy",
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _require_manage_org(org_id: str, user: AuthUser, settings: Settings) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_org"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403,
            detail="Only an owner can manage agent servers",
        )


async def _require_platform_admin_if_shared(shared: bool | None, user: AuthUser, settings: Settings) -> None:
    """US-57.1: `shared`/`pool_name`/`capacity` are the superadmin's to set.

    142_agent_server_write_policies.sql's manage_org check (above) still gates
    the row itself, but marking a machine SHARED is a platform decision, not
    an org one. 201_shared_agent_server_platform_only.sql is the hard
    backstop at the database layer (a trigger, since RLS is row- not
    column-level); this is the friendly 403 in front of it.
    """
    if not shared:
        return
    try:
        is_admin = await rpc(settings, user.token, "is_platform_admin", {})
    except RpcError:
        is_admin = False
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Only the platform admin may mark a machine shared.",
        )


async def _require_platform_admin(user: AuthUser, settings: Settings, detail: str) -> None:
    """US-68.3: turning a machine's auto-repair service on or off runs
    unattended jobs against production infrastructure, so — like `shared`
    (US-57.1) — it is the platform admin's call, not any org manager who
    happens to have `manage_org` on the owning org."""
    try:
        is_admin = await rpc(settings, user.token, "is_platform_admin", {})
    except RpcError:
        is_admin = False
    if not is_admin:
        raise HTTPException(status_code=403, detail=detail)


def _validate_pool_shape(shared: bool | None, pool_name: str | None, capacity: int | None) -> None:
    if not shared:
        return
    if not pool_name or not pool_name.strip():
        raise HTTPException(status_code=422, detail="A shared machine needs a pool name.")
    if capacity is None or not (0 <= capacity <= 64):
        raise HTTPException(status_code=422, detail="Capacity must be between 0 and 64.")


async def _get_host(settings: Settings, user: AuthUser, host_id: str) -> dict[str, Any]:
    """Fetch a host under the caller's own JWT — RLS is the isolation gate."""
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_servers",
        {"select": "*,servers(*)", "id": f"eq.{host_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Agent server not found")
    host = rows[0]
    if host["status"] == "removed":
        raise HTTPException(
            status_code=409,
            detail="This agent server was torn down. Provision it again to bring it back.",
        )
    return host


async def _start(
    settings: Settings,
    user: AuthUser,
    host: dict[str, Any],
    kind: str,
    **ctx: Any,
) -> dict[str, Any]:
    """Create the job row and launch it, refusing a second job on the host."""
    try:
        job = await asyncio.to_thread(
            agent_provision.create_job,
            settings,
            org_id=str(host["org_id"]),
            agent_server_id=str(host["id"]),
            kind=kind,
            slot_id=ctx.get("slot_id"),
            by=user.id,
            by_email=user.email,
        )
    except agent_provision.JobActive as e:
        raise HTTPException(status_code=409, detail=str(e))
    agent_provision.launch(
        settings,
        {
            "job_id": str(job["id"]),
            "agent_server_id": str(host["id"]),
            "kind": kind,
            **ctx,
        },
    )
    return {"job_id": str(job["id"]), "kind": kind}


def _validate_modules(modules: list[str] | None) -> None:
    unknown = [m for m in (modules or []) if m not in agent_provision.KNOWN_MODULES]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"unknown module(s): {', '.join(unknown)} — valid: "
            + ", ".join(VALID_MODULES),
        )


def _providers_if_needed(
    settings: Settings, org_id: str, template: dict[str, Any] | None
) -> list[dict[str, Any]] | None:
    """The org's LLM providers, but only when there is a model route to check
    against them (US-27.8). A template with no routes needs no lookup."""
    if not (template or {}).get("model_routes"):
        return None
    return db.get_org_llm_config(settings, org_id)[0]


def _validate_template(
    template: dict[str, Any] | None,
    host_modules: list[str],
    providers: list[dict[str, Any]] | None = None,
) -> None:
    """US-26.6: the same rules PATCH /runner/{id}/config enforces, plus one.

    A template naming a module the host never installed would produce slots
    that fail every run, so it is refused here rather than at the first
    dispatch.
    """
    if not template:
        return
    body = runner_socket.RunnerConfigBody(
        enabled_modules=template.get("enabled_modules"),
        model_routes=template.get("model_routes"),
        autonomy_policy=template.get("autonomy_policy"),
    )
    runner_socket._validate_config_body(body)

    # US-27.8: a template validates modules against the host but said nothing
    # about models. The same disagreement that broke a runner config breaks
    # every slot this host ever creates, so it is refused in the same place.
    if providers is not None:
        problem = runner_socket.validate_model_provider_pairing(
            template.get("enabled_modules"),
            template.get("model_routes"),
            providers,
        )
        if problem:
            raise HTTPException(status_code=422, detail=problem)

    missing = [
        m
        for m in (template.get("enabled_modules") or [])
        if m != "sim" and m not in (host_modules or [])
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"the template enables {', '.join(missing)}, which this host does "
            f"not install — installed: {', '.join(host_modules) or 'none'}",
        )

    for grant in template.get("capabilities") or []:
        if not isinstance(grant, dict) or not grant.get("project_id"):
            raise HTTPException(
                status_code=422, detail="each capability grant needs a project_id"
            )
        bad = [c for c in (grant.get("capabilities") or []) if c not in CAPABILITIES]
        if bad:
            raise HTTPException(
                status_code=422,
                detail=f"unknown capability: {', '.join(bad)} — valid: "
                + ", ".join(CAPABILITIES),
            )


# ---------------------------------------------------------------------------
# Registration (US-26.1)
# ---------------------------------------------------------------------------


class CreateAgentServerBody(BaseModel):
    server_id: str
    workdir: str = "/opt/buildmill"
    modules: list[str] = []
    extra_packages: list[str] = []
    setup_commands: str = ""
    allow_agent_sudo: bool = False
    slot_template: dict[str, Any] = {}
    # US-57.1: a shared machine IS a pool — named and sized by the superadmin.
    # Never settable except from the platform-admin org (see the two guards
    # above); absent, a machine is what Phase 26 always built.
    shared: bool = False
    pool_name: str | None = None
    capacity: int | None = None


@router.post("")
async def create_agent_server(
    body: CreateAgentServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Register a machine as an agent server, after preflight.

    Preflight runs BEFORE the row is written: a machine that is not
    Debian-family, has no systemd, or whose SSH user cannot sudo is rejected
    here with the reason named, rather than accepted and left to fail three
    minutes into an install with half a toolchain on it.
    """
    server = await servers_router.get_server_for_user(settings, user.token, body.server_id)
    await _require_manage_org(str(server["org_id"]), user, settings)
    await _require_platform_admin_if_shared(body.shared, user, settings)
    _validate_pool_shape(body.shared, body.pool_name, body.capacity)
    _validate_modules(body.modules)
    _validate_template(
        body.slot_template,
        body.modules,
        _providers_if_needed(settings, str(server["org_id"]), body.slot_template),
    )

    if not body.workdir.startswith("/"):
        raise HTTPException(status_code=422, detail="the working folder must be an absolute path")

    existing = await postgrest_get(
        settings,
        user.token,
        "agent_servers",
        {"select": "id,status", "server_id": f"eq.{body.server_id}", "limit": "1"},
    )
    if existing and existing[0]["status"] != "removed":
        raise HTTPException(
            status_code=409,
            detail="This server is already registered as an agent server.",
        )

    # US-27.13: refuse before opening an SSH session. A loopback API address
    # cannot work from any remote machine, so there is nothing to test and no
    # reason to make the admin wait for a connection to find that out.
    problem = agent_provision.api_url_problem(settings.api_base_url)
    if problem:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "The factory's own address is not usable by a remote machine.",
                "checks": [
                    {"check": "factory-reachable", "ok": False, "detail": problem}
                ],
            },
        )

    checks = await _run_preflight(settings, user, server, body.workdir)
    failed = [c for c in checks if not c["ok"]]
    if failed:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "This machine cannot host agents yet.",
                "checks": checks,
            },
        )

    fields = {
        "workdir": body.workdir.rstrip("/") or "/opt/buildmill",
        "modules": body.modules,
        "extra_packages": body.extra_packages,
        "setup_commands": body.setup_commands,
        "allow_agent_sudo": body.allow_agent_sudo,
        "slot_template": body.slot_template,
        "status": "new",
        "shared": body.shared,
        "pool_name": body.pool_name,
        "capacity": body.capacity,
    }
    if existing:
        # a torn-down host comes back on its own row, keeping its job history
        rows = await postgrest_patch(
            settings, user.token, "agent_servers", {"id": f"eq.{existing[0]['id']}"}, fields
        )
    else:
        rows = await postgrest_post(
            settings,
            user.token,
            "agent_servers",
            {"org_id": server["org_id"], "server_id": body.server_id, **fields},
        )
    return {"agent_server": rows[0] if rows else None, "checks": checks}


class UpdateAgentServerBody(BaseModel):
    workdir: str | None = None
    modules: list[str] | None = None
    extra_packages: list[str] | None = None
    setup_commands: str | None = None
    allow_agent_sudo: bool | None = None
    slot_template: dict[str, Any] | None = None
    shared: bool | None = None
    pool_name: str | None = None
    capacity: int | None = None
    auto_repair_enabled: bool | None = None


@router.patch("/{host_id}")
async def update_agent_server(
    host_id: str,
    body: UpdateAgentServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Edit a host's definition. Takes effect on the next provision/update —
    the row is the definition, the machine converges to it."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    # US-57.1: touching shared/pool_name/capacity on an existing host is a
    # platform decision too, whether or not `shared` itself is in this PATCH.
    resulting_shared = fields.get("shared", host.get("shared", False))
    if "shared" in fields or (resulting_shared and ("pool_name" in fields or "capacity" in fields)):
        await _require_platform_admin_if_shared(True, user, settings)
    if resulting_shared:
        _validate_pool_shape(
            True,
            fields.get("pool_name", host.get("pool_name")),
            fields.get("capacity", host.get("capacity")),
        )
    if "auto_repair_enabled" in fields and fields["auto_repair_enabled"] != host.get(
        "auto_repair_enabled"
    ):
        # US-68.3: the field rides the same generic SetupTab save as modules,
        # extra packages, etc. — only an actual flip needs the platform-admin
        # gate; re-sending the unchanged value alongside an unrelated edit
        # must not 403 an ordinary org manager saving their own machine.
        await _require_platform_admin(
            user, settings, "Only the platform admin may change auto-repair for a machine."
        )
    if "modules" in fields:
        _validate_modules(fields["modules"])
    if "slot_template" in fields or "modules" in fields:
        template = fields.get("slot_template", host.get("slot_template"))
        _validate_template(
            template,
            fields.get("modules", host.get("modules") or []),
            _providers_if_needed(settings, str(host["org_id"]), template),
        )
    if "workdir" in fields and not fields["workdir"].startswith("/"):
        raise HTTPException(status_code=422, detail="the working folder must be an absolute path")
    if not fields:
        return {"agent_server": host, "changed": []}

    rows = await postgrest_patch(
        settings, user.token, "agent_servers", {"id": f"eq.{host_id}"}, fields
    )
    return {"agent_server": rows[0] if rows else None, "changed": sorted(fields)}


async def _run_preflight(
    settings: Settings, user: AuthUser, server: dict[str, Any], workdir: str
) -> list[dict[str, Any]]:
    conn = await servers_router.connect_server(settings, user.token, server)
    try:
        password = None
        if server["auth_method"] == "password":
            creds = await servers_router.resolve_credentials(settings, server)
            password = creds.password
        return await asyncio.to_thread(
            agent_provision.preflight,
            conn.transport,
            workdir,
            password,
            # US-27.13: the address this machine's agents will be told to
            # dial, tested from the machine rather than from here.
            settings.api_base_url,
        )
    finally:
        conn.close()


@router.post("/{host_id}/preflight")
async def preflight(
    host_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Re-run the registration checks against a host that already exists."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    checks = await _run_preflight(settings, user, host["servers"], host["workdir"])
    return {"checks": checks, "ok": all(c["ok"] for c in checks)}


# ---------------------------------------------------------------------------
# Jobs (US-26.2, 26.4, 26.7, 26.8, 26.9)
# ---------------------------------------------------------------------------


class ProvisionBody(BaseModel):
    slots: int = 0


@router.post("/{host_id}/provision")
async def provision(
    host_id: str,
    body: ProvisionBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Install (or finish installing) the machine, optionally with N agents.

    Idempotent: running it on a half-installed box resumes rather than
    restarts, so a failed provision is fixed by pressing it again.
    """
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    if body.slots < 0 or body.slots > 32:
        raise HTTPException(status_code=422, detail="slots must be between 0 and 32")
    return await _start(settings, user, host, "provision", slots=body.slots)


class AddSlotsBody(BaseModel):
    slots: int = 1
    adopt_worker_id: str | None = None
    confirm_capacity: bool = False


@router.post("/{host_id}/slots")
async def add_slots(
    host_id: str,
    body: AddSlotsBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Add agents to a provisioned host.

    The capacity check is advisory (US-26.7): it names the actual numbers and
    can be confirmed through. The operator knows things the probe does not —
    that the box is about to be resized, that the workload is IO-bound — and a
    hard block would be the app overruling them on their own hardware.
    """
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    if host["status"] == "new":
        raise HTTPException(
            status_code=409,
            detail="Provision this machine before adding agents to it.",
        )
    if body.slots < 1 or body.slots > 32:
        raise HTTPException(status_code=422, detail="slots must be between 1 and 32")
    if body.adopt_worker_id and body.slots != 1:
        raise HTTPException(
            status_code=422, detail="binding an existing agent adds exactly one slot"
        )

    if body.adopt_worker_id:
        await _check_adoptable(settings, user, host, body.adopt_worker_id)
        await _check_modules_allowed_on_host(settings, user, host, body.adopt_worker_id)

    if not body.confirm_capacity:
        warning = await _capacity_warning(settings, user, host, body.slots)
        if warning:
            raise HTTPException(status_code=409, detail=warning)

    return await _start(
        settings,
        user,
        host,
        "add_slot",
        slots=body.slots,
        adopt_worker_id=body.adopt_worker_id,
    )


# US-78.6: modules that may only run on a platform pool. The database enforces
# this too (migration 236) — that is the enforcement point a raw PostgREST write
# cannot get around. This exists so the manager gets a sentence instead of a
# trigger's exception text bubbling up as a 500.
POOL_ONLY_MODULES = ("interactive",)


async def _check_modules_allowed_on_host(
    settings: Settings, user: AuthUser, host: dict[str, Any], worker_id: str
) -> None:
    """Refuse an interactive agent a slot on a machine the platform does not own."""
    if host.get("shared"):
        return
    rows = await postgrest_get(
        settings,
        user.token,
        "runner_config",
        {"select": "enabled_modules", "worker_id": f"eq.{worker_id}", "limit": "1"},
    )
    enabled = (rows[0].get("enabled_modules") if rows else None) or []
    blocked = sorted(set(enabled) & set(POOL_ONLY_MODULES))
    if blocked:
        raise HTTPException(
            status_code=409,
            detail=(
                "A Buildmill Interactive Agent runs on a platform agent pool "
                "only, not on a machine your organization manages."
            ),
        )


async def _check_adoptable(
    settings: Settings, user: AuthUser, host: dict[str, Any], worker_id: str
) -> None:
    rows = await postgrest_get(
        settings,
        user.token,
        "workers",
        {"select": "id,name,org_id,status", "id": f"eq.{worker_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="That agent does not exist")
    if str(rows[0]["org_id"]) != str(host["org_id"]):
        raise HTTPException(status_code=404, detail="That agent does not exist")
    live = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {
            "select": "id,name,agent_server_id",
            "worker_id": f"eq.{worker_id}",
            "status": "eq.active",
            "limit": "1",
        },
    )
    if live:
        raise HTTPException(
            status_code=409,
            detail="That agent already runs on an agent server. Remove it there first.",
        )


async def _capacity_warning(
    settings: Settings, user: AuthUser, host: dict[str, Any], adding: int
) -> dict[str, Any] | None:
    live = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {"select": "id", "agent_server_id": f"eq.{host['id']}", "status": "eq.active"},
    )
    after = len(live) + adding
    cpu = host.get("cpu_count")
    free = host.get("disk_free_gb")
    reasons = []
    if cpu and after > cpu:
        reasons.append(f"{after} agents on {cpu} CPU core(s)")
    if free is not None and float(free) < agent_provision.MIN_FREE_GB_FOR_SLOT:
        reasons.append(f"{free} GB free (under {agent_provision.MIN_FREE_GB_FOR_SLOT} GB)")
    if not reasons:
        return None
    return {
        "message": "This machine may not have room: " + "; ".join(reasons) + ".",
        "confirmable": True,
        "reasons": reasons,
    }


@router.post("/{host_id}/update")
async def update_fleet(
    host_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Re-push the current bundle and re-apply the host's definition, then
    restart each agent one at a time, draining its in-flight run first."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    if host["status"] == "new":
        raise HTTPException(status_code=409, detail="This machine has not been provisioned yet.")
    return await _start(settings, user, host, "update")


@router.post("/{host_id}/probe")
async def probe(
    host_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Read the machine's health now, instead of waiting for the sweep."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    return await _start(settings, user, host, "probe")


class TeardownBody(BaseModel):
    force: bool = False
    wipe_workdir: bool = False


@router.post("/{host_id}/teardown")
async def teardown(
    host_id: str,
    body: TeardownBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Decommission: stop every agent, revoke its token, remove the units.

    Wiping the working folder is opt-in and off by default — workspaces can
    hold the only copy of an uncommitted diff.
    """
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    if host.get("shared"):
        await _refuse_if_occupied(settings, user, host)
    return await _start(
        settings, user, host, "teardown", force=body.force, wipe_workdir=body.wipe_workdir
    )


async def _refuse_if_occupied(
    settings: Settings, user: AuthUser, host: dict[str, Any]
) -> None:
    """US-57.5: a pool with tenant agents on it does not tear down — moving
    them is a conversation, not a button. Named by org, not by count, so the
    superadmin knows who to have that conversation with."""
    slots = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {"select": "org_id", "agent_server_id": f"eq.{host['id']}", "status": "eq.active"},
    )
    tenants = sorted({str(s["org_id"]) for s in slots if str(s["org_id"]) != str(host["org_id"])})
    if tenants:
        orgs = await postgrest_get(
            settings, user.token, "organizations", {"select": "id,name", "id": f"in.({','.join(tenants)})"}
        )
        names = [o["name"] for o in orgs] or tenants
        raise HTTPException(
            status_code=409,
            detail=f"This pool still has agents from: {', '.join(names)}. "
            "Move or remove them first.",
        )


# ---------------------------------------------------------------------------
# Slot controls (US-26.5, US-26.9)
# ---------------------------------------------------------------------------


async def _get_slot(
    settings: Settings, user: AuthUser, host_id: str, slot_id: str
) -> dict[str, Any]:
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {
            "select": "*",
            "id": f"eq.{slot_id}",
            "agent_server_id": f"eq.{host_id}",
            "limit": "1",
        },
    )
    if not rows or rows[0]["status"] != "active":
        raise HTTPException(status_code=404, detail="Agent not found on this machine")
    return rows[0]


class SlotStateBody(BaseModel):
    desired_state: str


@router.patch("/{host_id}/slots/{slot_id}")
async def set_slot_state(
    host_id: str,
    slot_id: str,
    body: SlotStateBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Enable or pause one agent.

    Pause is not stop: the service keeps running and the socket stays
    connected — the agent just claims nothing. That is what makes it a safe
    default state and a usable drain primitive. A run already claimed is left
    alone to finish; cancelling work is the queue's job, not the fleet's.

    Authorized against the SLOT's own org, not the host's: a slot on a shared
    platform pool lives under the platform-admin org (`agent_servers.org_id`),
    which the slot's own workspace owner is never a member of. `_get_host`
    would 404 for them via RLS before the capability check even ran. The slot
    itself is already org-scoped correctly (`agent_slots.org_id` is the
    tenant), so checking there is both sufficient and correct — it is exactly
    what the `agent_slots` UPDATE policy (143_agent_server_write_policies.sql)
    already gates the write on.
    """
    if body.desired_state not in ("enabled", "paused"):
        raise HTTPException(
            status_code=422, detail="desired_state must be 'enabled' or 'paused'"
        )
    slot = await _get_slot(settings, user, host_id, slot_id)
    await _require_manage_org(str(slot["org_id"]), user, settings)

    paused = body.desired_state == "paused"
    await asyncio.to_thread(
        agent_provision.set_paused, settings, str(slot["worker_id"]), paused
    )
    pushed = await runner_socket.push_config_update(settings, str(slot["worker_id"]))
    await postgrest_patch(
        settings, user.token, "agent_slots", {"id": f"eq.{slot_id}"},
        {"desired_state": body.desired_state},
    )

    busy = await asyncio.to_thread(
        agent_provision.worker_is_busy, settings, str(slot["worker_id"])
    )
    return {
        "desired_state": body.desired_state,
        "pushed": pushed,
        "finishing": (busy or {}).get("title") if paused and busy else None,
    }


@router.post("/{host_id}/slots/{slot_id}/restart")
async def restart_slot(
    host_id: str,
    slot_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Drain, restart the service, and put the agent back how it was."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    slot = await _get_slot(settings, user, host_id, slot_id)
    return await _start(settings, user, host, "restart", slot_id=str(slot["id"]))


@router.post("/{host_id}/slots/{slot_id}/reissue-token")
async def reissue_slot_token(
    host_id: str,
    slot_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-27.9: mint a new worker token, write it to the machine, restart.

    The repair for a revoked managed agent. Revoke is right for a
    hand-installed worker — someone pastes a new token in. For a slot Build
    Mill installed, the token lives in a 0600 env file the app wrote, and
    there is nobody to paste anything: revoking one leaves the machine running
    and useless. Un-revoking is deliberately not offered; a revoked credential
    stays revoked and this delivers a new one."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    slot = await _get_slot(settings, user, host_id, slot_id)
    if not slot.get("worker_id"):
        raise HTTPException(
            status_code=409, detail="This agent has no identity to re-issue."
        )
    return await _start(
        settings, user, host, "reissue_token", slot_id=str(slot["id"])
    )


@router.get("/{host_id}/slots/idle-reasons")
async def slot_idle_reasons(
    host_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-27.9: why each agent on this host is not working.

    Presence is not permission. A connected socket proves the process is
    alive; it does not prove the worker may claim — the token can be revoked,
    the agent can be paused, its grants can exclude every queued item, or the
    whole queue can be held. The Agents tab showed presence and left the
    manager to infer the rest, which is how fourteen minutes disappeared."""
    host = await _get_host(settings, user, host_id)
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_slots",
        {
            "select": "id,worker_id",
            "agent_server_id": f"eq.{host_id}",
            "status": "eq.active",
        },
    )
    out: dict[str, Any] = {}
    for row in rows:
        if row.get("worker_id"):
            # us-116.4: the one status (presence first), same as /agents.
            out[str(row["id"])] = await asyncio.to_thread(
                db.agent_status, settings, str(row["worker_id"])
            )
    return {"reasons": out}


@router.delete("/{host_id}/slots/{slot_id}")
async def remove_slot(
    host_id: str,
    slot_id: str,
    force: bool = False,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Uninstall one agent: stop it, remove its unit and env file, revoke its
    token, and retire the identity — kept, not deleted, so past runs still
    name who did them."""
    host = await _get_host(settings, user, host_id)
    await _require_manage_org(str(host["org_id"]), user, settings)
    slot = await _get_slot(settings, user, host_id, slot_id)

    if not force:
        busy = await asyncio.to_thread(
            agent_provision.worker_is_busy, settings, str(slot["worker_id"])
        )
        if busy:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"{slot['name']} is still running "
                    f"{busy.get('title') or busy['id']}. Wait for it to hand back, "
                    "or force removal — which hands that run back as failed.",
                    "run_id": str(busy["id"]),
                    "forcible": True,
                },
            )

    return await _start(
        settings, user, host, "remove_slot", slot_id=str(slot["id"]), force=force
    )


# ---------------------------------------------------------------------------
# Fleet drift (US-26.8)
# ---------------------------------------------------------------------------


@router.get("/current-version")
async def current_version(
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """The bundle hash this API would install — the drift comparison point.

    Hash of the runner tree's contents, so two deploys of the same commit
    agree and there is no version file anyone can forget to bump.
    """
    return {"bundle_hash": await asyncio.to_thread(agent_provision.bundle_hash)}
