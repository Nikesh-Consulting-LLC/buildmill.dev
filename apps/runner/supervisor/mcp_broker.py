"""US-89.1: the loopback MCP broker — the workspace never holds the token.

Every CLI used to receive the worker token inside its MCP config —
`.factory-mcp.json` in the repo checkout, its `.grok/config.toml`
translation, the ACP session's server list. One of those files got
committed to a project repo on 2026-08-13, which is the whole argument:
files travel; secrets must not ride them.

The broker is a minimal HTTP relay on 127.0.0.1. The CLI's config points
at `http://127.0.0.1:<port>/factory` carrying only a MACHINE-LOCAL key —
minted fresh at supervisor start, worthless off the box — and the broker
injects `X-Worker-Token` (read from this process's environment at request
time, so a reissued token works without touching anything) as it forwards
to the factory. Per-slot isolation on shared pools comes from the key: a
sibling slot's user can reach the port but not use it.

Streaming-safe by construction: the upstream response is relayed
chunk-by-chunk as it arrives (the factory MCP answers JSON and SSE), and
each connection is `Connection: close` — one request, one relay, no
keep-alive state to get wrong.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from typing import Any

import httpx

logger = logging.getLogger("supervisor.mcp_broker")

LOCAL_KEY_HEADER = "x-factory-local-key"
MAX_BODY = 32 * 1024 * 1024
UPSTREAM_TIMEOUT = 300


class McpBroker:
    def __init__(self, api_url: str):
        self._api = (api_url or "").rstrip("/")
        self.local_key = secrets.token_urlsafe(24)
        # us-96.11: registered the moment it exists — this exact value rode
        # a run trace verbatim on 2026-08-14, and nothing that emits
        # telemetry may ever see it unmasked again.
        from . import redact

        redact.register("factory-local-key", self.local_key)
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None
        self._client: httpx.AsyncClient | None = None

    @property
    def factory_url(self) -> str | None:
        return f"http://127.0.0.1:{self.port}/factory" if self.port else None

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT)
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0
        )
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info("mcp broker listening on 127.0.0.1:%s", self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- one connection = one relayed request -------------------------------

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._relay(reader, writer)
        except Exception as e:  # noqa: BLE001 — a broken relay must not kill the loop
            logger.warning("mcp broker relay failed: %s", e)
            try:
                writer.write(
                    b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _relay(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line = (await reader.readline()).decode("latin-1").strip()
        if not request_line:
            return
        try:
            method, path, _version = request_line.split(" ", 2)
        except ValueError:
            await self._respond(writer, 400, b"bad request line")
            return

        headers: dict[str, str] = {}
        while True:
            line = (await reader.readline()).decode("latin-1")
            if line in ("\r\n", "\n", ""):
                break
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()

        if headers.get(LOCAL_KEY_HEADER) != self.local_key:
            await self._respond(writer, 403, b"missing or wrong local key")
            return

        if not (path == "/factory" or path.startswith("/factory?")):
            await self._respond(writer, 404, b"unknown broker path")
            return

        length = int(headers.get("content-length") or 0)
        if length > MAX_BODY:
            await self._respond(writer, 413, b"body too large")
            return
        body = await reader.readexactly(length) if length else b""

        # The upstream request: same method and body, pass-through of the
        # content-negotiation headers, the worker token injected HERE — read
        # at request time so a rotation needs no restart of anything local.
        upstream_headers = {
            "X-Worker-Token": os.environ.get("FACTORY_WORKER_TOKEN", ""),
        }
        for name in ("content-type", "accept", "mcp-session-id", "last-event-id"):
            if headers.get(name):
                upstream_headers[name] = headers[name]

        # Trailing slash: the slashless path 307s at the mount and MCP
        # clients refuse redirected servers — and this relay does not follow
        # redirects either, so the slash is correctness, not style.
        query = path.split("?", 1)[1] if "?" in path else ""
        url = f"{self._api}/mcp/" + (f"?{query}" if query else "")

        assert self._client is not None
        async with self._client.stream(
            method, url, content=body, headers=upstream_headers
        ) as resp:
            head = [f"HTTP/1.1 {resp.status_code} {resp.reason_phrase or ''}".rstrip()]
            for name in ("content-type", "mcp-session-id", "cache-control"):
                if resp.headers.get(name):
                    head.append(f"{name}: {resp.headers[name]}")
            head.append("connection: close")
            # No content-length: the connection close delimits the body, which
            # keeps SSE streams honest without chunked-encoding bookkeeping.
            writer.write(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
            await writer.drain()
            async for chunk in resp.aiter_bytes():
                writer.write(chunk)
                await writer.drain()

    @staticmethod
    async def _respond(
        writer: asyncio.StreamWriter, status: int, body: bytes
    ) -> None:
        writer.write(
            (
                f"HTTP/1.1 {status} X\r\nConnection: close\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode("latin-1")
            + body
        )
        await writer.drain()


# ---------------------------------------------------------------------------
# The supervisor's singleton — started at boot, consulted by mcpconfig.
# ---------------------------------------------------------------------------

_BROKER: McpBroker | None = None


async def start(api_url: str) -> McpBroker | None:
    """Start the process-wide broker. Failure is loud in the log but not
    fatal: mcpconfig falls back to the legacy token-in-config shape, so a
    machine where loopback binding is impossible keeps working."""
    global _BROKER
    if _BROKER is not None:
        return _BROKER
    broker = McpBroker(api_url)
    try:
        await broker.start()
    except Exception as e:  # noqa: BLE001
        logger.warning("mcp broker could not start (legacy config stays): %s", e)
        return None
    _BROKER = broker
    return broker


def info() -> tuple[str, str] | None:
    """(factory_url, local_key) when the broker is up, else None."""
    if _BROKER is not None and _BROKER.factory_url:
        return _BROKER.factory_url, _BROKER.local_key
    return None
