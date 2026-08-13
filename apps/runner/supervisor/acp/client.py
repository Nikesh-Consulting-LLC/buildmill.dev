"""ACP client transport (US-78.2).

JSON-RPC 2.0 over a `SessionProcess`'s stdin/stdout, newline-delimited. This is
deliberately the same shape as `supervisor/connection.py` -- `request` /
`notify` / `reply`, an id counter, a pending-future map -- because that file
already solved request/response correlation for this codebase and two styles of
the same thing is how they drift apart.

What differs from `connection.py`, and why:

  * **traffic goes both ways by design.** The control socket is mostly
    runner→server; here the agent calls US as often as we call it
    (`fs/read_text_file`, `session/request_permission`, `terminal/create`), so
    `on_request` is a first-class constructor argument rather than an
    afterthought.
  * **the peer can die mid-request.** A WebSocket reconnects; a subprocess that
    exits is gone. Every pending request is failed with a real error when the
    stream ends, because a module awaiting a future that will never resolve is
    a hung run, and a hung run holds a lease.

Protocol conventions (from the spec): camelCase keys, snake_case discriminators,
absolute paths only, 1-based line numbers.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("supervisor.acp")

# ACP versions with a single integer MAJOR. 1 is current.
PROTOCOL_VERSION = 1

# Handlers the owner supplies. A request handler returns the JSON-RPC `result`
# or raises; a notification handler returns nothing and must never raise into
# the read loop.
RequestHandler = Callable[[str, dict], Awaitable[Any]]
NotifyHandler = Callable[[str, dict], Awaitable[None]]

# JSON-RPC's own reserved code for "the method is not one I implement". The
# agent is entitled to ask for a capability we did not declare; answering with
# a proper error is how it learns, and crashing is not.
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def client_capabilities() -> dict[str, Any]:
    """What this client tells the agent it can do, in `initialize`.

    We declare `fs` and `terminal` because we implement them (US-78.2 AC3). An
    over-declaration is worse than an under-declaration: the agent would route
    real work through a method that answers "method not found", mid-run.
    """
    return {
        "fs": {"readTextFile": True, "writeTextFile": True},
        "terminal": True,
    }


class AcpError(Exception):
    """A JSON-RPC error the agent returned, carried with its code."""

    def __init__(self, message: str, code: int | None = None, data: Any = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


class AcpClient:
    """Speaks ACP to one agent process.

    Owns the read loop; does not own the process (the caller spawned it through
    the audited primitive and closes it). Every method here is transport --
    what the messages *mean* belongs to the module.
    """

    def __init__(
        self,
        proc: Any,
        *,
        on_request: RequestHandler | None = None,
        on_notification: NotifyHandler | None = None,
        on_disconnect: Callable[[], None] | None = None,
    ) -> None:
        self._proc = proc
        self._on_request = on_request
        self._on_notification = on_notification
        # US-83.3: called exactly once when the stream ends WITHOUT close()
        # having been asked for — the agent process died out from under us.
        # A deliberate close() never fires it.
        self._on_disconnect = on_disconnect
        self._pending: dict[int, asyncio.Future] = {}
        self._counter = 0
        self._reader: asyncio.Task | None = None
        self._stopped = False
        # Set once `initialize` answers, so callers can ask what the agent
        # actually supports instead of assuming (US-78.4 gates MCP transport on
        # this; US-78.9 gates resume on it).
        self.agent_capabilities: dict[str, Any] = {}
        self.agent_info: dict[str, Any] = {}
        self.protocol_version: int | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._reader is None:
            self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        self._stopped = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reader = None
        self._fail_pending("the ACP client was closed")

    # -- read loop ---------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            while True:
                line = await self._proc.next_line()
                if line is None:
                    break
                await self._handle_line(line)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a dead reader must not be silent
            logger.warning("ACP read loop ended unexpectedly", exc_info=True)
        finally:
            if not self._stopped:
                self._fail_pending(
                    "the agent process ended before answering — see its stderr"
                )
                # US-83.3: a dead child must not hold anything — the owner
                # gets told, so a session host can release the agent instead
                # of standing as a zombie until someone restarts the runner.
                if self._on_disconnect is not None:
                    try:
                        self._on_disconnect()
                    except Exception:  # noqa: BLE001 — the reaper must not kill the reader
                        logger.warning("on_disconnect handler failed", exc_info=True)

    async def _handle_line(self, line: str) -> None:
        text = line.strip()
        if not text:
            return
        try:
            msg = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            # Agents print things that are not protocol (banners, update
            # notices). Losing one line is survivable; losing the run is not.
            logger.debug("non-JSON line from agent: %.200s", text)
            return
        if not isinstance(msg, dict):
            return
        method = msg.get("method")
        if method is not None and "id" in msg:
            await self._serve(msg)
            return
        if method is not None:
            await self._deliver_notification(str(method), msg.get("params") or {})
            return
        self._resolve(msg)

    def _resolve(self, msg: dict) -> None:
        rid = msg.get("id")
        fut = self._pending.pop(rid, None) if rid is not None else None
        if fut is None or fut.done():
            return
        error = msg.get("error")
        if error:
            fut.set_exception(
                AcpError(
                    str(error.get("message") or "the agent returned an error"),
                    code=error.get("code"),
                    data=error.get("data"),
                )
            )
        else:
            fut.set_result(msg.get("result"))

    async def _deliver_notification(self, method: str, params: dict) -> None:
        if self._on_notification is None:
            return
        try:
            await self._on_notification(method, params)
        except Exception:  # noqa: BLE001 -- narration must not break the run
            logger.warning("ACP notification handler failed", exc_info=True)

    async def _serve(self, msg: dict) -> None:
        """Answer an agent→client request."""
        req_id = msg.get("id")
        method = str(msg.get("method"))
        params = msg.get("params") or {}
        if self._on_request is None:
            await self._reply_error(req_id, METHOD_NOT_FOUND, f"unhandled: {method}")
            return
        try:
            result = await self._on_request(method, params)
        except NotImplementedError:
            await self._reply_error(req_id, METHOD_NOT_FOUND, f"unhandled: {method}")
        except AcpError as e:
            await self._reply_error(req_id, e.code or INTERNAL_ERROR, e.message)
        except Exception as e:  # noqa: BLE001
            logger.warning("ACP request handler failed: %s", method, exc_info=True)
            await self._reply_error(req_id, INTERNAL_ERROR, str(e))
        else:
            await self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _reply_error(self, req_id: Any, code: int, message: str) -> None:
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": code, "message": message},
            }
        )

    def _fail_pending(self, reason: str) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(AcpError(reason))

    # -- sending -----------------------------------------------------------

    async def _send(self, frame: dict) -> None:
        await self._proc.send(json.dumps(frame))

    async def request(
        self, method: str, params: dict | None = None, timeout: float = 120
    ) -> Any:
        self._counter += 1
        rid = self._counter
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            )
        except Exception:
            self._pending.pop(rid, None)
            raise
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise AcpError(f"{method} did not answer within {timeout}s") from None
        finally:
            self._pending.pop(rid, None)

    async def notify(self, method: str, params: dict | None = None) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # -- the agent-facing calls -------------------------------------------

    async def initialize(self, timeout: float = 60) -> dict:
        """Handshake. Records what the agent says it can do.

        Everything downstream gates on this rather than on assumption: US-78.4
        picks its MCP transport from `mcpCapabilities`, US-78.9 only attempts a
        resume when `loadSession` is declared.
        """
        result = await self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": client_capabilities(),
                "clientInfo": {
                    "name": "buildmill-runner",
                    "title": "Build Mill",
                    "version": "1",
                },
            },
            timeout=timeout,
        ) or {}
        self.agent_capabilities = result.get("agentCapabilities") or {}
        self.agent_info = result.get("agentInfo") or {}
        version = result.get("protocolVersion")
        self.protocol_version = version if isinstance(version, int) else None
        return result

    @property
    def supports_load_session(self) -> bool:
        return bool(self.agent_capabilities.get("loadSession"))

    @property
    def supports_session_close(self) -> bool:
        """US-83.3: measured against grok 1.0.0 — the handshake declares
        `sessionCapabilities: {"close": {}}` (an EMPTY object, so presence is
        the signal, never truthiness), and `session/close {sessionId}` answers
        `{"_meta": {"x.ai/closeOutcome": "closed"}}`."""
        caps = self.agent_capabilities.get("sessionCapabilities")
        return isinstance(caps, dict) and "close" in caps

    def mcp_transports(self) -> dict[str, bool]:
        """Which MCP transports the agent declared. stdio is mandatory for
        every ACP agent, so it is always true; http/sse are what vary."""
        caps = self.agent_capabilities.get("mcpCapabilities") or {}
        return {
            "stdio": True,
            "http": bool(caps.get("http")),
            "sse": bool(caps.get("sse")),
        }

    async def session_new(
        self,
        cwd: str,
        mcp_servers: list[dict] | None = None,
        additional_directories: list[str] | None = None,
        timeout: float = 120,
    ) -> str:
        result = await self.request(
            "session/new",
            {
                "cwd": cwd,
                "mcpServers": mcp_servers or [],
                "additionalDirectories": additional_directories or [],
            },
            timeout=timeout,
        ) or {}
        session_id = result.get("sessionId")
        if not session_id:
            raise AcpError("session/new returned no sessionId")
        return str(session_id)

    async def session_load(
        self,
        session_id: str,
        cwd: str,
        mcp_servers: list[dict] | None = None,
        timeout: float = 300,
    ) -> dict:
        """Resume a prior session.

        The spec requires the agent to replay the whole conversation as
        `session/update` notifications *before* answering this — which is why
        the caller mutes its notification handler across the call (US-78.9),
        or the run's trace is written twice.
        """
        return await self.request(
            "session/load",
            {
                "sessionId": session_id,
                "cwd": cwd,
                "mcpServers": mcp_servers or [],
            },
            timeout=timeout,
        ) or {}

    async def prompt(
        self, session_id: str, text: str, timeout: float = 3600
    ) -> str:
        """One turn. Returns the stop reason.

        US-83.4: the measured vocabulary is snake_case — `end_turn`,
        `max_tokens`, `max_turn_requests`, `refusal`, `cancelled`. An early
        spec draft's PascalCase list (Completed/ToolUse/StopSequence) stood
        here once and shipped a bug (85ed355: complete PRDs discarded because
        `end_turn` was not on the list). Do not teach it again."""
        result = await self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            },
            timeout=timeout,
        ) or {}
        return str(result.get("stopReason") or "")

    async def cancel(self, session_id: str) -> None:
        """A notification, not a request — there is nothing to wait for."""
        await self.notify("session/cancel", {"sessionId": session_id})

    async def session_close(self, session_id: str, timeout: float = 5) -> None:
        """US-83.3: ask the agent to close the session before the process is
        terminated, so it can flush its own session state. Only meaningful
        when `supports_session_close`; short timeout because this is a
        courtesy on the way to a kill, never something to hang on."""
        await self.request("session/close", {"sessionId": session_id}, timeout=timeout)
