"""US-16.8: Build Mill reporting its own errors.

The three ways self-instrumentation hurts rather than helps, each asserted:
the reporting path raising and taking the request with it, a report-of-a-report
recursing, and a request's credentials riding along into a table managers read
and a prompt somebody pastes into an LLM.
"""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import app_issues
from app.main import app

_router = APIRouter()


@_router.get("/api/v1/test-only/boom")
def _boom():
    raise RuntimeError("deliberate: the checkout total was undefined")


@_router.get("/api/v1/test-only/not-found")
def _not_found():
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail="no such thing")


app.include_router(_router)


@pytest.fixture()
def client(settings_override):
    """Starlette's ServerErrorMiddleware runs the 500 handler and then re-raises
    so the ASGI server can log it — uvicorn swallows that, the test client does
    not. Turning off `raise_server_exceptions` is what makes this behave like
    the running app."""
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


def test_an_unhandled_exception_is_reported_and_still_500s(client, reports):
    resp = client.get("/api/v1/test-only/boom")
    assert resp.status_code == 500
    assert len(reports) == 1
    assert reports[0]["error_type"] == "RuntimeError"
    assert "checkout total" in reports[0]["message"]
    assert "_boom" in reports[0]["stack_trace"]
    assert reports[0]["context"]["path"] == "/api/v1/test-only/boom"


def test_a_handled_http_error_is_not_a_system_error(client, reports):
    resp = client.get("/api/v1/test-only/not-found")
    assert resp.status_code == 404
    # 4xx is pipeline state, not a defect — reporting it would bury the real ones.
    assert reports == []


def test_a_broken_reporting_path_does_not_break_the_request(client, monkeypatch):
    monkeypatch.setattr(
        app_issues,
        "_self_deployment",
        lambda settings: {"id": "d", "org_id": "o", "project_id": "p"},
    )

    def _explode(settings, deployment, payload):
        raise RuntimeError("the reporting path is itself broken")

    monkeypatch.setattr(app_issues, "ingest_report", _explode)
    resp = client.get("/api/v1/test-only/boom")
    # Still the original 500 — never a second failure stacked on the first.
    assert resp.status_code == 500


def test_no_configuration_means_silence(client, monkeypatch):
    monkeypatch.setattr(app_issues, "_self_deployment", lambda settings: None)
    calls: list[str] = []
    monkeypatch.setattr(
        app_issues, "ingest_report", lambda *a, **k: calls.append("wrote") or {}
    )
    resp = client.get("/api/v1/test-only/boom")
    assert resp.status_code == 500
    assert calls == []


def test_reporting_cannot_recurse(monkeypatch, settings_override):
    """An exception raised inside the reporting path must not be reported —
    otherwise a broken ingestion path is an unbounded loop."""
    depth = {"n": 0}

    def _ingest(settings, deployment, payload):
        depth["n"] += 1
        # Simulate the reporting path failing in a way that would itself be
        # reported if the guard were not held.
        app_issues.self_report(settings, RuntimeError("inner"), {})
        return {"id": "x", "deduped": False}

    monkeypatch.setattr(
        app_issues,
        "_self_deployment",
        lambda settings: {"id": "d", "org_id": "o", "project_id": "p"},
    )
    monkeypatch.setattr(app_issues, "ingest_report", _ingest)

    app_issues.self_report(settings_override, RuntimeError("outer"), {})
    assert depth["n"] == 1, "a report of a report got through the guard"


# --- nothing secret rides along ---------------------------------------------


def test_credential_shaped_context_is_redacted():
    scrubbed = app_issues.scrub(
        {
            "path": "/api/v1/runs",
            "Authorization": "Bearer sk-live-abcdef",
            "cookie": "sb-access-token=eyJ...",
            "X-Worker-Token": "sfw_deadbeef",
            "report_key": "sfr_secret",
            "supabase_service_role_key": "srk_secret",
            "nested": {"github_token": "ghp_secret", "issue_id": "abc"},
            "list": [{"password": "hunter2"}],
        }
    )
    flat = repr(scrubbed)
    for secret in (
        "sk-live-abcdef",
        "eyJ",
        "sfw_deadbeef",
        "sfr_secret",
        "srk_secret",
        "ghp_secret",
        "hunter2",
    ):
        assert secret not in flat, f"{secret} survived the scrubber"
    # ...and everything a system error actually needs survives.
    assert scrubbed["path"] == "/api/v1/runs"
    assert scrubbed["nested"]["issue_id"] == "abc"


def test_the_scrubber_terminates_on_deeply_nested_context():
    deep: dict = {}
    node = deep
    for _ in range(30):
        node["child"] = {}
        node = node["child"]
    assert app_issues.scrub(deep)  # does not recurse forever


def test_a_reported_request_never_carries_its_headers(client, reports):
    client.get(
        "/api/v1/test-only/boom",
        headers={"Authorization": "Bearer sk-live-verysecret"},
    )
    assert "sk-live-verysecret" not in repr(reports[0])


# ---------------------------------------------------------------------------
# US-76.1 follow-up: a socket that ends normally is not a defect
# ---------------------------------------------------------------------------


def test_a_peer_that_vanishes_during_the_handshake_is_not_reported(reports, monkeypatch):
    """Observed once on the runner's control socket, at the moment a deploy
    restarted the API.

    A peer that hangs up mid-handshake leaves the server calling `accept()` on
    a connection the ASGI layer has already finished with, and uvicorn answers
    with a bare RuntimeError — which misses the WebSocketDisconnect exemption
    and lands in the inbox as a crash. It is the same normal ending, one moment
    earlier, and it will recur on every release.
    """
    import asyncio

    from app.errors import WebSocketErrorReporter

    async def _app(scope, receive, send):
        raise RuntimeError(
            "Expected ASGI message 'websocket.send' or 'websocket.close', "
            "but got 'websocket.accept'."
        )

    reporter = WebSocketErrorReporter(_app)
    with pytest.raises(RuntimeError):
        asyncio.run(
            reporter({"type": "websocket", "path": "/api/v1/runner/socket"}, None, None)
        )
    assert reports == [], "a hang-up mid-handshake is not a defect"


def test_any_other_runtime_error_on_a_socket_is_still_reported(reports, monkeypatch):
    """The exemption is narrow on purpose — it matches uvicorn's own wording
    for 'this connection is gone', not RuntimeError in general."""
    import asyncio

    from app.errors import WebSocketErrorReporter

    async def _app(scope, receive, send):
        raise RuntimeError("deliberate: the session registry was None")

    reporter = WebSocketErrorReporter(_app)
    with pytest.raises(RuntimeError):
        asyncio.run(
            reporter({"type": "websocket", "path": "/api/v1/runs/x/console"}, None, None)
        )
    assert len(reports) == 1
    assert "session registry" in str(reports[0])
