"""Sessions with no work item (US-78.10).

Every ACP session before this one belonged to a run: claimed from the pool,
leased, submitted, reviewed. This opens one directly, so a manager can explore a
codebase or try an approach without first inventing a work item for it.

Server→runner over the control socket, composed onto `on_message` beside
`workspace_prepare` and `session_input`:

  * `session.open`  — prepare the project's checkout, start the CLI, `session/new`
                      (or `session/load` when reopening), reply with the ACP
                      session id and workspace path.
  * `session.close` — end the conversation, stop the child, release the agent.

The conversation itself is the SAME machinery a run uses — the shared ACP
engine (US-83.2), the `LIVE` registry `session_input` types into. A session is
a different owner, not a different mechanism; that is why this file is short.

US-83.2 AC4: narration is decoupled from the protocol. A trace line is an
enqueue; a separate flusher task does the awaited socket sends. Before this,
every `session/update` awaited `connection.notify` inside the notification
handler, so a slow control socket back-pressured the agent's own read loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from . import gitwork, mcpconfig
from .acp import AcpError, describe_update
from .acp.engine import open_session
from .acp.events import Coalescer
from .modules.interactive import LIVE, LiveSession, write_model_config
from .primitives import LocalPrimitives

logger = logging.getLogger("supervisor.session_host")

# What a session leaves behind when it closes. The workspace is PRESERVED (the
# same per-project checkout runs use, us-31.8), so reopening resumes against the
# files the conversation is about. US-78.10 AC7 requires the UI to say which.
WORKSPACE_ON_CLOSE = "preserved"

# US-83.2 AC4: how many narration lines may wait on a slow socket before new
# ones are dropped. Dropping narration is survivable; a stalled agent is not.
NARRATION_QUEUE_SIZE = 256

# Ends the flusher after everything queued before it has been sent.
_CLOSE = object()


class _Narrator:
    """A bounded queue between trace producers and the control socket.

    `emit` is sync and never blocks — the engine's contract. The flusher owns
    every awaited send. `stop()` drains what was already queued (the last
    lines before a failure are the diagnostic), then ends the task.
    """

    def __init__(self, connection: Any, session_id: str):
        self._connection = connection
        self._session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=NARRATION_QUEUE_SIZE)
        self._task = asyncio.create_task(self._flush())

    def emit(self, kind: str, line: str) -> None:
        try:
            self._queue.put_nowait((kind, line[:4000]))
        except asyncio.QueueFull:
            logger.debug("session narration dropped: queue full")

    async def _flush(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _CLOSE:
                return
            kind, line = item
            try:
                await self._connection.notify(
                    "session.trace",
                    {"session_id": self._session_id, "kind": kind, "content": line},
                )
            except Exception:  # noqa: BLE001 — narration must never take the session
                logger.debug("session trace dropped", exc_info=True)

    async def stop(self) -> None:
        try:
            await self._queue.put(_CLOSE)
            await asyncio.wait_for(self._task, timeout=10)
        except Exception:  # noqa: BLE001 — a stuck flusher is cancelled, not waited on
            self._task.cancel()


class _Host:
    """One live session's process, client and bookkeeping."""

    def __init__(self, session_id: str, opened, workdir: str, narrator: _Narrator):
        self.session_id = session_id
        self.opened = opened
        self.workdir = workdir
        self.narrator = narrator
        self.opened_at = time.monotonic()

    async def close(self) -> None:
        # US-83.3 AC3: graceful first — measured against grok 1.0.0,
        # `session/close` answers `{"x.ai/closeOutcome": "closed"}` and lets
        # the agent flush its own session state before the process dies.
        # Any failure falls through to the kill; this is a courtesy, not a
        # dependency.
        client = self.opened.client
        if getattr(client, "supports_session_close", False):
            try:
                await client.session_close(self.opened.session_id)
            except Exception:  # noqa: BLE001
                logger.debug("graceful session/close declined", exc_info=True)
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.opened.proc.close()
        except Exception:  # noqa: BLE001
            pass
        await self.narrator.stop()


# session id -> _Host, for the sessions this machine is holding.
HOSTS: dict[str, _Host] = {}


def is_busy() -> bool:
    """Whether a session is holding this agent.

    US-78.10 AC3: a session holds the slot for its life, so the work loop must
    not also claim runs into the same workspace — two conversations editing one
    checkout is the failure this prevents.
    """
    return bool(HOSTS)


async def handle(connection: Any, msg: dict[str, Any]) -> None:
    method = msg.get("method")
    if method not in ("session.open", "session.close"):
        return
    req_id = msg.get("id")
    params = msg.get("params") or {}
    try:
        if method == "session.open":
            result = await _open(connection, params)
        else:
            result = await _close(params)
    except Exception as e:  # noqa: BLE001 — report, never crash the socket
        logger.warning("%s failed: %s", method, e, exc_info=True)
        result = {"ok": False, "error": str(e)[:500]}
    if req_id is not None:
        await connection.reply(req_id, result)


async def _open(connection: Any, params: dict[str, Any]) -> dict[str, Any]:
    session_id = str(params.get("session_id") or "")
    project_id = params.get("project_id")
    git_remote_url = params.get("git_remote_url")
    resume = params.get("acp_session_id") or None
    model_env = params.get("model_env") or {}
    if not session_id or not git_remote_url:
        raise ValueError("session.open needs session_id and git_remote_url")
    if session_id in HOSTS:
        raise ValueError("this session is already open on this machine")

    prim = LocalPrimitives(env=model_env)

    # The same per-project workspace a run uses (us-31.8), so a session and the
    # runs that follow it are looking at the same tree. US-89.1: the remote is
    # clean; the credential helper reads FACTORY_WORKER_TOKEN from this
    # process's env.
    workdir = await gitwork.prepare_checkout(
        prim, str(git_remote_url), f"session-{session_id[:8]}",
        project_id=project_id,
    )

    written = write_model_config(os.environ.get("GROK_HOME", ""), model_env)
    if written is None and model_env.get("GROK_MODELS_BASE_URL"):
        raise AcpError(
            "this agent has no model to reason with — set one on its settings "
            "page before opening a session"
        )

    mcp_written = mcpconfig.write(
        workdir, os.environ.get("FACTORY_API_URL", ""), token, project_id, []
    )

    from .modules.interactive import ACP_ARGS, DEFAULT_CMD
    from .modules.cli_base import split_cmd

    argv = [
        *split_cmd(os.environ.get("RUNNER_INTERACTIVE_CMD", DEFAULT_CMD)),
        *ACP_ARGS,
    ]

    narrator = _Narrator(connection, session_id)
    coalescer = Coalescer()

    async def on_update(update_params: dict) -> None:
        described = describe_update(update_params)
        if described is None:
            return
        for kind, line in coalescer.feed(*described, time.monotonic()):
            narrator.emit(kind, line)

    async def on_permission(tool_call: dict, outcome: str, reason: str) -> None:
        narrator.emit(
            "decision",
            f"permission {outcome} for {tool_call.get('title') or 'a tool'} ({reason})",
        )

    def on_disconnect() -> None:
        # US-83.3 AC2: the child died out from under the session. Scheduled,
        # not awaited — this fires inside the client's read loop teardown.
        asyncio.ensure_future(_reap(connection, session_id))

    try:
        # US-83.2: the exact open sequence a run uses — handshake trace line,
        # tools translation, the refusal when the factory's tools cannot be
        # expressed, resume-or-new. One engine, two owners.
        opened = await open_session(
            prim,
            argv,
            str(workdir),
            mcp_config=str(mcp_written) if mcp_written else None,
            resume_session_id=str(resume) if resume else None,
            emit=narrator.emit,
            on_update=on_update,
            on_permission=on_permission,
            on_disconnect=on_disconnect,
        )
    except Exception:
        # The queued lines are the diagnostic — send them before reporting.
        await narrator.stop()
        raise

    HOSTS[session_id] = _Host(session_id, opened, str(workdir), narrator)

    # Typed into by exactly the same path a run's session is (US-78.8).
    LIVE[session_id] = LiveSession(opened.client, opened.session_id, narrator.emit)

    narrator.emit(
        "step",
        "session opened — workspace "
        + ("reattached" if opened.resumed else "fresh conversation"),
    )
    return {
        "ok": True,
        "acp_session_id": opened.session_id,
        "workspace_path": str(workdir),
    }


async def _reap(connection: Any, session_id: str) -> None:
    """US-83.3 AC2: a dead child releases the agent on its own.

    Before this, a crashed session CLI left `HOSTS`/`LIVE` populated,
    `is_busy()` true, and the work loop refusing claims — a zombie held the
    slot until someone restarted the runner, and (the sweep not existing
    either) the server row stayed open forever. Idempotent against a manager's
    Close and against the sweep: whoever gets there first wins, the rest find
    nothing to do.
    """
    host = HOSTS.pop(session_id, None)
    LIVE.pop(session_id, None)
    if host is None:
        return
    tail = ""
    try:
        tail = host.opened.proc.stderr_tail() or ""
    except Exception:  # noqa: BLE001
        pass
    logger.warning("session %s: the agent process died — releasing", session_id)
    await host.close()
    try:
        await connection.notify(
            "session.failed",
            {
                "session_id": session_id,
                "error": ("the agent process ended unexpectedly. " + tail)[:500],
            },
        )
    except Exception:  # noqa: BLE001 — the slot is free either way
        logger.warning("session.failed did not reach the server", exc_info=True)


async def _close(params: dict[str, Any]) -> dict[str, Any]:
    session_id = str(params.get("session_id") or "")
    host = HOSTS.pop(session_id, None)
    LIVE.pop(session_id, None)
    if host is None:
        # Already gone is the success case, not an error: the server closing a
        # session this machine no longer holds is exactly what a restart looks
        # like, and refusing would leave the row open forever.
        return {"ok": True, "workspace": WORKSPACE_ON_CLOSE, "was_open": False}
    await host.close()
    return {"ok": True, "workspace": WORKSPACE_ON_CLOSE, "was_open": True}


async def close_all() -> None:
    """Every session this machine holds — on shutdown, so a restart does not
    leave rows claiming to be open against a process that is gone."""
    for session_id in list(HOSTS):
        await _close({"session_id": session_id})
