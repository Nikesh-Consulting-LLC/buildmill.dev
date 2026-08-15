"""Supervisor-runner control socket (US-10.1).

A supervisor runner (apps/runner) holds one persistent WebSocket to the API,
framed as JSON-RPC 2.0. This story establishes the channel: token handshake,
presence (`runner_sessions`), and heartbeat. Config push (US-10.2), the LLM
inference relay (US-10.3), and the command-audit / policy plane (US-10.7) add
methods on this same socket without changing the transport.

The runner authenticates with its registry token (US-3.1) — the same value it
sends as `X-Worker-Token` on the HTTP pool contract. Browsers never open this
socket; only the operator-side Python app does, so the token rides the
`X-Worker-Token` header (with a `params.token` fallback for clients that can't
set headers). Nothing here dispatches work — work is still pulled over HTTP.
"""

import asyncio
import json
import logging
import re
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, ConfigDict

from .. import agent_provision, db, llm, runner_policy, workspace_prep
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..errors import safe_accept
from ..supabase import RpcError, rpc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/runner", tags=["runner"])

# Seconds to send the opening `runner.hello` before we drop the socket.
HELLO_TIMEOUT = 15

# Live control sockets by worker id — lets the config PATCH push to a runner
# connected in THIS process. Single-process assumption (no queue infra, per
# ARCHITECTURE); a multi-worker deploy would move this to LISTEN/NOTIFY.
_LIVE: dict[str, WebSocket] = {}

# Server-initiated requests awaiting the runner's reply (the symmetric half
# of the runner's own llm.infer-style requests, which the API answers
# inline in _dispatch). Keyed by a request id this process minted; resolved
# by _dispatch when a reply frame with a matching id arrives on the same
# worker's socket. Single-process, same caveat as _LIVE above.
_PENDING: dict[str, "asyncio.Future[Any]"] = {}
_req_seq = 0

# US-78.8: browser consoles attached to a run, by run id. Same single-process
# assumption as _LIVE above, and the same migration path if that ever changes.
#
# Fed from the `run.trace` handler rather than from a second socket method: the
# console shows the run's trace, so giving it its own event stream would mean
# two descriptions of one run that could disagree about what the agent said.
_CONSOLES: dict[str, set["asyncio.Queue[Any]"]] = {}


def is_worker_live(worker_id: str) -> bool:
    """Whether this worker holds a control socket on this process right now."""
    return str(worker_id) in _LIVE


def attach_console(run_id: str, queue: "asyncio.Queue[Any]") -> None:
    _CONSOLES.setdefault(str(run_id), set()).add(queue)


def detach_console(run_id: str, queue: "asyncio.Queue[Any]") -> None:
    """Detaching leaves the run going — this drops a listener, nothing else."""
    listeners = _CONSOLES.get(str(run_id))
    if not listeners:
        return
    listeners.discard(queue)
    if not listeners:
        _CONSOLES.pop(str(run_id), None)


def broadcast_to_consoles(run_id: str, event: dict[str, Any]) -> None:
    """Fan one trace line out to every attached console. Never blocks and never
    raises: a console that cannot keep up loses a line, and a run does not."""
    for queue in list(_CONSOLES.get(str(run_id), ())):
        try:
            queue.put_nowait(event)
        except Exception:  # noqa: BLE001
            pass


async def push_to_worker(worker_id: str, message: dict[str, Any]) -> bool:
    """Send a server→runner frame to a connected runner; False if it's offline."""
    ws = _LIVE.get(str(worker_id))
    if ws is None:
        return False
    try:
        await ws.send_text(json.dumps(message))
        return True
    except Exception:  # noqa: BLE001 — a dead socket is treated as offline
        return False


async def request_from_worker(
    worker_id: str,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: float = 90,
) -> Any:
    """Server→runner request/reply — e.g. "prepare this project's workspace
    and tell me what you got". Raises RuntimeError if the runner isn't
    connected or answers an error, TimeoutError if it never replies."""
    global _req_seq
    ws = _LIVE.get(str(worker_id))
    if ws is None:
        raise RuntimeError("runner is not connected")
    _req_seq += 1
    req_id = f"srv-{_req_seq}"
    fut: "asyncio.Future[Any]" = asyncio.get_running_loop().create_future()
    _PENDING[req_id] = fut
    try:
        await ws.send_text(
            json.dumps(
                {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
            )
        )
        return await asyncio.wait_for(fut, timeout)
    finally:
        _PENDING.pop(req_id, None)


async def push_config_update(settings: Settings, worker_id: str) -> bool:
    """Push the runner's current server-side config to it, if it's connected.

    US-26.5 pauses and resumes a runner by writing `runner_config.paused` and
    calling this, so a connected supervisor stops pulling immediately rather
    than at its next poll.
    """
    config = db.get_runner_config(settings, str(worker_id))
    return await push_to_worker(
        worker_id,
        {"jsonrpc": "2.0", "method": "config.update", "params": {"config": config}},
    )


def _result(req_id: Any, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _error(req_id: Any, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


# US-32.4: the canonical setting names a module may declare. Kept in step with
# `apps/runner/supervisor/modules/base.py::KNOWN_SETTINGS` — the runner is the
# source of truth for what its modules accept, but a hello is untrusted input,
# and a declaration naming something nothing else in the system understands
# would render as a field that saves and never arrives anywhere.
# US-47.1 dropped `permission_mode` from both sides. An older runner still
# declaring it is the normal case for a while, and the drop-what-we-do-not-know
# rule above is exactly right for it: the knob stops rendering, the module's
# other settings keep working, and nothing has to be upgraded in lockstep.
KNOWN_SETTINGS = (
    "model",
    "fallback_model",
    "effort",
    "max_turns",
    "standing_instructions",
    "mcp",
    # US-52.1: the claude module declares `auth` (api | subscription). Since
    # US-53.1 it is a CAPABILITY signal only — "this runner's supervisor
    # understands subscription mode" — never a resolvable run setting; the
    # value lives on runner_config.claude_billing.
    "auth",
)
SETTING_DELIVERIES = ("argv", "env", "prompt", "runner")

# US-53.1: the two billing modes, validated where the setting now lives (the
# runner-config PATCH). Pinned against the module's declared choices by
# test_module_declarations.
# US-60.1: `platform` bills the superadmin's own key — refused unless
# `buildmill` is the sole enabled module (enforced in the PATCH handler,
# where the resolved module set is known).
AUTH_MODES = ("api", "subscription", "platform")
SETTING_KINDS = ("text", "int", "number", "enum", "bool")


def _module_settings(reported: Any) -> list[dict[str, Any]]:
    """Normalize the declarations a runner reported at hello.

    A hello arrives over a socket the machine owns, so this keeps only the
    shape the settings page renders and drops anything it does not recognise —
    quietly, because an unknown knob is a newer runner talking to an older
    server, not an attack, and the modules it *did* declare properly should
    still be configurable.
    """
    if not isinstance(reported, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in reported[:32]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("module") or "").strip()[:40]
        if not name:
            continue
        knobs = []
        for knob in (entry.get("settings") or [])[:32]:
            if not isinstance(knob, dict):
                continue
            key = str(knob.get("name") or "")
            if key not in KNOWN_SETTINGS:
                continue
            kind = str(knob.get("kind") or "text")
            delivery = str(knob.get("delivery") or "argv")
            knobs.append(
                {
                    "name": key,
                    "kind": kind if kind in SETTING_KINDS else "text",
                    "delivery": (
                        delivery if delivery in SETTING_DELIVERIES else "argv"
                    ),
                    "flag": str(knob.get("flag") or "")[:60],
                    "choices": [
                        str(c)[:40] for c in (knob.get("choices") or [])[:20]
                    ],
                    "help": str(knob.get("help") or "")[:400],
                }
            )
        out.append(
            {
                "module": name,
                "capabilities": [
                    str(c)[:20] for c in (entry.get("capabilities") or [])[:20]
                ],
                "needs_repo": bool(entry.get("needs_repo", True)),
                "settings": knobs,
            }
        )
    return out


@router.websocket("/socket")
async def runner_socket(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
):
    if not await safe_accept(websocket):
        return

    # --- Handshake: the first frame must be a `runner.hello` request. -------
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=HELLO_TIMEOUT)
        hello = json.loads(raw)
    except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect):
        await websocket.close(code=4400, reason="expected a runner.hello frame")
        return

    if not isinstance(hello, dict) or hello.get("method") != "runner.hello":
        await websocket.close(code=4400, reason="first frame must be runner.hello")
        return

    params = hello.get("params") or {}
    token = websocket.headers.get("x-worker-token") or params.get("token") or ""
    worker = db.get_worker_by_token(settings, token)
    if not worker:
        await websocket.send_text(
            _error(hello.get("id"), 4401, "invalid or revoked worker token")
        )
        await websocket.close(code=4401, reason="invalid or revoked worker token")
        return

    worker_id = str(worker["id"])
    session_id = db.open_runner_session(
        settings,
        worker_id=worker_id,
        org_id=str(worker["org_id"]),
        host_info=params.get("host_info") or {},
        agent_versions=params.get("agent_versions") or {},
        modules_available=params.get("modules_available") or [],
        # US-32.4: what each of those modules accepts, kept with the session so
        # the settings page can be honest about a module while the machine is
        # offline.
        module_settings=_module_settings(params.get("module_settings")),
    )
    config = db.get_runner_config(settings, worker_id)
    _LIVE[worker_id] = websocket
    # The hello result carries the session id AND the server-side config (US-10.2).
    await websocket.send_text(
        _result(hello.get("id"), {"session_id": session_id, "config": config})
    )
    logger.info("runner %s connected (session %s)", worker.get("name"), session_id)

    # --- Control loop -------------------------------------------------------
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                await _dispatch(settings, websocket, worker, session_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        if _LIVE.get(worker_id) is websocket:
            _LIVE.pop(worker_id, None)
        db.close_runner_session(settings, session_id)
        logger.info(
            "runner %s disconnected (session %s)", worker.get("name"), session_id
        )


async def _dispatch(
    settings: Settings,
    websocket: WebSocket,
    worker: dict[str, Any],
    session_id: str,
    msg: dict[str, Any],
) -> None:
    """Handle one runner→server frame. `id` present = request (needs a reply);
    absent = notification. US-10.2+ register more methods here."""
    method = msg.get("method")
    req_id = msg.get("id")

    # A reply to a server-initiated request (workspace.prepare, etc.) has no
    # `method` — just id + (result | error) — so it never matches a method
    # branch below. Resolve the future request_from_worker is awaiting.
    if method is None and req_id is not None and req_id in _PENDING:
        fut = _PENDING.get(req_id)
        if fut is not None and not fut.done():
            if "error" in msg:
                fut.set_exception(
                    RuntimeError((msg.get("error") or {}).get("message", "rpc error"))
                )
            else:
                fut.set_result(msg.get("result"))
        return

    if method == "heartbeat":
        db.touch_runner_session(settings, session_id)
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "llm.infer":
        # Brain inference relay (US-10.3): the runner reasons through the
        # server's Vault-keyed provider — no model key on the machine.
        params = msg.get("params") or {}
        route = params.get("route") or "runner_brain"
        messages = params.get("messages") or []
        try:
            result = await llm.complete_as_org(
                settings,
                str(worker["org_id"]),
                route,
                messages=messages,
                temperature=params.get("temperature"),
            )
            payload = {
                "completion": result.text,
                "model": result.model,
                "provider": result.provider_name,
                "used_fallback": result.used_fallback,
            }
            if req_id is not None:
                await websocket.send_text(_result(req_id, payload))
        except Exception as e:  # noqa: BLE001 — relay errors go back as JSON-RPC
            if req_id is not None:
                await websocket.send_text(
                    _error(req_id, 5000, f"llm.infer failed: {e}")
                )
        return

    if method == "gateway.mint":
        # Mint a short-lived scoped gateway key for a run/route (US-10.3/10.6);
        # the runner injects it as the module's provider key.
        params = msg.get("params") or {}
        # US-36.1: guarded like `llm.infer` above. Unguarded, any database blip
        # here escapes the control loop and closes the socket — and because
        # claiming is HTTP while minting is this socket, the agent goes right on
        # taking work it can no longer do. That is how one bad insert cost five
        # runs on 2026-07-27.
        try:
            # US-60.1: stamp whether this run bills the platform's own key —
            # decided by the agent's own config, the same source `run_job`'s
            # billing record already reads, never by the caller's request.
            worker_config = db.get_runner_config(settings, str(worker["id"]))
            key = db.mint_gateway_key(
                settings,
                str(worker["org_id"]),
                str(worker["id"]),
                run_id=params.get("run_id"),
                route=params.get("route") or "runner_brain",
                # US-27.8: the model this agent is configured to use for this
                # run kind. `route` is `runner_code`/`runner_plan`, which are
                # not keys in LLM_FUNCTIONS and route nowhere — the model is
                # what tells the gateway which provider should answer.
                model=params.get("model"),
                platform_billed=worker_config.get("claude_billing") == "platform",
            )
        except Exception as e:  # noqa: BLE001 — the runner decides what to do
            logger.warning("gateway.mint failed for %s: %s", worker.get("name"), e)
            if req_id is not None:
                await websocket.send_text(
                    _error(req_id, 5001, f"gateway.mint failed: {e}")
                )
            return
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"key": key}))
        return

    if method == "subscription.token":
        # US-52.2: the factory-held Claude subscription token, for a run whose
        # auth mode is `subscription`. The reply is the ONLY place the token
        # transits; nothing here may log it — the warning below names the
        # failure, which by construction happened before any secret was read
        # or is a vault lookup error carrying ids, not key material. `token`
        # is null when the org holds none, and the runner then falls back to
        # the machine-held credential (us-52.1).
        try:
            token = llm.read_claude_subscription_token(
                settings, str(worker["org_id"])
            )
        except Exception as e:  # noqa: BLE001 — the runner decides what to do
            logger.warning(
                "subscription.token lookup failed for %s: %s", worker.get("name"), e
            )
            if req_id is not None:
                await websocket.send_text(
                    _error(req_id, 5002, f"subscription.token failed: {e}")
                )
            return
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"token": token}))
        return

    if method == "run.trace":
        # US-27.12: the supervisor's own account of a repair attempt — what it
        # changed and what happened. Two attempts that changed nothing and
        # failed identically were invisible on 2026-07-26. Best-effort and
        # never acknowledged as an error: a trace that cannot be written must
        # not cost a run.
        #
        # US-36.1: that last sentence was a comment, not code. The default kind
        # was `note`, which `run_trace_kind_check` does not permit, so every
        # insert raised, the exception escaped this unguarded handler, and the
        # control socket died — five runs, one per reconnect. Both halves are
        # fixed: a legal default, and the guard the comment always promised.
        params = msg.get("params") or {}
        run_id = params.get("run_id")
        content = (params.get("content") or "").strip()
        if run_id and content:
            try:
                db.record_run_trace(
                    settings,
                    str(run_id),
                    str(worker["id"]),
                    params.get("kind") or db.DEFAULT_RUN_TRACE_KIND,
                    content[:4000],
                )
            except Exception as e:  # noqa: BLE001 — a trace must never cost a run
                logger.warning(
                    "run.trace could not be written for run %s: %s", run_id, e
                )
            # US-78.8: attached consoles see it now, rather than on the next
            # realtime refresh. Outside the try above on purpose — a trace that
            # could not be stored is still worth showing the person watching.
            broadcast_to_consoles(
                str(run_id),
                {
                    "type": "trace",
                    "kind": params.get("kind") or db.DEFAULT_RUN_TRACE_KIND,
                    "content": content[:4000],
                },
            )
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "session.trace":
        # US-78.10: the same transcript line a run sends as `run.trace`, from a
        # session that has no run to hang it on. Best-effort for the same
        # reason: a line that cannot be stored must not cost the session.
        params = msg.get("params") or {}
        session_id = params.get("session_id")
        content = (params.get("content") or "").strip()
        if session_id and content:
            try:
                db.record_agent_session_event(
                    settings,
                    str(session_id),
                    params.get("kind") or db.DEFAULT_RUN_TRACE_KIND,
                    content[:4000],
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "session.trace could not be written for %s: %s", session_id, e
                )
            broadcast_to_consoles(
                str(session_id),
                {
                    "type": "trace",
                    "kind": params.get("kind") or db.DEFAULT_RUN_TRACE_KIND,
                    "content": content[:4000],
                },
            )
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "session.failed":
        # US-83.3 AC2: the runner's watchdog reports a session whose CLI died.
        # The row closes NOW rather than idling until the sweep — and only a
        # session this worker actually holds is believed (the worker_id guard
        # in finish_agent_session), so no agent can fail another's session.
        params = msg.get("params") or {}
        session_id = params.get("session_id")
        if session_id:
            try:
                db.finish_agent_session(
                    settings,
                    str(session_id),
                    "failed",
                    error=(params.get("error") or "the agent process died")[:500],
                    worker_id=str(worker["id"]),
                )
            except Exception as e:  # noqa: BLE001 — the sweep is the backstop
                logger.warning(
                    "session.failed could not be recorded for %s: %s", session_id, e
                )
            broadcast_to_consoles(
                str(session_id),
                {
                    "type": "trace",
                    "kind": "error",
                    "content": (params.get("error") or "the agent process died")[:500],
                },
            )
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "runner.incident":
        # US-31.1: the runner reports a fault of its own — today, a hand-back
        # the API refused — so the failure is visible in the app instead of
        # only in the machine's journal. Best-effort: recording an incident
        # must never cost the runner anything.
        params = msg.get("params") or {}
        try:
            db.record_runner_incident(
                settings,
                str(worker["org_id"]),
                str(worker["id"]),
                params.get("run_id"),
                (params.get("kind") or "runner-fault")[:40],
                (params.get("message") or "").replace("\x00", ""),
            )
            db.notify_org_managers(
                settings,
                str(worker["org_id"]),
                "runner_fault",
                {
                    "worker": worker.get("name"),
                    "run_id": params.get("run_id"),
                    "message": (params.get("message") or "")[:200],
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning("runner.incident recording failed for %s", worker.get("id"))
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "command.audit":
        # Policy check + audit record BEFORE the runner executes (US-10.7).
        params = msg.get("params") or {}
        argv = params.get("argv") or []
        cwd = params.get("cwd")
        config = db.get_runner_config(settings, str(worker["id"]))
        allow, reason = runner_policy.evaluate(config.get("autonomy_policy") or {}, argv)
        audit_id = db.record_command_audit(
            settings,
            str(worker["org_id"]),
            str(worker["id"]),
            session_id,
            params.get("run_id"),
            argv,
            cwd,
            "allow" if allow else "deny",
        )
        if req_id is not None:
            await websocket.send_text(
                _result(req_id, {"allow": allow, "reason": reason, "audit_id": audit_id})
            )
        return

    if method == "command.result":
        # The runner reports a command's exit + output after it ran (US-10.7).
        params = msg.get("params") or {}
        audit_id = params.get("audit_id")
        if audit_id:
            db.finish_command_audit(
                settings, audit_id, params.get("exit_code"), params.get("output")
            )
        return

    if method == "prep.step":
        # US-85.1: one checklist entry of a workspace-preparation job moving.
        # Best-effort like run.trace — a step that cannot be written must not
        # cost the preparation — and scoped to jobs THIS worker owns, because
        # a socket frame is untrusted input.
        params = msg.get("params") or {}
        job_id = params.get("job_id")
        step = params.get("step")
        if job_id and step:
            try:
                await asyncio.to_thread(
                    workspace_prep.set_step,
                    settings,
                    str(job_id),
                    str(step),
                    str(params.get("status") or "running"),
                    str(params.get("detail") or ""),
                    worker_id=str(worker["id"]),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "prep.step could not be written for job %s: %s", job_id, e
                )
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"ok": True}))
        return

    if method == "runner.hello":
        # A duplicate hello (reconnect race) just refreshes presence.
        db.touch_runner_session(settings, session_id)
        if req_id is not None:
            await websocket.send_text(_result(req_id, {"session_id": session_id}))
        return

    if req_id is not None:
        await websocket.send_text(_error(req_id, -32601, f"method not found: {method}"))


# --------------------------------------------------------------------------
# Config write + live push (US-10.2)
# --------------------------------------------------------------------------


class RunnerConfigBody(BaseModel):
    # US-32.3: unknown fields are refused, not dropped. `concurrency` lived
    # here for six phases — validated, stored and pushed over the socket — and
    # the runner never read a single one of them. A client still sending it
    # should hear that it does nothing rather than believe it worked.
    model_config = ConfigDict(extra="forbid")

    enabled_modules: list[str] | None = None
    model_routes: dict[str, Any] | None = None
    # US-32.6: per run kind, `{"preset_id": uuid}` or `{"custom": {...}}`.
    # An absent kind inherits the org's default preset.
    run_routes: dict[str, Any] | None = None
    # US-53.1: how this agent's Claude runs are billed — `api` | `subscription`.
    # One switch on the agent, never a preset/route setting.
    claude_billing: str | None = None
    # US-53.4: the run kinds this agent claims — a checkbox per kind. None =
    # leave unchanged (and a never-set agent means ALL kinds); [] = benched.
    enabled_kinds: list[str] | None = None
    autonomy_policy: dict[str, Any] | None = None
    # US-31.2: minutes one run may hold its claim; -1 clears back to the
    # worker-type default, None leaves it unchanged.
    max_run_minutes: int | None = None
    max_total_run_minutes: int | None = None
    # US-31.5: attempts this agent may spend on one work item.
    max_item_attempts: int | None = None
    # US-66.1: per-kind model this agent pins — org-owned, unlike the six
    # platform-owned fields above; not in `_PLATFORM_OWNED_FIELDS`.
    model_overrides: dict[str, Any] | None = None


# US-13.8: the module registry the checkboxes render from — names must
# match apps/runner/supervisor's built-in modules (US-10.5). `buildmill`
# (US-60.1) is Claude Code under a platform-billed name.
# US-78.3: `interactive` is the Buildmill Interactive Agent — a fork of
# xai-org/grok-build driven over ACP (a persistent session), where every module
# above is a one-shot command line.
KNOWN_MODULES = ("claude", "buildmill", "grok", "opencode", "interactive", "sim")
POLICY_MODES = ("allow", "require-approval", "deny")


# US-27.8: which provider type each CLI module's SDK speaks. Mirrors
# `provider_type` on the supervisor modules (apps/runner/supervisor/modules);
# `sim` needs no provider at all. A module pointed at a model belonging to a
# provider it cannot talk to is a misconfiguration that is knowable at Save,
# and discovering it 90 seconds into a run on a remote machine — as an error
# about a model that exists — is what this map exists to prevent.
#
# US-60.1: `buildmill` is deliberately absent. It never resolves against the
# org's own configured providers at all — it always speaks to the platform's
# own Anthropic key — so this pairing check (which validates a model against
# the ORG's providers) does not apply to it, the same way it does not apply
# to `sim`.
#
# US-78.5: `interactive` is absent for the same reason. Its model is
# platform-owned (`platform_run_config`), so validating it against the tenant's
# providers would refuse a pairing the tenant does not choose and cannot fix.
MODULE_PROVIDER_TYPES = {
    "claude": ("anthropic",),
    "grok": ("xai",),
    # opencode speaks the OpenAI wire format, which Groq also serves.
    "opencode": ("openai", "groq"),
}


def validate_model_provider_pairing(
    enabled_modules: list[str] | None,
    model_routes: dict[str, Any] | None,
    providers: list[dict[str, Any]],
) -> str | None:
    """US-27.8: why this module/model pairing cannot work, or None.

    Shared by the runner console and a host's slot template, so the two say
    the same thing rather than one of them saying nothing."""
    from .llm_gateway import provider_for_model

    modules = [m for m in (enabled_modules or []) if m in MODULE_PROVIDER_TYPES]
    if not modules or not model_routes:
        return None
    for kind, model in sorted((model_routes or {}).items()):
        # `brain` is not a CLI module's run kind — the supervisor's own
        # reasoning goes through llm.infer and is resolved server-side by
        # llm_function_routes. Pairing rules must not reach it, or a fleet on
        # Claude could not think on Groq.
        if kind == "brain":
            continue
        if not isinstance(model, str) or not model.strip():
            continue
        provider = provider_for_model(providers, model)
        if provider is None:
            offered = sorted({m for p in providers for m in (p.get("models") or [])})
            return (
                f"no configured provider offers '{model}' (the {kind} route) — "
                "add it to a provider in Settings → LLM providers"
                + (f". Configured: {', '.join(offered[:8])}" if offered else "")
            )
        ptype = (provider.get("provider_type") or "").lower()
        for module in modules:
            wanted = MODULE_PROVIDER_TYPES[module]
            if ptype not in wanted:
                return (
                    f"the {module} module speaks "
                    f"{' or '.join(wanted)}, but the {kind} route's model "
                    f"'{model}' belongs to "
                    f"'{provider.get('name') or ptype}' ({ptype}) — pick a "
                    f"model from a {' or '.join(wanted)} provider, or enable a "
                    "module that speaks this one"
                )
    return None


# Every dispatchable run kind, matching the database's `runs_kind_check`
# (`brain` is deliberately absent — the supervisor's own reasoning routes
# through Settings → Routing). US-53.4 widened this from the seven it froze
# at in us-32.6: `guidelines` (us-43), `elaborate` (us-44.1) and `wireframe`
# (us-48) were dispatchable but could not be routed or gated.
ROUTE_KINDS = (
    "prd", "breakdown", "plan", "code", "test", "release", "deploy",
    "guidelines", "elaborate", "wireframe", "merge",
)


def _validate_run_routes(routes: dict[str, Any]) -> None:
    """US-32.6: a route is a preset reference or inline custom settings.

    Both shapes are checked here rather than being stored as written, because a
    route that means nothing is a tuning choice that appears to have been made.
    `brain` is deliberately not a route kind: the supervisor's own reasoning
    goes through Settings → Routing, not through a run preset.
    """
    from .. import presets as presets_lib

    for kind, entry in routes.items():
        if kind not in ROUTE_KINDS:
            raise HTTPException(
                status_code=422,
                detail=f"'{kind}' is not a dispatchable run kind — "
                + ", ".join(ROUTE_KINDS),
            )
        if entry is None:
            continue
        if not isinstance(entry, dict):
            raise HTTPException(
                status_code=422,
                detail=f"the {kind} route must be a preset reference or custom "
                "settings",
            )
        has_preset = bool(entry.get("preset_id"))
        has_custom = isinstance(entry.get("custom"), dict)
        if has_preset and has_custom:
            raise HTTPException(
                status_code=422,
                detail=f"the {kind} route is both a preset and custom — pick one",
            )
        if not has_preset and not has_custom:
            raise HTTPException(
                status_code=422,
                detail=f"the {kind} route names neither a preset nor custom "
                "settings; leave it out to inherit the org default",
            )
        if has_preset and not _looks_like_uuid(str(entry["preset_id"])):
            raise HTTPException(
                status_code=422,
                detail=f"the {kind} route's preset id is not an id",
            )
        if has_custom:
            custom = dict(entry["custom"])
            # `model` lives alongside the preset settings in a custom route —
            # it is validated against the org's providers by the pairing check
            # further down, the same one that guards `model_routes`.
            model = custom.pop("model", None)
            if model is not None and not isinstance(model, str):
                raise HTTPException(
                    status_code=422,
                    detail=f"the {kind} route's model must be a model name",
                )
            try:
                presets_lib.clean_settings(custom)
            except presets_lib.PresetInvalid as e:
                raise HTTPException(
                    status_code=422, detail=f"the {kind} route: {e}"
                )


def _looks_like_uuid(value: str) -> bool:
    import uuid as _uuid

    try:
        _uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _validate_config_body(body: RunnerConfigBody) -> None:
    """US-13.8: reject a misconfiguration at write time with the exact
    field and value, instead of letting a typo become a silent no-op
    (runner_policy.evaluate skips unparseable patterns) or a bare DB
    CHECK failure."""
    if body.enabled_modules is not None:
        unknown = [m for m in body.enabled_modules if m not in KNOWN_MODULES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"unknown module(s): {', '.join(unknown)} — "
                f"valid: {', '.join(KNOWN_MODULES)}",
            )
    if body.claude_billing is not None and body.claude_billing not in AUTH_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"claude_billing must be one of: {', '.join(AUTH_MODES)} "
            f"(got '{body.claude_billing}')",
        )
    if body.enabled_kinds is not None:
        bad = [k for k in body.enabled_kinds if k not in ROUTE_KINDS]
        if bad:
            raise HTTPException(
                status_code=422,
                detail=f"unknown run kind(s): {', '.join(bad)} — "
                f"valid: {', '.join(ROUTE_KINDS)}",
            )
    if (
        body.max_total_run_minutes is not None
        and body.max_total_run_minutes != -1
        and not (1 <= body.max_total_run_minutes <= 1440)
    ):
        raise HTTPException(
            status_code=422,
            detail="max_total_run_minutes must be between 1 minute and 1440 (24 hours)",
        )
    if body.max_run_minutes is not None and body.max_run_minutes != -1 and not (
        1 <= body.max_run_minutes <= 1440
    ):
        raise HTTPException(
            status_code=422,
            detail="max_run_minutes must be between 1 minute and 1440 (24 hours)",
        )
    if body.max_item_attempts is not None and not (
        1 <= body.max_item_attempts <= 20
    ):
        raise HTTPException(
            status_code=422,
            detail="max_item_attempts must be between 1 and 20",
        )
    if body.model_routes is not None:
        for k, v in body.model_routes.items():
            if v is not None and not isinstance(v, str):
                raise HTTPException(
                    status_code=422,
                    detail=f"model route '{k}' must be a model name string",
                )
    if body.model_overrides is not None:
        bad_kinds = [k for k in body.model_overrides if k not in ROUTE_KINDS]
        if bad_kinds:
            raise HTTPException(
                status_code=422,
                detail=f"unknown run kind(s): {', '.join(bad_kinds)} — "
                f"valid: {', '.join(ROUTE_KINDS)}",
            )
        for k, v in body.model_overrides.items():
            if v is not None and not isinstance(v, str):
                raise HTTPException(
                    status_code=422,
                    detail=f"model override '{k}' must be a model name string",
                )
    if body.run_routes is not None:
        _validate_run_routes(body.run_routes)
    if body.autonomy_policy is not None:
        pol = body.autonomy_policy
        mode = pol.get("mode", "allow")
        if mode not in POLICY_MODES:
            raise HTTPException(
                status_code=422,
                detail=f"autonomy mode '{mode}' is not one of: "
                + ", ".join(POLICY_MODES),
            )
        for key in ("deny_patterns", "allow_patterns"):
            patterns = pol.get(key) or []
            if not isinstance(patterns, list):
                raise HTTPException(
                    status_code=422, detail=f"{key} must be a list of regexes"
                )
            for pat in patterns:
                try:
                    re.compile(str(pat))
                except re.error as e:
                    raise HTTPException(
                        status_code=422,
                        detail=f"invalid regex in {key}: '{pat}' ({e}) — "
                        "an unparseable pattern would be silently skipped "
                        "at evaluation time",
                    )


def _config_changes(
    before: dict[str, Any], after: dict[str, Any]
) -> list[str]:
    changed = []
    for key in (
        "enabled_modules",
        "model_routes",
        "run_routes",
        "autonomy_policy",
        "max_run_minutes",
        "max_total_run_minutes",
        "max_item_attempts",
        "claude_billing",
        "enabled_kinds",
        "model_overrides",
    ):
        if before.get(key) != after.get(key):
            changed.append(key)
    return changed


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
            status_code=403, detail="Not authorized to configure runners"
        )


# US-57.6: how an agent runs is the platform's to set. `enforce_runner_config_
# platform_fields()` (migration 204) is the hard backstop at the database
# layer; this is the friendly 403 in front of it, checked before the write is
# even attempted, so a normal org PATCH never reaches a raw Postgres error.
_PLATFORM_OWNED_FIELDS = (
    "model_routes", "run_routes", "autonomy_policy",
    "max_run_minutes", "max_total_run_minutes", "max_item_attempts",
)


async def _require_platform_admin_for_platform_fields(
    body: "RunnerConfigBody", user: AuthUser, settings: Settings
) -> None:
    if not any(getattr(body, f) is not None for f in _PLATFORM_OWNED_FIELDS):
        return
    try:
        is_admin = await rpc(settings, user.token, "is_platform_admin", {})
    except RpcError:
        is_admin = False
    if not is_admin:
        raise HTTPException(
            status_code=403,
            detail="How an agent runs is the platform's to set — "
            + ", ".join(_PLATFORM_OWNED_FIELDS),
        )


@router.patch("/{worker_id}/config")
async def update_runner_config(
    worker_id: str,
    body: RunnerConfigBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Update a runner's server-side config and push it live to the runner if
    it's connected. Capability-gated (manage_work); service-role write so the
    live `config.update` can fire regardless of the browser's own RLS."""
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    await _require_platform_admin_for_platform_fields(body, user, settings)
    _validate_config_body(body)
    before = db.get_runner_config(settings, worker_id)
    # US-60.1: `platform` billing is the anti-loophole — without this check
    # any org could set claude_billing=platform on a plain `claude` agent
    # and get free platform-funded API access forever. Saving `buildmill` as
    # the sole module always forces billing to `platform`, silently
    # correcting whatever else was sent — there is no other legal value for
    # it and nothing to choose.
    resolved_modules = (
        body.enabled_modules
        if body.enabled_modules is not None
        else before.get("enabled_modules") or []
    )
    if body.claude_billing == "platform" and resolved_modules != ["buildmill"]:
        raise HTTPException(
            status_code=422,
            detail="claude_billing 'platform' is only available with the "
            "Buildmill Agent module",
        )
    if resolved_modules == ["buildmill"]:
        body.claude_billing = "platform"
    # US-27.8: refuse a module/model disagreement at Save. The pairing is
    # knowable the moment the manager clicks it; discovering it 90 seconds
    # into a run on a remote machine, as an error about a model that exists,
    # is the failure this ends.
    routes = dict(
        body.model_routes
        if body.model_routes is not None
        else before.get("model_routes") or {}
    )
    # US-32.6: a custom route names its own model, and it deserves exactly the
    # same check — a model the enabled module's provider cannot answer is the
    # same misconfiguration whichever field it was typed into.
    run_routes = (
        body.run_routes
        if body.run_routes is not None
        else before.get("run_routes") or {}
    )
    for kind, entry in (run_routes or {}).items():
        model = ((entry or {}).get("custom") or {}).get("model")
        if model:
            routes[kind] = model
    # US-66.1: an agent's own model pin gets the identical check — a model
    # its enabled module's provider cannot answer is the same misconfiguration
    # whichever field it was pinned through.
    model_overrides = (
        body.model_overrides
        if body.model_overrides is not None
        else before.get("model_overrides") or {}
    )
    for kind, model in (model_overrides or {}).items():
        if model:
            routes[kind] = model
    if routes:
        providers, _fn_routes = db.get_org_llm_config(
            settings, str(worker["org_id"])
        )
        problem = validate_model_provider_pairing(
            body.enabled_modules
            if body.enabled_modules is not None
            else before.get("enabled_modules"),
            routes,
            providers,
        )
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    config = db.upsert_runner_config(
        settings,
        worker_id,
        str(worker["org_id"]),
        enabled_modules=body.enabled_modules,
        model_routes=body.model_routes,
        run_routes=body.run_routes,
        autonomy_policy=body.autonomy_policy,
        max_run_minutes=body.max_run_minutes,
        max_total_run_minutes=body.max_total_run_minutes,
        max_item_attempts=body.max_item_attempts,
        claude_billing=body.claude_billing,
        enabled_kinds=body.enabled_kinds,
        model_overrides=body.model_overrides,
    )
    pushed = await push_to_worker(
        worker_id,
        {"jsonrpc": "2.0", "method": "config.update", "params": {"config": config}},
    )
    # US-13.8: saving reports what changed and whether the push landed.
    return {
        "config": config,
        "pushed": pushed,
        "changed": _config_changes(before, config),
    }


class PolicyPreviewBody(BaseModel):
    command: str


@router.get("/{worker_id}/idle-reason")
async def idle_reason(
    worker_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-27.9: why this agent is not working — one of `working`, `revoked`,
    `paused`, `no-grants`, `queue-held`, `idle`.

    "Waiting for work" must only ever mean there is no work. On 2026-07-26 it
    meant a revoked token, for fourteen minutes, on every surface at once."""
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    return db.worker_idle_reason(settings, worker_id)


@router.post("/{worker_id}/policy-preview")
async def policy_preview(
    worker_id: str,
    body: PolicyPreviewBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-13.8: evaluate a command line against the runner's CURRENT
    stored policy — the same runner_policy.evaluate the shell audit path
    uses, so the preview cannot drift from enforcement."""
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    if not body.command.strip():
        raise HTTPException(status_code=422, detail="command is required")
    try:
        import shlex

        argv = shlex.split(body.command)
    except ValueError:
        argv = body.command.split()
    policy = db.get_runner_config(settings, worker_id).get("autonomy_policy") or {}
    allow, reason = runner_policy.evaluate(policy, argv)
    if allow:
        decision = "allow"
    elif reason == "command requires manager approval":
        decision = "hold"
    else:
        decision = "block"
    matched = None
    if reason and reason.startswith("blocked by policy pattern: "):
        matched = reason.removeprefix("blocked by policy pattern: ")
    return {"decision": decision, "reason": reason, "matched_pattern": matched}


class PrepareWorkspaceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str


@router.post("/{worker_id}/prepare-workspace")
async def prepare_workspace(
    worker_id: str,
    body: PrepareWorkspaceBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Manager-triggered test: ask a connected runner to clone/fetch a
    project into its persistent workspace right now, outside any claimed
    run, and report what it got. Proves an agent's ability to reach the
    factory remote and get source onto disk — the same round-trip a real
    run depends on, without waiting for one to be dispatched."""
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    project = db.get_project_repo_by_id(
        settings, body.project_id, str(worker["org_id"])
    )
    if not project or "/" not in (project.get("repo_full_name") or ""):
        raise HTTPException(
            status_code=404,
            detail="project not found, or has no linked GitHub repository",
        )
    remote = (
        f"{settings.api_base_url.rstrip('/')}/git/"
        f"{project['org_shortname']}/{project['slug']}.git"
    )
    try:
        result = await request_from_worker(
            worker_id,
            "workspace.prepare",
            {"project_id": body.project_id, "remote": remote},
            timeout=120,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail="the runner didn't answer in time — it may be busy or stuck",
        )
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"project": project["name"], **(result or {})}


@router.post("/{worker_id}/prepare-workspace-job")
async def prepare_workspace_job(
    worker_id: str,
    body: PrepareWorkspaceBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-85.1: the full preparation, as a background job the popup watches.

    Where `prepare-workspace` above answers one synchronous clone/fetch, this
    creates a `workspace_prep_jobs` row, hands the runner the whole checklist
    (directory, latest code, agent + MCP config, tool servers, verification)
    and returns the job id immediately — progress streams to the row over the
    control socket, and the browser reads it over Realtime. Closing the popup
    cancels nothing.
    """
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    project = db.get_project_repo_by_id(
        settings, body.project_id, str(worker["org_id"])
    )
    if not project or "/" not in (project.get("repo_full_name") or ""):
        raise HTTPException(
            status_code=404,
            detail="project not found, or has no linked GitHub repository",
        )
    if not is_worker_live(worker_id):
        raise HTTPException(
            status_code=409,
            detail="the agent's runner is not connected — there is no machine "
            "to prepare",
        )
    # AC6: an agent mid-run keeps its run — preparation would fight the
    # workspace the run is standing in.
    busy = await asyncio.to_thread(
        agent_provision.worker_is_busy, settings, worker_id
    )
    if busy:
        raise HTTPException(
            status_code=409,
            detail=f"this agent is running {busy.get('title') or busy['id']} — "
            "prepare after it hands back",
        )
    try:
        job = await asyncio.to_thread(
            workspace_prep.create_job,
            settings,
            org_id=str(worker["org_id"]),
            worker_id=worker_id,
            project_id=body.project_id,
            by=user.id,
            by_email=user.email,
        )
    except workspace_prep.JobActive as e:
        live = await asyncio.to_thread(
            workspace_prep.live_job, settings, worker_id, body.project_id
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(e),
                "job_id": str(live["id"]) if live else None,
            },
        )
    remote = (
        f"{settings.api_base_url.rstrip('/')}/git/"
        f"{project['org_shortname']}/{project['slug']}.git"
    )
    payload = {
        "job_id": str(job["id"]),
        "project_id": body.project_id,
        "remote": remote,
        "tool_servers": await asyncio.to_thread(
            workspace_prep.tool_server_bundle, settings, str(worker["org_id"])
        ),
    }
    workspace_prep.launch(settings, str(job["id"]), worker_id, payload)
    return {"job_id": str(job["id"]), "project": project["name"]}


class RunnerCommandBody(BaseModel):
    argv: list[str]
    cwd: str | None = None


@router.post("/{worker_id}/command")
async def run_runner_command(
    worker_id: str,
    body: RunnerCommandBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Manager-initiated command on a connected runner (US-10.7) — e.g. a manual
    repair. Goes through the same audit path on the runner side."""
    worker = db.get_worker(settings, worker_id)
    if not worker:
        raise HTTPException(status_code=404, detail="runner not found")
    await _require_manage_work(str(worker["org_id"]), user, settings)
    pushed = await push_to_worker(
        worker_id,
        {"jsonrpc": "2.0", "method": "command.run", "params": {"argv": body.argv, "cwd": body.cwd}},
    )
    if not pushed:
        raise HTTPException(status_code=409, detail="runner is not connected")
    return {"pushed": True}
