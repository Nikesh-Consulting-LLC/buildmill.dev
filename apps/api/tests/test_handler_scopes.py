"""US-79.7: error handlers must survive every scope they serve (prod BUG-8).

BUG-8's shape: a PostgREST refusal on a websocket route reached
`answer_postgrest_refusal`, whose `request.method` dereference raised
AttributeError from *inside* the handler — so the inbox reported the mask six
times and the cause never. Commit 8c0f4af fixed the two report-context dicts;
this file pins the rest: every registered handler, invoked with a websocket
connection, must report the original error and never raise itself.
"""

import asyncio

import pytest
from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.exceptions import RequestValidationError, WebSocketRequestValidationError
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.websockets import WebSocket as StarletteWebSocket

from app import app_issues
from app.main import app
from app.supabase import PostgrestError, SupabaseUnreachable

_router = APIRouter()


@_router.websocket("/api/v1/test-only/ws-postgrest")
async def _ws_postgrest(websocket: WebSocket):
    await websocket.accept()
    raise PostgrestError(
        "column run_trace.created_at does not exist", code="42703", status_code=400
    )


@_router.websocket("/api/v1/test-only/ws-http-500")
async def _ws_http_500(websocket: WebSocket):
    await websocket.accept()
    raise HTTPException(status_code=500, detail="deliberate socket 500")


@_router.websocket("/api/v1/test-only/ws-crash")
async def _ws_crash(websocket: WebSocket):
    await websocket.accept()
    raise ValueError("deliberate socket crash")


app.include_router(_router)


@pytest.fixture()
def client(settings_override):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def reports(monkeypatch):
    """Capture self-reports instead of writing them."""
    captured: list[dict] = []

    def _ingest(settings, deployment, payload):
        captured.append(payload)
        return {"id": "77777777-7777-4777-8777-777777777777", "deduped": False}

    monkeypatch.setattr(
        app_issues,
        "_self_deployment",
        lambda settings: {"id": "d", "org_id": "o", "project_id": "p"},
    )
    monkeypatch.setattr(app_issues, "ingest_report", _ingest)
    return captured


def _drain(ws) -> None:
    """Run the socket until the server side finishes; the routes above raise
    immediately after accept, so there is nothing to receive."""
    try:
        ws.receive_text()
    except Exception:  # noqa: BLE001 — any ending is fine; the report is the assertion
        pass


def test_a_postgrest_refusal_on_a_socket_reports_the_cause(client, reports):
    with client.websocket_connect("/api/v1/test-only/ws-postgrest") as ws:
        _drain(ws)
    assert reports, "the refusal was never reported"
    assert reports[0]["error_type"] == "PostgrestError"
    assert "created_at" in reports[0]["message"]
    assert reports[0]["context"]["method"] == "WEBSOCKET"
    masks = [r for r in reports if r["error_type"] == "AttributeError"]
    assert masks == [], "the handler raised over the failure it was reporting"


def test_a_socket_500_reports_as_itself(client, reports):
    with client.websocket_connect("/api/v1/test-only/ws-http-500") as ws:
        _drain(ws)
    assert reports, "a 5xx on a socket is a defect and must be reported"
    assert reports[0]["error_type"] == "HTTPException"
    assert reports[0]["context"]["method"] == "WEBSOCKET"
    assert reports[0]["context"]["status_code"] == 500
    masks = [r for r in reports if r["error_type"] == "AttributeError"]
    assert masks == []


def test_an_unhandled_socket_crash_reports_the_original(client, reports):
    # No handler catches a plain ValueError on a socket: the reporter files it
    # and re-raises, which the test client surfaces at context exit.
    with pytest.raises(ValueError):
        with client.websocket_connect("/api/v1/test-only/ws-crash") as ws:
            _drain(ws)
    assert reports
    assert reports[0]["error_type"] == "ValueError"
    assert reports[0]["context"]["transport"] == "websocket"


# ---------------------------------------------------------------------------
# The sweep: no handler anyone registers later may reintroduce the mask.
# ---------------------------------------------------------------------------


def _fake_websocket() -> StarletteWebSocket:
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "scheme": "ws",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": "/api/v1/test-only/sweep",
        "raw_path": b"/api/v1/test-only/sweep",
        "query_string": b"",
        "headers": [],
        "subprotocols": [],
    }

    async def _receive():
        return {"type": "websocket.connect"}

    async def _send(message):
        return None

    return StarletteWebSocket(scope, receive=_receive, send=_send)


# One representative instance per registered handler key. A new handler makes
# this test fail with a naming error until its author adds one — which is the
# moment they find out it will be invoked for websockets too.
_REPRESENTATIVE = {
    RequestValidationError: lambda: RequestValidationError([]),
    WebSocketRequestValidationError: lambda: WebSocketRequestValidationError([]),
    StarletteHTTPException: lambda: StarletteHTTPException(
        status_code=500, detail="sweep"
    ),
    HTTPException: lambda: HTTPException(status_code=500, detail="sweep"),
    PostgrestError: lambda: PostgrestError("sweep", code="PGRST000", status_code=400),
    SupabaseUnreachable: lambda: SupabaseUnreachable("GET sweep", "ConnectTimeout"),
    Exception: lambda: RuntimeError("sweep"),
}


def test_every_registered_handler_survives_a_websocket_scope(monkeypatch):
    monkeypatch.setattr(app_issues, "_self_deployment", lambda settings: None)
    class_keys = [k for k in app.exception_handlers if isinstance(k, type)]
    assert class_keys, "no exception handlers registered — the sweep found nothing"
    for key in class_keys:
        factory = _REPRESENTATIVE.get(key)
        assert factory is not None, (
            f"no representative exception for handler key {key.__name__} — "
            "add one to _REPRESENTATIVE so the new handler is swept for "
            "websocket safety (US-79.7)"
        )
        handler = app.exception_handlers[key]
        result = handler(_fake_websocket(), factory())
        if asyncio.iscoroutine(result):
            asyncio.run(result)
