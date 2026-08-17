"""US-89.1: the loopback broker injects the token; the config never holds it."""

import asyncio
import json

import httpx
import pytest

from supervisor.mcp_broker import LOCAL_KEY_HEADER, McpBroker


class FakeUpstream:
    """Records one request and answers a fixed JSON body."""

    def __init__(self):
        self.seen: dict = {}
        self.port: int | None = None
        self._server: asyncio.AbstractServer | None = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle, host="127.0.0.1", port=0
        )
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader, writer):
        request_line = (await reader.readline()).decode().strip()
        headers = {}
        while True:
            line = (await reader.readline()).decode()
            if line in ("\r\n", "\n", ""):
                break
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
        length = int(headers.get("content-length") or 0)
        body = await reader.readexactly(length) if length else b""
        self.seen = {"request_line": request_line, "headers": headers, "body": body}
        payload = json.dumps({"ok": True}).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n"
            + f"content-length: {len(payload)}\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()


async def _roundtrip(monkeypatch, key=None, path="/factory", *, bearer=None):
    upstream = FakeUpstream()
    await upstream.start()
    monkeypatch.setenv("FACTORY_WORKER_TOKEN", "sfw_live_token")
    broker = McpBroker(f"http://127.0.0.1:{upstream.port}")
    await broker.start()
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"http://127.0.0.1:{broker.port}{path}",
                content=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                headers=(
                    {
                        "content-type": "application/json",
                        "authorization": f"Bearer {bearer}",
                    }
                    if bearer is not None
                    else {
                        "content-type": "application/json",
                        LOCAL_KEY_HEADER: key
                        if key is not None
                        else broker.local_key,
                    }
                ),
            )
        return resp, upstream.seen
    finally:
        await broker.stop()
        await upstream.stop()


def test_broker_injects_the_token_and_relays_the_answer(monkeypatch):
    resp, seen = asyncio.run(_roundtrip(monkeypatch))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # The token was injected broker-side, read from the environment.
    assert seen["headers"]["x-worker-token"] == "sfw_live_token"
    # The path maps to the factory MCP endpoint — trailing slash, so no
    # 307 is ever involved (MCP clients refuse redirected servers).
    assert seen["request_line"].startswith("POST /mcp/ ")
    # The client's own headers never carried the token.
    assert b"sfw_live_token" not in seen["body"]


def test_wrong_local_key_is_refused_before_any_forward(monkeypatch):
    resp, seen = asyncio.run(_roundtrip(monkeypatch, key="not-the-key"))
    assert resp.status_code == 403
    assert seen == {}  # upstream never saw a request


def test_a_bearer_token_is_the_same_credential_as_the_header(monkeypatch):
    """us-115.1 AC6: MCP's own auth shape, so the CLI's config can name the
    credential with `bearer_token_env_var` — which is also what makes it skip
    OAuth discovery, four `.well-known` probes this broker answers 403."""

    async def go():
        upstream = FakeUpstream()
        await upstream.start()
        monkeypatch.setenv("FACTORY_WORKER_TOKEN", "sfw_live_token")
        broker = McpBroker(f"http://127.0.0.1:{upstream.port}")
        await broker.start()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"http://127.0.0.1:{broker.port}/factory",
                    content=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                    headers={
                        "content-type": "application/json",
                        "authorization": f"Bearer {broker.local_key}",
                    },
                )
            return resp, upstream.seen
        finally:
            await broker.stop()
            await upstream.stop()

    resp, seen = asyncio.run(go())
    assert resp.status_code == 200
    assert seen["headers"]["x-worker-token"] == "sfw_live_token"
    # The bearer the client sent is machine-local and stops here; it is never
    # what goes upstream.
    assert "authorization" not in seen["headers"]


def test_a_wrong_bearer_is_refused_like_a_wrong_header(monkeypatch):
    resp, seen = asyncio.run(_roundtrip(monkeypatch, bearer="not-the-key"))
    assert resp.status_code == 403
    assert seen == {}


def test_a_request_with_no_credential_at_all_is_refused(monkeypatch):
    resp, seen = asyncio.run(_roundtrip(monkeypatch, key=""))
    assert resp.status_code == 403
    assert seen == {}


def test_unknown_path_is_refused(monkeypatch):
    resp, seen = asyncio.run(_roundtrip(monkeypatch, path="/somewhere-else"))
    assert resp.status_code == 404
    assert seen == {}


def test_mcpconfig_uses_the_broker_when_it_is_up(monkeypatch):
    from supervisor import mcp_broker, mcpconfig

    async def _go():
        broker = McpBroker("https://api.example.test")
        await broker.start()
        try:
            monkeypatch.setattr(mcp_broker, "_BROKER", broker)
            body = mcpconfig.build("https://api.example.test", "sfw_secret")
            factory = body["mcpServers"]["factory"]
            assert factory["url"].startswith("http://127.0.0.1:")
            assert factory["headers"] == {
                "X-Factory-Local-Key": broker.local_key
            }
            assert "sfw_secret" not in json.dumps(body)
        finally:
            await broker.stop()
            monkeypatch.setattr(mcp_broker, "_BROKER", None)

    asyncio.run(_go())


def test_mcpconfig_falls_back_to_legacy_without_a_broker(monkeypatch):
    from supervisor import mcp_broker, mcpconfig

    monkeypatch.setattr(mcp_broker, "_BROKER", None)
    body = mcpconfig.build("https://api.example.test", "sfw_secret")
    factory = body["mcpServers"]["factory"]
    assert factory["url"] == "https://api.example.test/mcp/"
    assert factory["headers"] == {"X-Worker-Token": "sfw_secret"}
