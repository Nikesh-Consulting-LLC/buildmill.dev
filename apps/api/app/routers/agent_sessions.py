"""Sessions with no work item (US-78.10).

A manager opens a session against a project and talks to the agent directly —
no issue, no dispatch, no lease. Everything downstream is the machinery a run
already uses: the same ACP client on the machine, the same console, the same
metering. This router is the lifecycle around it.

Why a session is not a run, restated where it will be read: a run has a claim, a
lease, an item and a review gate. A session has none of those. Putting these in
`runs` would mean every dispatch query, every pool listing and every attempt
counter learning to ignore a row shape they were never written for.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db, model_resolution
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import postgrest_get, rpc
from . import runner_socket
from .llm_gateway import module_env

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent-sessions", tags=["agent-sessions"])

# US-78.10 AC3: how long an open session may sit with a silent agent before the
# factory closes it. Stated in the UI before it bites, so a manager is never
# surprised by a session that vanished.
IDLE_TIMEOUT_MINUTES = 30

# US-83.2: a session key outlives a run key (runs re-mint per run; a session
# mints once at open). Twelve hours covers a working day of conversation; the
# idle sweep closes an abandoned one long before this matters.
SESSION_KEY_TTL_SECONDS = 12 * 3600


class OpenBody(BaseModel):
    worker_id: str
    project_id: str


async def _require_develop(settings: Settings, user: AuthUser, org_id: str) -> None:
    """Opening a session spends model budget on a pool machine, so it takes the
    same capability as dispatching work — not merely being able to see the org."""
    try:
        ok = await rpc(
            settings, user.token, "has_org_capability", {"p_org": org_id, "p_capability": "develop"}
        )
    except Exception:  # noqa: BLE001
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="You cannot run work in this workspace.")


@router.post("")
async def open_session(
    body: OpenBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    workers = await postgrest_get(
        settings,
        user.token,
        "workers",
        {"select": "id,name,org_id", "id": f"eq.{body.worker_id}", "limit": "1"},
    )
    if not workers:
        raise HTTPException(status_code=404, detail="That agent does not exist.")
    worker = workers[0]
    org_id = str(worker["org_id"])
    await _require_develop(settings, user, org_id)

    config = await postgrest_get(
        settings,
        user.token,
        "runner_config",
        {"select": "enabled_modules", "worker_id": f"eq.{body.worker_id}", "limit": "1"},
    )
    modules = (config[0].get("enabled_modules") if config else None) or []
    if "interactive" not in modules:
        # AC: sessions on the four non-interactive modules are out of scope —
        # they run one-shot command lines and have no session to open.
        raise HTTPException(
            status_code=409,
            detail=(
                "Only a Buildmill Interactive Agent holds a session. This agent "
                "runs a one-shot command line, so there is nothing to open."
            ),
        )
    if not runner_socket.is_worker_live(body.worker_id):
        raise HTTPException(
            status_code=409, detail="That agent is not connected right now."
        )

    session = db.open_agent_session(
        settings,
        org_id=org_id,
        project_id=body.project_id,
        worker_id=body.worker_id,
        created_by_auth_user=user.id,
    )
    if session is None:
        raise HTTPException(
            status_code=409,
            detail="That agent already holds a session. Close it before opening another.",
        )

    # US-83.2: the session's CLI gets the same env a run gets — a real scoped
    # key against the gateway, built by the same module_env. The old
    # `session_model_env` was a stub ({"model": ...}), so no key was ever
    # minted, the CLI would have started credential-less, and the runner's
    # no-model refusal (keyed on GROK_MODELS_BASE_URL) could never fire.
    #
    # us-116.1: WHICH model is the run resolver's answer, asked about a kind
    # the agent actually claims — not `model_overrides.code` read in isolation,
    # which refused an Architect with six roles pinned for lacking a model for
    # work it is configured never to do.
    config = db.get_runner_config(settings, body.worker_id)
    inputs = model_resolution.load_inputs(settings, org_id, config=config)
    picked = model_resolution.resolve_session(inputs)
    model = picked.model or ""
    if not model:
        # US-78.5's rule, applied before anything spawns on the machine. The
        # refusal names the agent, the roles it tried and every place a model
        # can come from — never a control that is not on the page.
        db.finish_agent_session(
            settings, session["id"], "failed", error="no model configured"
        )
        raise HTTPException(
            status_code=409,
            detail=model_resolution.no_model_refusal(
                str(worker.get("name") or ""), picked.tried, inputs.org_default
            ),
        )
    # AC3: which kind the conversation resolved through, on the row — so "why
    # is this session using Sonnet" has an answer that is not a guess.
    db.record_session_model(settings, session["id"], model, picked.kind)
    try:
        key = db.mint_gateway_key(
            settings,
            org_id,
            body.worker_id,
            session_id=session["id"],
            route="session",
            model=model,
            # US-60.1's rule, mirrored from gateway.mint: whose credential
            # answers is the agent's own config, never the caller's request.
            platform_billed=(config or {}).get("claude_billing") == "platform",
            ttl_seconds=SESSION_KEY_TTL_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 — no credential, no session
        db.finish_agent_session(settings, session["id"], "failed", error=str(e)[:500])
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not obtain model credentials for this session — a "
                "factory-side fault; nothing ran on the agent's machine. "
                "Try again once the gateway is healthy."
            ),
        )
    base = (getattr(settings, "api_base_url", "") or "").rstrip("/")
    model_env = module_env(
        "xai", f"{base}/api/v1/llm-gateway", key, model, module="interactive"
    )

    try:
        reply = await runner_socket.request_from_worker(
            body.worker_id,
            "session.open",
            {
                "session_id": session["id"],
                "project_id": body.project_id,
                "git_remote_url": session.get("git_remote_url"),
                "acp_session_id": session.get("acp_session_id"),
                "model_env": model_env,
            },
            timeout=300,
        )
    except Exception as e:  # noqa: BLE001
        db.finish_agent_session(settings, session["id"], "failed", error=str(e)[:500])
        raise HTTPException(status_code=502, detail=f"The agent could not open a session: {e}")

    if not (reply or {}).get("ok"):
        error = (reply or {}).get("error") or "the agent refused"
        db.finish_agent_session(settings, session["id"], "failed", error=error)
        raise HTTPException(status_code=502, detail=error)

    db.mark_agent_session_open(
        settings,
        session["id"],
        acp_session_id=reply.get("acp_session_id"),
        workspace_path=reply.get("workspace_path"),
    )
    return {
        "id": session["id"],
        "status": "open",
        "idle_timeout_minutes": IDLE_TIMEOUT_MINUTES,
    }


async def sweep_idle_sessions(settings: Settings) -> int:
    """US-83.3 AC1: the 30-minute promise, kept.

    `db.idle_agent_sessions` existed from US-78.10 with ZERO callers — the UI
    stated "closes itself after 30 minutes of silence" and nothing did it, so
    a forgotten session held its agent forever and the one-live-session index
    then refused every new session on that worker. This runs on the API's
    liveness sweep (60s cadence) and closes through the exact path the Close
    button uses: ask the machine, then finish the row regardless — a session
    row left open against a process nobody can reach is how an agent stays
    held forever.
    """
    import asyncio

    try:
        rows = await asyncio.to_thread(
            db.idle_agent_sessions, settings, IDLE_TIMEOUT_MINUTES
        )
    except Exception as e:  # noqa: BLE001 — the sweep must survive
        logger.warning("idle session sweep could not list sessions: %s", e)
        return 0
    closed = 0
    for row in rows:
        session_id = str(row["id"])
        worker_id = str(row.get("worker_id") or "")
        if worker_id and runner_socket.is_worker_live(worker_id):
            try:
                await runner_socket.request_from_worker(
                    worker_id, "session.close", {"session_id": session_id}, timeout=60
                )
            except Exception as e:  # noqa: BLE001 — the row must close regardless
                logger.warning(
                    "idle sweep: session.close did not reach worker %s: %s",
                    worker_id,
                    e,
                )
        try:
            await asyncio.to_thread(
                db.finish_agent_session, settings, session_id, "closed"
            )
            closed += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("idle sweep could not close session %s: %s", session_id, e)
    return closed


@router.post("/{session_id}/close")
async def close_session(
    session_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_sessions",
        {"select": "id,org_id,worker_id,status", "id": f"eq.{session_id}", "limit": "1"},
    )
    if not rows:
        raise HTTPException(status_code=404, detail="That session does not exist.")
    row = rows[0]
    await _require_develop(settings, user, str(row["org_id"]))

    worker_id = str(row.get("worker_id") or "")
    workspace = "preserved"
    if worker_id and runner_socket.is_worker_live(worker_id):
        try:
            reply = await runner_socket.request_from_worker(
                worker_id, "session.close", {"session_id": session_id}, timeout=60
            )
            workspace = (reply or {}).get("workspace") or workspace
        except Exception as e:  # noqa: BLE001 — the row must close regardless
            logger.warning("session.close did not reach worker %s: %s", worker_id, e)
    # Closed in the database whatever the machine said. A session row left open
    # against a process nobody can reach is how an agent stays held forever.
    db.finish_agent_session(settings, session_id, "closed")
    return {"id": session_id, "status": "closed", "workspace": workspace}
