"""The manager's half of an interactive run (US-78.8).

Server→runner over the control socket, composed onto `on_message` the same way
`workspace_prepare` is — ignore every method but this one, so handlers stack
without knowing about each other.

The chain: browser WebSocket → api → this → the live ACP session the
interactive module registered under the run's id. There was no path from a
person to a running agent before this; clarifications are agent-initiated and
asynchronous, and the server terminal is SSH to the machine, not a channel into
the supervisor's own child process.
"""

from __future__ import annotations

import logging
from typing import Any

from .modules.interactive import LIVE

logger = logging.getLogger("supervisor.session_input")


async def handle(connection: Any, msg: dict[str, Any]) -> None:
    if msg.get("method") != "session.input":
        return
    req_id = msg.get("id")
    params = msg.get("params") or {}
    run_id = str(params.get("run_id") or "")
    action = str(params.get("action") or "prompt")
    session = LIVE.get(run_id)
    if session is None:
        # Not an error the socket should die on: the run may have finished
        # between the manager pressing enter and this arriving, which is a
        # sentence to show them, not a fault.
        result = {"ok": False, "error": "this run is not holding a live session"}
    else:
        try:
            if action == "cancel":
                await session.cancel()
            else:
                text = str(params.get("text") or "").strip()
                if not text:
                    raise ValueError("nothing to say")
                await session.steer(text, str(params.get("author") or ""))
            result = {"ok": True}
        except Exception as e:  # noqa: BLE001 — report, never crash the socket
            logger.warning("session.input failed for run %s: %s", run_id, e)
            result = {"ok": False, "error": str(e)[:500]}
    if req_id is not None:
        await connection.reply(req_id, result)
