"""US-78.2: the ACP transport, tested against what a real stream does to it.

Framing bugs in a JSON-RPC client do not fail loudly — they hang a run, which
holds a lease, which parks work nobody is doing. So every case here is one of
the six ways the stream can arrive wrong, not a happy path with decoration.
"""

import asyncio
import json

import pytest

from supervisor.acp import AcpClient, AcpError
from supervisor.acp.client import METHOD_NOT_FOUND
from supervisor.acp.events import Coalescer, describe_update


class FakeProcess:
    """A SessionProcess-shaped double: `next_line` hands out queued lines and
    then blocks (like a live agent), `send` records the frames written."""

    def __init__(self, lines=None):
        self.sent: list[str] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._eof = False
        for line in lines or []:
            self._queue.put_nowait(line)

    async def next_line(self):
        if self._eof:
            return None
        line = await self._queue.get()
        if line is None:
            self._eof = True
            return None
        return line

    async def send(self, text: str) -> None:
        self.sent.append(text)

    # -- test-side controls
    def feed(self, line: str) -> None:
        self._queue.put_nowait(line)

    def end(self) -> None:
        self._queue.put_nowait(None)

    def frames(self) -> list[dict]:
        return [json.loads(s) for s in self.sent]


async def _settle():
    """Let the reader task drain what was just queued."""
    for _ in range(6):
        await asyncio.sleep(0)


def test_request_resolves_on_its_matching_id():
    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.request("initialize", {}))
        await _settle()
        sent = proc.frames()[0]
        assert sent["method"] == "initialize"
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": sent["id"], "result": {"ok": 1}}))
        assert await asyncio.wait_for(task, 1) == {"ok": 1}
        await client.close()

    asyncio.run(scenario())


def test_a_notification_between_request_and_response_does_not_confuse_it():
    """The agent narrates while it works — updates land in the middle of every
    real request, so an id-blind reader would resolve the wrong future."""

    async def scenario():
        seen = []

        async def on_notification(method, params):
            seen.append((method, params))

        proc = FakeProcess()
        client = AcpClient(proc, on_notification=on_notification)
        client.start()
        task = asyncio.ensure_future(client.request("session/prompt", {}))
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "method": "session/update",
                    "params": {"update": {"sessionUpdate": "agent_message_chunk"}},
                }
            )
        )
        await _settle()
        assert not task.done()
        proc.feed(
            json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "Completed"}})
        )
        assert (await asyncio.wait_for(task, 1))["stopReason"] == "Completed"
        assert seen and seen[0][0] == "session/update"
        await client.close()

    asyncio.run(scenario())


def test_a_malformed_line_is_skipped_not_fatal():
    """Agents print banners and update notices onto stdout. Losing one line is
    survivable; losing the run is not."""

    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.request("initialize", {}))
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed("Grok Build 1.0.0 — checking for updates")
        proc.feed("{not json at all")
        proc.feed("[]")  # valid JSON, wrong shape
        await _settle()
        assert not task.done()
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}))
        assert await asyncio.wait_for(task, 1) == {"ok": True}
        await client.close()

    asyncio.run(scenario())


def test_an_error_reply_raises_with_its_code():
    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.request("session/load", {}))
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "loadSession not supported"},
                }
            )
        )
        with pytest.raises(AcpError) as caught:
            await asyncio.wait_for(task, 1)
        assert caught.value.code == -32601
        assert "loadSession" in caught.value.message
        await client.close()

    asyncio.run(scenario())


def test_the_child_dying_fails_every_pending_request():
    """The one that matters most: a WebSocket reconnects, a subprocess does
    not. A future nobody will ever resolve is a hung run holding a lease."""

    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        first = asyncio.ensure_future(client.request("session/prompt", {}))
        second = asyncio.ensure_future(client.request("session/prompt", {}))
        await _settle()
        proc.end()
        for task in (first, second):
            with pytest.raises(AcpError) as caught:
                await asyncio.wait_for(task, 1)
            assert "ended before answering" in caught.value.message
        await client.close()

    asyncio.run(scenario())


def test_an_agent_request_is_served_and_answered():
    async def scenario():
        async def on_request(method, params):
            assert method == "fs/read_text_file"
            return {"content": "hello"}

        proc = FakeProcess()
        client = AcpClient(proc, on_request=on_request)
        client.start()
        proc.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "fs/read_text_file",
                    "params": {"path": "/tmp/x"},
                }
            )
        )
        await _settle()
        reply = proc.frames()[-1]
        assert reply["id"] == 7 and reply["result"] == {"content": "hello"}
        await client.close()

    asyncio.run(scenario())


def test_an_unhandled_agent_request_answers_method_not_found():
    """The agent may ask for a capability we did not declare. Answering
    properly is how it learns; crashing the read loop is not."""

    async def scenario():
        async def on_request(method, params):
            raise NotImplementedError(method)

        proc = FakeProcess()
        client = AcpClient(proc, on_request=on_request)
        client.start()
        proc.feed(
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "terminal/create", "params": {}})
        )
        await _settle()
        reply = proc.frames()[-1]
        assert reply["id"] == 3
        assert reply["error"]["code"] == METHOD_NOT_FOUND
        await client.close()

    asyncio.run(scenario())


def test_a_handler_that_raises_answers_an_error_and_keeps_reading():
    async def scenario():
        async def on_request(method, params):
            raise ValueError("disk on fire")

        proc = FakeProcess()
        client = AcpClient(proc, on_request=on_request)
        client.start()
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "fs/write_text_file"}))
        await _settle()
        assert "disk on fire" in proc.frames()[-1]["error"]["message"]
        # still alive: a normal request after the failure still resolves
        task = asyncio.ensure_future(client.request("initialize", {}))
        await _settle()
        rid = proc.frames()[-1]["id"]
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}))
        assert await asyncio.wait_for(task, 1) == {}
        await client.close()

    asyncio.run(scenario())


def test_initialize_records_what_the_agent_declared():
    """US-78.4 and US-78.9 both gate on this rather than on assumption."""

    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.initialize())
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentCapabilities": {
                            "loadSession": True,
                            "mcpCapabilities": {"http": True, "sse": False},
                        },
                        "agentInfo": {"name": "buildmill-agent-cli"},
                    },
                }
            )
        )
        await asyncio.wait_for(task, 1)
        assert client.supports_load_session is True
        assert client.mcp_transports() == {"stdio": True, "http": True, "sse": False}
        assert client.protocol_version == 1
        # the handshake declares what we actually implement, no more
        params = proc.frames()[0]["params"]
        assert params["clientCapabilities"]["fs"]["readTextFile"] is True
        assert params["clientCapabilities"]["terminal"] is True
        await client.close()

    asyncio.run(scenario())


def test_an_agent_that_declares_nothing_reads_as_no_capabilities():
    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.initialize())
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}))
        await asyncio.wait_for(task, 1)
        assert client.supports_load_session is False
        # stdio is mandatory for every ACP agent, so it is the floor, not a guess
        assert client.mcp_transports() == {"stdio": True, "http": False, "sse": False}
        await client.close()

    asyncio.run(scenario())


def test_session_new_without_a_session_id_is_an_error_not_a_none():
    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        task = asyncio.ensure_future(client.session_new("/work"))
        await _settle()
        rid = proc.frames()[0]["id"]
        proc.feed(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}))
        with pytest.raises(AcpError):
            await asyncio.wait_for(task, 1)
        await client.close()

    asyncio.run(scenario())


def test_cancel_is_a_notification_with_no_id():
    async def scenario():
        proc = FakeProcess()
        client = AcpClient(proc)
        client.start()
        await client.cancel("sess-1")
        frame = proc.frames()[-1]
        assert frame["method"] == "session/cancel"
        assert "id" not in frame
        await client.close()

    asyncio.run(scenario())


# -- describe_update / Coalescer -------------------------------------------


def test_update_variants_map_to_permitted_trace_kinds():
    assert describe_update(
        {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}}
    ) == ("output", "hi")
    assert describe_update(
        {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "hmm"}}}
    ) == ("step", "hmm")
    kind, line = describe_update(
        {
            "update": {
                "sessionUpdate": "tool_call",
                "title": "Read",
                "rawInput": {"path": "/repo/app.py"},
            }
        }
    )
    assert kind == "tool" and "/repo/app.py" in line
    kind, line = describe_update(
        {
            "update": {
                "sessionUpdate": "plan",
                "entries": [
                    {"content": "one", "status": "completed"},
                    {"content": "two", "status": "in_progress"},
                ],
            }
        }
    )
    assert kind == "decision" and "1/2" in line and "two" in line


def test_our_own_prompt_echoed_back_is_dropped():
    """user_message_chunk is the manager's words, not the agent's — replaying
    it into the trace would attribute it to the agent."""
    assert (
        describe_update(
            {"update": {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "do it"}}}
        )
        is None
    )


def test_an_unknown_variant_is_ignorable_not_fatal():
    """The spec says the variant list is open."""
    assert describe_update({"update": {"sessionUpdate": "something_new_in_v2"}}) is None
    assert describe_update({}) is None
    assert describe_update({"update": "not a dict"}) is None


def test_chunks_are_coalesced_until_the_kind_changes():
    c = Coalescer(max_chars=1000, max_seconds=99)
    assert c.feed("output", "Hello", 0.0) == []
    assert c.feed("output", " world", 0.1) == []
    emitted = c.feed("tool", "Read: /x", 0.2)
    assert emitted == [("output", "Hello world"), ("tool", "Read: /x")]
    assert c.drain() == []


def test_chunks_are_rejoined_verbatim():
    """US-88.1: the chunks are consecutive slices of one string, so the only
    correct joiner is nothing at all. Joining with a space is what produced
    `a health check command .` in the manager's console."""
    c = Coalescer(max_chars=1000, max_seconds=99)
    for i, tok in enumerate(["The user ran ", "`", "/doctor", "`", ", a health check", "."]):
        assert c.feed("output", tok, float(i) / 100) == []
    assert c.drain() == [("output", "The user ran `/doctor`, a health check.")]


def test_a_multi_line_answer_stays_multi_line():
    """US-88.1 AC2: newlines are the structure of an agent's report. Collapsing
    them turned a doctor report into one unbroken smear."""
    c = Coalescer(max_chars=1000, max_seconds=99)
    c.feed("step", "# Doctor report\n\n## Overall\n", 0.0)
    c.feed("step", "- workspace: OK\n- shell: FAIL", 0.1)
    assert c.drain() == [
        ("step", "# Doctor report\n\n## Overall\n- workspace: OK\n- shell: FAIL")
    ]


def test_a_full_block_cuts_at_a_line_ending():
    """US-88.1: the console renders these rows as markdown, and half a table
    row is not half a table — it is no table at all. The cut goes to the last
    line ending that fits; the rest waits for the next row."""
    c = Coalescer(max_chars=40, max_seconds=99)
    out = c.feed("output", "| Check | Status |\n| Workspace | OK |\n| Shell | FAIL |", 0.0)
    assert out == [("output", "| Check | Status |\n| Workspace | OK |")]
    assert c.drain() == [("output", "| Shell | FAIL |")]


def test_a_time_flush_keeps_the_unfinished_line():
    """The 2-second flush must not cut a line the agent is still writing."""
    c = Coalescer(max_chars=1000, max_seconds=2.0)
    assert c.feed("output", "## Overall\nmostly hea", 0.0) == []
    assert c.tick(2.5) == [("output", "## Overall")]
    assert c.feed("output", "lthy\n", 2.6) == []
    assert c.drain() == [("output", "mostly healthy")]


def test_an_oversized_chunk_is_split_not_truncated():
    """An agent that does not stream sends the whole answer as one chunk. It
    used to be clipped to a single row with an ellipsis; every block of it
    arrives now."""
    c = Coalescer(max_chars=10, max_seconds=99)
    out = c.feed("output", "one\ntwo\nthree\nfour\n", 0.0)
    assert [line for _, line in out] + [line for _, line in c.drain()] == [
        "one\ntwo",
        "three",
        "four",
    ]


def test_a_whitespace_only_buffer_emits_nothing():
    c = Coalescer(max_chars=1000, max_seconds=99)
    c.feed("output", "\n\n", 0.0)
    assert c.drain() == []


def test_a_streamed_chunk_keeps_its_whitespace():
    """The other half of AC1 — describe_update must hand the chunk over
    untouched, because the space between two tokens lives at a chunk edge."""
    assert describe_update(
        {"update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " world"}}}
    ) == ("output", " world")
    assert describe_update(
        {"update": {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "a\nb"}}}
    ) == ("step", "a\nb")


def test_a_long_answer_flushes_before_it_becomes_a_transcript():
    c = Coalescer(max_chars=20, max_seconds=99)
    out = []
    for i in range(6):
        out.extend(c.feed("output", "chunk", float(i)))
    assert out, "a buffer past max_chars must emit rather than grow"
    assert all(len(line) <= 20 for _, line in out)


def test_a_slow_answer_still_appears_while_it_is_being_written():
    c = Coalescer(max_chars=10_000, max_seconds=2.0)
    assert c.feed("output", "thinking", 0.0) == []
    assert c.tick(1.0) == []
    assert c.tick(2.5) == [("output", "thinking")]


# ---------------------------------------------------------------------------
# US-83.3: a dead child tells its owner
# ---------------------------------------------------------------------------


def test_on_disconnect_fires_when_the_stream_dies_unasked():
    """A crashed session CLI used to leave a zombie host holding the agent —
    the owner must hear about an unrequested end of stream, exactly once."""

    async def scenario():
        fired = []
        proc = FakeProcess()
        client = AcpClient(proc, on_disconnect=lambda: fired.append(True))
        client.start()
        proc.end()  # the child died; nobody called close()
        await _settle()
        assert fired == [True]
        await client.close()
        assert fired == [True], "close() after the fact must not fire it again"

    asyncio.run(scenario())


def test_a_deliberate_close_never_fires_on_disconnect():
    async def scenario():
        fired = []
        proc = FakeProcess()
        client = AcpClient(proc, on_disconnect=lambda: fired.append(True))
        client.start()
        await _settle()
        await client.close()
        await _settle()
        assert fired == []

    asyncio.run(scenario())


def test_session_close_capability_is_presence_not_truthiness():
    """Measured on grok 1.0.0: `sessionCapabilities: {"close": {}}` — an EMPTY
    object. A truthiness check would read a supporting agent as unsupported."""
    proc = FakeProcess()
    client = AcpClient(proc)
    client.agent_capabilities = {"sessionCapabilities": {"close": {}}}
    assert client.supports_session_close is True
    client.agent_capabilities = {"sessionCapabilities": {"list": {}}}
    assert client.supports_session_close is False
    client.agent_capabilities = {}
    assert client.supports_session_close is False
