"""US-10.1/10.2/10.3: supervisor connection — frames, config, request relay."""

import asyncio

import pytest

from supervisor.connection import (
    RECONNECT_MAX,
    RECONNECT_MIN,
    RunnerConnection,
    build_hello,
    heartbeat_frame,
    next_backoff,
)


def test_build_hello_shape():
    h = build_hello("tok", agent_versions={"claude": "1"}, modules_available=["sim", "claude"])
    assert h["jsonrpc"] == "2.0"
    assert h["method"] == "runner.hello"
    assert h["params"]["token"] == "tok"
    assert h["params"]["modules_available"] == ["sim", "claude"]
    assert h["params"]["agent_versions"] == {"claude": "1"}
    assert "host_info" in h["params"]


def test_heartbeat_is_a_notification():
    f = heartbeat_frame()
    assert f["method"] == "heartbeat"
    assert "id" not in f  # notification: no reply expected


def test_socket_url_scheme_upgrade():
    assert (
        RunnerConnection(api_url="https://f.example.com", token="t").socket_url()
        == "wss://f.example.com/api/v1/runner/socket"
    )
    assert (
        RunnerConnection(api_url="http://localhost:8000/", token="t").socket_url()
        == "ws://localhost:8000/api/v1/runner/socket"
    )


def test_backoff_grows_and_caps():
    assert next_backoff(RECONNECT_MIN) >= RECONNECT_MIN
    assert next_backoff(RECONNECT_MAX) == RECONNECT_MAX
    assert next_backoff(0.1) <= RECONNECT_MAX


def test_config_update_replaces_live_config():
    conn = RunnerConnection(api_url="http://x", token="t")
    seen = []

    async def on_config(cfg):
        seen.append(cfg)

    conn.on_config = on_config

    async def _run():
        await conn._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "config.update",
                "params": {"config": {"max_item_attempts": 3}},
            }
        )

    asyncio.run(_run())
    assert conn.config == {"max_item_attempts": 3}
    assert seen == [{"max_item_attempts": 3}]


def test_request_resolves_on_matching_reply():
    conn = RunnerConnection(api_url="http://x", token="t")

    async def _run():
        rid, frame, fut = conn._register_request("llm.infer", {"messages": []})
        assert frame["id"] == rid and frame["method"] == "llm.infer"
        await conn._handle_message(
            {"jsonrpc": "2.0", "id": rid, "result": {"completion": "hi"}}
        )
        return await fut

    assert asyncio.run(_run())["completion"] == "hi"


def test_request_raises_on_error_reply():
    conn = RunnerConnection(api_url="http://x", token="t")

    async def _run():
        rid, frame, fut = conn._register_request("llm.infer", {})
        await conn._handle_message(
            {"jsonrpc": "2.0", "id": rid, "error": {"code": 5000, "message": "boom"}}
        )
        return await fut

    with pytest.raises(RuntimeError):
        asyncio.run(_run())


def test_reply_sends_result_or_error_frame():
    conn = RunnerConnection(api_url="http://x", token="t")
    sent = []

    class FakeWs:
        async def send(self, text):
            sent.append(text)

    conn._ws = FakeWs()

    asyncio.run(conn.reply("srv-1", result={"ok": True}))
    import json as _json

    frame = _json.loads(sent[0])
    assert frame == {"jsonrpc": "2.0", "id": "srv-1", "result": {"ok": True}}

    asyncio.run(conn.reply("srv-2", error="boom"))
    frame = _json.loads(sent[1])
    assert frame["id"] == "srv-2"
    assert frame["error"]["message"] == "boom"


def test_reply_is_a_noop_when_disconnected():
    conn = RunnerConnection(api_url="http://x", token="t")
    # No _ws set — must not raise.
    asyncio.run(conn.reply("srv-1", result={"ok": True}))


# --------------------------------------------------------------------- US-52.2
def test_fetch_subscription_token_returns_token_none_or_falls_back():
    conn = RunnerConnection(api_url="http://x", token="t")

    async def _with_reply(reply):
        async def fake_request(method, params=None, timeout=30):
            assert method == "subscription.token"
            return reply

        conn.request = fake_request
        return await conn.fetch_subscription_token()

    # The org holds a token -> it rides.
    assert asyncio.run(_with_reply({"token": "sk-ant-oat01-abc"})) == "sk-ant-oat01-abc"
    # The org holds none -> None, so the machine-held credential is used.
    assert asyncio.run(_with_reply({"token": None})) is None

    # An older server (method unknown) or a socket hiccup -> None, never a
    # raise: a factory-side lookup problem must not fail work the machine's
    # own credential can do.
    async def _raising():
        async def fake_request(method, params=None, timeout=30):
            raise RuntimeError("method not found")

        conn.request = fake_request
        return await conn.fetch_subscription_token()

    assert asyncio.run(_raising()) is None
