"""The interactive console (US-78.8): a manager, attached to a running agent.

    browser WebSocket → here → the EXISTING runner control socket → the ACP
    session the interactive module is holding.

Two directions, deliberately asymmetric:

  * **out** — the run's trace, replayed from the top on attach and then
    streamed. It is the same `run_trace` the run page already renders; nothing
    is invented for the console, so what the manager watches live and what the
    factory stored are the same rows.
  * **in** — `prompt`, `permission` or `cancel`, relayed to the worker as
    `session.input`. This is the first path in this system from a person to a
    running agent: clarifications are agent-initiated and asynchronous, and the
    server terminal is SSH to the machine, not a channel into the supervisor's
    own child process.

Attaching is read-only until the manager types. Detaching leaves the run going;
closing the tab kills nothing, because the session lives on the pool machine and
not in the browser.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import jwt
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from ..auth import AuthUser
from ..config import Settings, get_settings
from ..errors import safe_accept
from ..supabase import postgrest_get
from . import runner_socket

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runs", tags=["run-console"])

# How much of the trace an attaching console is given before live streaming
# starts. Enough to read the run so far; not the whole of a long one.
REPLAY_LIMIT = 300


async def _authenticate_ws(settings: Settings, token: str) -> AuthUser:
    """Same handshake as the server terminal: browsers cannot set WS headers,
    so the credential arrives in the first frame and never in the URL."""
    from ..auth import get_signing_key

    try:
        signing_key = get_signing_key(token, settings)
        claims = jwt.decode(
            token, signing_key, algorithms=["ES256", "RS256"], audience="authenticated"
        )
    except Exception:
        raise ValueError("invalid token")
    return AuthUser(id=claims["sub"], email=claims.get("email", ""), token=token)


async def _load_run(settings: Settings, user: AuthUser, run_id: str) -> dict[str, Any] | None:
    """The run, read under the caller's own token — so RLS decides whether this
    manager may see it, rather than this file re-deciding it."""
    rows = await postgrest_get(
        settings,
        user.token,
        "runs",
        {
            "select": "id,status,worker_id,issue_id,org_id",
            "id": f"eq.{run_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def _replay(settings: Settings, user: AuthUser, run_id: str) -> list[dict]:
    rows = await postgrest_get(
        settings,
        user.token,
        "run_trace",
        {
            # `at`, not `created_at`: that is the column `run_trace` actually
            # has (migration 121), and asking for the other one made every
            # attach 500 before a single line was ever shown.
            "select": "kind,content,at",
            "run_id": f"eq.{run_id}",
            "order": "at.asc",
            "limit": str(REPLAY_LIMIT),
        },
    )
    # `stage:` lines are timing instrumentation the run page already strips;
    # they are noise in a transcript.
    return [r for r in rows if not str(r.get("content") or "").startswith("stage:")]


async def _load_session(
    settings: Settings, user: AuthUser, session_id: str
) -> dict[str, Any] | None:
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_sessions",
        {
            "select": "id,status,worker_id,org_id",
            "id": f"eq.{session_id}",
            "limit": "1",
        },
    )
    return rows[0] if rows else None


async def _replay_session(
    settings: Settings, user: AuthUser, session_id: str
) -> list[dict]:
    rows = await postgrest_get(
        settings,
        user.token,
        "agent_session_events",
        {
            "select": "kind,content,at",
            "session_id": f"eq.{session_id}",
            "order": "at.asc",
            "limit": str(REPLAY_LIMIT),
        },
    )
    return [r for r in rows if not str(r.get("content") or "").startswith("stage:")]


@router.websocket("/sessions/{session_id}/console")
async def session_console(
    websocket: WebSocket,
    session_id: str,
    settings: Settings = Depends(get_settings),
):
    """US-78.10: the same console, over a session instead of a run.

    Deliberately the same socket protocol and the same client component — a
    session is a different owner of the conversation, not a different kind of
    conversation, and a second console would drift from this one.
    """
    if not await safe_accept(websocket):
        return
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        token = (json.loads(first) or {}).get("token", "")
    except Exception:
        await websocket.close(code=4401)
        return
    try:
        user = await _authenticate_ws(settings, token)
    except ValueError:
        await websocket.send_json({"type": "error", "message": "Authentication failed."})
        await websocket.close(code=4401)
        return

    session = await _load_session(settings, user, session_id)
    if session is None:
        await websocket.send_json({"type": "error", "message": "Session not found."})
        await websocket.close(code=4404)
        return

    worker_id = str(session.get("worker_id") or "")
    live = bool(worker_id) and runner_socket.is_worker_live(worker_id)
    await websocket.send_json(
        {
            "type": "attached",
            "runId": session_id,
            "status": session.get("status"),
            "steerable": live and session.get("status") in ("open", "opening"),
            "trace": await _replay_session(settings, user, session_id),
        }
    )
    await _serve_console(settings, websocket, session_id, worker_id, user)


@router.websocket("/{run_id}/console")
async def run_console(
    websocket: WebSocket,
    run_id: str,
    settings: Settings = Depends(get_settings),
):
    if not await safe_accept(websocket):
        return
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        token = (json.loads(first) or {}).get("token", "")
    except Exception:
        await websocket.close(code=4401)
        return

    try:
        user = await _authenticate_ws(settings, token)
    except ValueError:
        await websocket.send_json({"type": "error", "message": "Authentication failed."})
        await websocket.close(code=4401)
        return

    run = await _load_run(settings, user, run_id)
    if run is None:
        # RLS answered nothing: either it does not exist or this manager may
        # not see it, and the console must not distinguish the two.
        await websocket.send_json({"type": "error", "message": "Run not found."})
        await websocket.close(code=4404)
        return

    worker_id = str(run.get("worker_id") or "")
    live = bool(worker_id) and runner_socket.is_worker_live(worker_id)
    await websocket.send_json(
        {
            "type": "attached",
            "runId": run_id,
            "status": run.get("status"),
            "steerable": live and run.get("status") == "running",
            "trace": await _replay(settings, user, run_id),
        }
    )

    await _serve_console(settings, websocket, run_id, worker_id, user)


async def _serve_console(
    settings: Settings,
    websocket: WebSocket,
    conversation_id: str,
    worker_id: str,
    user: AuthUser,
) -> None:
    """Stream events out and relay input in, for a run or a session alike.

    The id is whichever the caller attached to — the runner keys its live
    sessions the same way, so one implementation serves both rather than two
    that can drift.
    """
    queue: asyncio.Queue = asyncio.Queue()
    runner_socket.attach_console(conversation_id, queue)
    # Assigned before the try, or a failure on this very line would leave the
    # name unbound and the `finally` below would raise NameError over it —
    # hiding the real error behind a teardown one.
    pump = asyncio.ensure_future(_pump_out(websocket, queue))
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            await _handle_input(
                settings, websocket, conversation_id, worker_id, user, msg
            )
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — a console must never take a run with it
        logger.warning("console failed for %s", conversation_id, exc_info=True)
    finally:
        runner_socket.detach_console(conversation_id, queue)
        pump.cancel()


async def _pump_out(websocket: WebSocket, queue: asyncio.Queue) -> None:
    while True:
        event = await queue.get()
        try:
            await websocket.send_json(event)
        except Exception:  # noqa: BLE001 — the reader loop owns the teardown
            return


async def _handle_input(
    settings: Settings,
    websocket: WebSocket,
    run_id: str,
    worker_id: str,
    user: AuthUser,
    msg: dict,
) -> None:
    action = str(msg.get("action") or "")
    if action not in ("prompt", "cancel", "permission"):
        return
    if not worker_id or not runner_socket.is_worker_live(worker_id):
        await websocket.send_json(
            {
                "type": "refused",
                "message": "This run is not connected — nothing is listening.",
            }
        )
        return
    try:
        reply = await runner_socket.request_from_worker(
            worker_id,
            "session.input",
            {
                "run_id": run_id,
                "action": action,
                "text": msg.get("text"),
                "author": user.email or user.id,
            },
            timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        await websocket.send_json({"type": "refused", "message": str(e)[:300]})
        return
    if not (reply or {}).get("ok"):
        await websocket.send_json(
            {
                "type": "refused",
                "message": (reply or {}).get("error") or "The agent did not accept that.",
            }
        )
