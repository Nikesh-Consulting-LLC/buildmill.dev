"""US-36.1: no control-socket handler may take the socket down with it.

On 2026-07-27 the supervisor sent `run.trace` with no `kind`. The handler
defaulted it to `note`, which `run_trace_kind_check` does not permit, so every
insert raised. The handler had no try/except — despite a comment claiming a
trace "must not cost a run" — so the exception escaped `_dispatch`, escaped the
control loop, and closed the WebSocket. Claiming is HTTP and minting is that
socket, so the agent kept taking work it could no longer do: five runs, one per
reconnect, each blaming `Not logged in`.

`run_trace` had never held a single row.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from app import db
from app.routers import runner_socket

ORG = str(uuid.uuid4())
WORKER = str(uuid.uuid4())
RUN = str(uuid.uuid4())


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw):
        self.sent.append(json.loads(raw))


def _worker():
    return {"id": WORKER, "org_id": ORG, "name": "Programmer.001"}


def _dispatch(msg):
    ws = FakeSocket()
    asyncio.run(runner_socket._dispatch(object(), ws, _worker(), "sess-1", msg))
    return ws


# ------------------------------------------------------- the trace kind fix


class _FakeCursor:
    def fetchone(self):
        return {"id": 1}


class _RecordingConn:
    """Captures the `kind` actually sent to the SQL function."""

    def __init__(self, sink: list[str]):
        self.sink = sink

    def execute(self, _query, params):
        self.sink.append(params[2])
        return _FakeCursor()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _kinds_written(monkeypatch, kinds):
    written: list[str] = []
    monkeypatch.setattr(db, "_valid_uuid", lambda v: True)
    monkeypatch.setattr(db, "_connect", lambda s: _RecordingConn(written))
    for kind in kinds:
        db.record_run_trace(object(), RUN, WORKER, kind, "x")
    return written


def test_unknown_kind_is_coerced_to_a_permitted_value(monkeypatch):
    """The trigger. `note` is not in the constraint; storing the line as
    `progress` keeps the diagnostics instead of losing them and the socket."""
    assert _kinds_written(monkeypatch, ["note", "banana", ""]) == [
        "progress",
        "progress",
        "progress",
    ]


def test_every_permitted_kind_is_passed_through_unchanged(monkeypatch):
    assert _kinds_written(monkeypatch, db.RUN_TRACE_KINDS) == list(db.RUN_TRACE_KINDS)


def test_the_default_kind_is_one_the_constraint_allows():
    """A default outside the permitted set is exactly what caused the outage."""
    assert db.DEFAULT_RUN_TRACE_KIND in db.RUN_TRACE_KINDS


# --------------------------------------------------- the handlers stay alive


def test_run_trace_survives_a_failing_write(monkeypatch):
    """The comment always promised this; now the code does it."""

    def boom(*a, **kw):
        raise RuntimeError("violates check constraint run_trace_kind_check")

    monkeypatch.setattr(db, "record_run_trace", boom)

    ws = _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "run.trace",
            "params": {"run_id": RUN, "content": "a note"},
        }
    )
    # It did not raise (the socket lives) and the runner still got its reply.
    assert ws.sent and ws.sent[0]["id"] == 4
    assert ws.sent[0]["result"] == {"ok": True}


def test_gateway_mint_survives_a_failing_mint(monkeypatch):
    """Unguarded, this is the same outage on any transient database error."""

    def boom(*a, **kw):
        raise RuntimeError("connection already closed")

    # US-60.1: gateway.mint now reads the worker's own config first, to
    # decide platform_billed — stub it so this test still exercises mint's
    # OWN failure, not an incidental one from that lookup.
    monkeypatch.setattr(db, "get_runner_config", lambda s, wid: {})
    monkeypatch.setattr(db, "mint_gateway_key", boom)

    ws = _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "gateway.mint",
            "params": {"run_id": RUN, "route": "runner_code"},
        }
    )
    assert ws.sent and ws.sent[0]["id"] == 9
    assert "error" in ws.sent[0]
    assert "gateway.mint failed" in ws.sent[0]["error"]["message"]


def test_gateway_mint_returns_the_key_when_it_works(monkeypatch):
    monkeypatch.setattr(db, "get_runner_config", lambda s, wid: {})
    monkeypatch.setattr(db, "mint_gateway_key", lambda *a, **kw: "sfg_abc")
    ws = _dispatch(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "gateway.mint",
            "params": {"run_id": RUN, "route": "runner_code", "model": "m"},
        }
    )
    assert ws.sent[0]["result"] == {"key": "sfg_abc"}
