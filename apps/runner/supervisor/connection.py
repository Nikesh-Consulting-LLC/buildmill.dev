"""Supervisor runner — control-socket connection manager (US-10.1).

Holds one persistent WebSocket (JSON-RPC 2.0) to the factory's
`/api/v1/runner/socket`. On connect it sends `runner.hello` (worker token in the
`X-Worker-Token` header), records the server-assigned session id, then heartbeats
until the socket drops — reconnecting with capped backoff. Work is still pulled
over HTTP (US-10.6); this channel carries control, config (US-10.2), the LLM
inference relay (US-10.3), and command audit (US-10.7).

The frame-building helpers are pure so they can be unit-tested without a socket;
the server side of the protocol is covered by the API's WebSocket tests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("supervisor.connection")

HEARTBEAT_SECONDS = 30
RECONNECT_MIN = 1.0
RECONNECT_MAX = 30.0


def host_info() -> dict[str, Any]:
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        # US-53.4: supervisor capabilities the settings page keys warnings off
        # — an older runner that ignores a config field should be flagged as
        # ignoring it, not silently believed to obey.
        "features": ["kind_gate"],
    }


def build_hello(
    token: str,
    *,
    host: dict[str, Any] | None = None,
    agent_versions: dict[str, Any] | None = None,
    modules_available: list[str] | None = None,
    module_settings: list[dict[str, Any]] | None = None,
    req_id: int = 1,
) -> dict[str, Any]:
    """The opening JSON-RPC request. The token rides the header too, but is
    included here as a fallback for transports that can't set headers."""
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "runner.hello",
        "params": {
            "token": token,
            "host_info": host if host is not None else host_info(),
            "agent_versions": agent_versions or {},
            "modules_available": modules_available or [],
            # US-32.4: what each of those modules can be told. The server
            # stores it with the session so the settings page stays honest
            # about a module while its machine is offline.
            "module_settings": module_settings or [],
        },
    }


def heartbeat_frame() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "heartbeat"}


def next_backoff(current: float) -> float:
    return min(RECONNECT_MAX, max(RECONNECT_MIN, current * 2))


def _connect_cm(url: str, headers: dict[str, str]):
    """websockets renamed `extra_headers` → `additional_headers` in v12;
    support both so the runner isn't pinned to one release."""
    import websockets

    try:
        return websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:
        return websockets.connect(url, extra_headers=headers, max_size=None)


@dataclass
class RunnerConnection:
    """Owns the socket lifecycle. `modules_available` / `agent_versions` are
    reported at hello; `on_message` handles server→runner frames (config,
    commands) that later stories register."""

    api_url: str
    token: str
    modules_available: list[str] = field(default_factory=list)
    # US-32.4: per-module setting declarations, reported at hello.
    module_settings: list[dict[str, Any]] = field(default_factory=list)
    agent_versions: dict[str, Any] = field(default_factory=dict)
    on_message: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    on_config: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    session_id: str | None = None
    # Server-pushed config (US-10.2): seeded from the hello result, replaced
    # live on `config.update`. The work loop (US-10.6) reads this.
    config: dict[str, Any] = field(default_factory=dict)
    # Request/response correlation over the socket (US-10.3 brain relay).
    _ws: Any = field(default=None, repr=False)
    _pending: dict[int, Any] = field(default_factory=dict, repr=False)
    _req_counter: int = field(default=0, repr=False)

    def socket_url(self) -> str:
        base = self.api_url.rstrip("/")
        if base.startswith("https://"):
            base = "wss://" + base[len("https://") :]
        elif base.startswith("http://"):
            base = "ws://" + base[len("http://") :]
        return f"{base}/api/v1/runner/socket"

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        backoff = RECONNECT_MIN
        while stop is None or not stop.is_set():
            try:
                await self._serve_once(stop)
                backoff = RECONNECT_MIN
            except Exception as e:  # noqa: BLE001 — reconnect on any drop
                logger.warning("control socket dropped: %s; reconnecting", e)
            if stop is not None and stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = next_backoff(backoff)

    async def _serve_once(self, stop: asyncio.Event | None) -> None:
        headers = {"X-Worker-Token": self.token}
        async with _connect_cm(self.socket_url(), headers) as ws:
            await ws.send(
                json.dumps(
                    build_hello(
                        self.token,
                        agent_versions=self.agent_versions,
                        modules_available=self.modules_available,
                        module_settings=self.module_settings,
                    )
                )
            )
            reply = json.loads(await ws.recv())
            result = reply.get("result") or {}
            self.session_id = result.get("session_id")
            if not self.session_id:
                raise RuntimeError(f"handshake refused: {reply.get('error')}")
            self.config = result.get("config") or {}
            self._ws = ws
            logger.info("connected; session %s", self.session_id)

            beat = asyncio.create_task(self._heartbeat(ws, stop))
            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    await self._handle_message(msg)
            finally:
                beat.cancel()
                self._ws = None

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        """Built-in handling for server→runner frames, then delegate to the
        optional `on_message` hook. Replies to our own requests resolve their
        future; `config.update` replaces the live config."""
        rid = msg.get("id")
        if rid is not None and rid in self._pending:
            fut = self._pending.get(rid)
            if fut is not None and not fut.done():
                if "error" in msg:
                    fut.set_exception(
                        RuntimeError((msg["error"] or {}).get("message", "rpc error"))
                    )
                else:
                    fut.set_result(msg.get("result"))
            return
        if msg.get("method") == "config.update":
            self.config = (msg.get("params") or {}).get("config") or {}
            if self.on_config is not None:
                await self.on_config(self.config)
        if self.on_message is not None:
            await self.on_message(msg)

    def _register_request(self, method: str, params: dict[str, Any] | None):
        self._req_counter += 1
        rid = self._req_counter
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        frame = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        return rid, frame, fut

    async def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 120
    ) -> Any:
        """Send a JSON-RPC request over the control socket and await its reply."""
        if self._ws is None:
            raise RuntimeError("control socket is not connected")
        rid, frame, fut = self._register_request(method, params)
        await self._ws.send(json.dumps(frame))
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)

    async def infer(
        self,
        messages: list[dict[str, Any]],
        route: str = "runner_brain",
        temperature: float | None = None,
        timeout: float = 120,
    ) -> dict[str, Any]:
        """Ask the server brain to run an inference (US-10.3): returns the
        {completion, model, provider, used_fallback} payload."""
        return await self.request(
            "llm.infer",
            {"route": route, "messages": messages, "temperature": temperature},
            timeout=timeout,
        )

    async def mint_gateway_key(
        self,
        run_id: str | None = None,
        route: str = "runner_brain",
        timeout: float = 30,
        model: str | None = None,
    ) -> str:
        """Request a short-lived scoped gateway key from the server (US-10.3).

        US-27.8: send the model this agent is configured to use. `route` is
        `runner_code`/`runner_plan`, which the org's routing table has no
        entries for — the model is what lets the gateway pick the provider
        that actually offers it, instead of falling back to the org default
        and answering an Anthropic-shaped request from Groq."""
        reply = await self.request(
            "gateway.mint",
            {"run_id": run_id, "route": route, "model": model},
            timeout=timeout,
        )
        return reply["key"]

    async def fetch_subscription_token(self, timeout: float = 30) -> str | None:
        """US-52.2: the org's factory-held Claude subscription token, if any.

        None when the org holds none — and on any failure, including an older
        server that does not know the method — so the machine-held credential
        (us-52.1) is the fallback in every case. Never raises into a run:
        both homes bill a subscription, and a factory-side lookup problem must
        not fail work a machine credential can do. The token is never logged.
        """
        try:
            reply = await self.request("subscription.token", {}, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — fall back to the machine's own
            logger.warning(
                "subscription.token unavailable (%s); using the machine-held "
                "credential if the box has one",
                e,
            )
            return None
        token = (reply or {}).get("token")
        return str(token) if token else None

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Fire-and-forget server notification (no reply expected)."""
        if self._ws is None:
            return
        await self._ws.send(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
        )

    async def reply(
        self, req_id: Any, result: Any = None, error: str | None = None
    ) -> None:
        """Answer a server→runner request (workspace.prepare, etc.) — the
        symmetric half of `request()`, called from an `on_message` handler."""
        if self._ws is None:
            return
        frame: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            frame["error"] = {"code": 5000, "message": error}
        else:
            frame["result"] = result
        await self._ws.send(json.dumps(frame))

    async def _heartbeat(self, ws, stop: asyncio.Event | None) -> None:
        while stop is None or not stop.is_set():
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await ws.send(json.dumps(heartbeat_frame()))
            except Exception:  # noqa: BLE001 — the read loop reconnects
                return
