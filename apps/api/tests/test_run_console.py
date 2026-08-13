"""US-78.8: the manager's console onto a live interactive run.

The console is the first path in this system from a person to a running agent,
so the tests that matter are the refusals: who may attach, and what happens
when there is nothing on the other end.
"""

import asyncio
import json

import pytest

from app.routers import run_console, runner_socket


@pytest.fixture()
def fake_run(monkeypatch):
    """One run, readable by the caller. Read through PostgREST under the user's
    own token, so RLS is what decides visibility — these fakes stand in for the
    database's answer, not for the authorization."""
    state = {"run": {"id": "run-1", "status": "running", "worker_id": "w-1"}, "trace": []}

    async def _get(settings, token, path, params):
        if path == "runs":
            return [state["run"]] if state["run"] else []
        if path == "run_trace":
            return state["trace"]
        raise AssertionError(f"unfaked read: {path}")

    monkeypatch.setattr(run_console, "postgrest_get", _get)
    return state


def _attach(client, token, run_id="run-1"):
    ws = client.websocket_connect(f"/api/v1/runs/{run_id}/console")
    ws.__enter__()
    ws.send_text(json.dumps({"token": token}))
    return ws


def test_attaching_replays_the_trace_from_the_top(client, make_token, fake_run, monkeypatch):
    """A manager who opens the console late is not looking at a fragment."""
    fake_run["trace"] = [
        {"kind": "output", "content": "first thing", "created_at": "2026-01-01"},
        {"kind": "step", "content": "stage:checkout 12ms", "created_at": "2026-01-01"},
        {"kind": "tool", "content": "Read: /repo/a.py", "created_at": "2026-01-01"},
    ]
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        hello = ws.receive_json()
    assert hello["type"] == "attached"
    assert hello["steerable"] is True
    contents = [t["content"] for t in hello["trace"]]
    assert "first thing" in contents
    # stage: lines are timing instrumentation the run page already strips —
    # noise in a transcript a human is reading.
    assert not any(c.startswith("stage:") for c in contents)


def test_a_run_the_caller_cannot_see_is_not_found(client, make_token, fake_run):
    """RLS answering nothing must not distinguish 'does not exist' from 'not
    yours' — that difference is itself information about another org."""
    fake_run["run"] = None
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        msg = ws.receive_json()
    assert msg["type"] == "error" and "not found" in msg["message"].lower()


def test_a_bad_token_is_refused_before_any_read(client, fake_run):
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": "not-a-jwt"}))
        msg = ws.receive_json()
    assert msg["type"] == "error"
    assert "authentication" in msg["message"].lower()


def test_an_offline_worker_is_attachable_but_not_steerable(
    client, make_token, fake_run, monkeypatch
):
    """Watching a finished or disconnected run still works — only typing into
    it does not, and the console is told which before the manager tries."""
    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: False)
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        hello = ws.receive_json()
        assert hello["steerable"] is False
        ws.send_text(json.dumps({"action": "prompt", "text": "stop that"}))
        refused = ws.receive_json()
    assert refused["type"] == "refused"
    assert "not connected" in refused["message"]


def test_typing_reaches_the_worker_as_session_input(
    client, make_token, fake_run, monkeypatch
):
    sent = {}

    async def _request(worker_id, method, params=None, timeout=90):
        sent["worker_id"] = worker_id
        sent["method"] = method
        sent["params"] = params
        return {"ok": True}

    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)
    monkeypatch.setattr(runner_socket, "request_from_worker", _request)
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        ws.receive_json()
        ws.send_text(json.dumps({"action": "prompt", "text": "use the other library"}))
        # nothing comes back on success — the agent's reply arrives as trace
        ws.send_text(json.dumps({"action": "cancel"}))
        ws.close()
    assert sent["method"] == "session.input"
    assert sent["worker_id"] == "w-1"
    # US-78.8 AC6: attributed, so a steered run reads afterwards as a steered run
    assert sent["params"]["author"]


def test_a_worker_refusal_is_shown_rather_than_swallowed(
    client, make_token, fake_run, monkeypatch
):
    async def _request(worker_id, method, params=None, timeout=90):
        return {"ok": False, "error": "this run is not holding a live session"}

    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)
    monkeypatch.setattr(runner_socket, "request_from_worker", _request)
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        ws.receive_json()
        ws.send_text(json.dumps({"action": "prompt", "text": "hello"}))
        msg = ws.receive_json()
    assert msg["type"] == "refused"
    assert "live session" in msg["message"]


def test_an_unknown_action_is_ignored_not_relayed(
    client, make_token, fake_run, monkeypatch
):
    calls = []

    async def _request(worker_id, method, params=None, timeout=90):
        calls.append(method)
        return {"ok": True}

    monkeypatch.setattr(runner_socket, "is_worker_live", lambda w: True)
    monkeypatch.setattr(runner_socket, "request_from_worker", _request)
    with client.websocket_connect("/api/v1/runs/run-1/console") as ws:
        ws.send_text(json.dumps({"token": make_token()}))
        ws.receive_json()
        ws.send_text(json.dumps({"action": "rm -rf", "text": "x"}))
        ws.send_text(json.dumps({"action": "prompt", "text": "real one"}))
        ws.close()
    assert calls == ["session.input"]


# -- the fan-out registry, on its own ---------------------------------------


def test_broadcast_reaches_every_attached_console():
    async def scenario():
        a: asyncio.Queue = asyncio.Queue()
        b: asyncio.Queue = asyncio.Queue()
        runner_socket.attach_console("run-x", a)
        runner_socket.attach_console("run-x", b)
        runner_socket.broadcast_to_consoles("run-x", {"type": "trace", "content": "hi"})
        assert (await a.get())["content"] == "hi"
        assert (await b.get())["content"] == "hi"
        runner_socket.detach_console("run-x", a)
        runner_socket.detach_console("run-x", b)

    asyncio.run(scenario())


def test_detaching_leaves_the_run_going_and_cleans_up():
    q: asyncio.Queue = asyncio.Queue()
    runner_socket.attach_console("run-y", q)
    runner_socket.detach_console("run-y", q)
    # no listeners left, and broadcasting to nobody is not an error
    runner_socket.broadcast_to_consoles("run-y", {"type": "trace"})
    assert "run-y" not in runner_socket._CONSOLES


def test_a_console_that_cannot_keep_up_loses_a_line_not_a_run():
    """A full queue must never propagate back into the trace handler — the run
    matters and the spectator does not."""

    class Full:
        def put_nowait(self, item):
            raise RuntimeError("full")

    runner_socket.attach_console("run-z", Full())
    try:
        runner_socket.broadcast_to_consoles("run-z", {"type": "trace"})
    finally:
        runner_socket._CONSOLES.pop("run-z", None)
