"""Runner command auditor (US-10.7).

Routes `run_shell` through the control socket: a `command.audit` request gets a
policy verdict + audit id BEFORE the command runs, and a `command.result`
notification reports its exit + output after. If the control socket is
unavailable the auditor fails OPEN (allows) so healthy work isn't blocked by a
transient control-plane hiccup — the server still won't have a record, which the
health signal (US-10.11) can notice.
"""

from __future__ import annotations

import logging
from collections import deque

logger = logging.getLogger("supervisor.audit")

# US-31.1: command results that could not be delivered because the control
# socket was down at that instant. On 2026-07-26 every `claude -p` failure
# had `exit_code` and `output` null in runner_command_audit for exactly this
# reason — the one moment the evidence mattered was the moment the socket had
# just dropped. Buffered here, flushed on the next opportunity. Bounded so a
# long outage costs the oldest results, never memory.
_PENDING: deque[dict] = deque(maxlen=200)


async def flush_pending(conn) -> int:
    """Deliver buffered command results; returns how many landed."""
    sent = 0
    while _PENDING:
        payload = _PENDING[0]
        try:
            await conn.notify("command.result", payload)
        except Exception:  # noqa: BLE001 — still down; keep the buffer
            break
        _PENDING.popleft()
        sent += 1
    if sent:
        logger.info("flushed %d buffered command result(s)", sent)
    return sent


class SocketAuditor:
    def __init__(self, conn, run_id: str | None = None):
        self.conn = conn
        self.run_id = run_id

    async def audit(self, argv, cwd):
        try:
            # A live request is proof the socket is up — drain any evidence
            # that was stranded by an earlier drop before the next command.
            await flush_pending(self.conn)
            reply = await self.conn.request(
                "command.audit",
                {"run_id": self.run_id, "argv": list(argv), "cwd": cwd},
                timeout=30,
            )
            return bool(reply.get("allow")), reply.get("audit_id")
        except Exception as e:  # noqa: BLE001 — fail open, don't block healthy work
            logger.warning("command audit unavailable (%s); allowing", e)
            return True, None

    async def report(self, audit_id, exit_code, output):
        payload = {
            "audit_id": audit_id,
            "exit_code": exit_code,
            "output": (output or "")[:20000],
        }
        try:
            await self.conn.notify("command.result", payload)
        except Exception:  # noqa: BLE001 — buffer, don't lose the evidence
            if audit_id is not None:
                _PENDING.append(payload)
                logger.warning(
                    "command result for audit %s buffered (socket down)", audit_id
                )
