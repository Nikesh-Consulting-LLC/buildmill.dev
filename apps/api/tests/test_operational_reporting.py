"""US-76.1/76.2: the errors users hit reach the superadmin's console.

US-16.8 only ever filed *unhandled* exceptions. A router that catches a failing
dependency and translates it into a deliberate `HTTPException` produced an
ordinary 4xx that Starlette resolved before the catch-all could see it — which
is how "GitHub merge failed" reached a manager's screen and nothing reached
System issues. These tests pin both halves of the rule: dependency failures and
5xx report, ordinary refusals stay silent.
"""

import asyncio

import pytest
from fastapi import APIRouter, HTTPException, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import app_issues, github
from app.errors import ReportedHTTPException
from app.main import app
from app.routers import reviews

_router = APIRouter()


@_router.get("/api/v1/test-only/dependency-failed")
def _dependency_failed():
    raise ReportedHTTPException(
        status_code=409,
        detail="GitHub merge failed: Not Found",
        context={"pr_url": "https://github.com/o/r/pull/31"},
    )


@_router.get("/api/v1/test-only/already-claimed")
def _already_claimed():
    # The same status code, raised because the *caller* lost a race. Pipeline
    # state — the case that must stay out of the inbox.
    raise HTTPException(status_code=409, detail="this run is already claimed")


@_router.get("/api/v1/test-only/upstream-down")
def _upstream_down():
    raise HTTPException(status_code=503, detail="the runner pool is unreachable")


@_router.websocket("/api/v1/test-only/ws-boom")
async def _ws_boom(websocket: WebSocket):
    await websocket.accept()
    raise RuntimeError("deliberate: the socket handler blew up")


@_router.websocket("/api/v1/test-only/ws-bye")
async def _ws_bye(websocket: WebSocket):
    await websocket.accept()
    # Blocks until the client hangs up, which raises WebSocketDisconnect.
    await websocket.receive_text()


app.include_router(_router)


@pytest.fixture()
def client(settings_override):
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture()
def reports(monkeypatch):
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


# --- which HTTP refusals are defects -----------------------------------------


def test_a_dependency_failure_is_reported_and_answers_unchanged(client, reports):
    resp = client.get("/api/v1/test-only/dependency-failed")
    # The client contract is untouched: same status, same detail body.
    assert resp.status_code == 409
    assert resp.json() == {"detail": "GitHub merge failed: Not Found"}
    assert len(reports) == 1
    assert reports[0]["context"]["status_code"] == 409
    assert reports[0]["context"]["path"] == "/api/v1/test-only/dependency-failed"
    # what the raise site knew rides along
    assert reports[0]["context"]["pr_url"] == "https://github.com/o/r/pull/31"


def test_an_ordinary_conflict_is_still_not_a_system_error(client, reports):
    resp = client.get("/api/v1/test-only/already-claimed")
    assert resp.status_code == 409
    # Same status code as the test above — only the exception class differs,
    # because a status code cannot tell the two apart.
    assert reports == []


def test_a_deliberate_5xx_is_reported_without_being_marked(client, reports):
    resp = client.get("/api/v1/test-only/upstream-down")
    assert resp.status_code == 503
    assert len(reports) == 1
    assert reports[0]["context"]["status_code"] == 503


# --- websockets ---------------------------------------------------------------


def test_a_websocket_crash_is_reported(client, reports):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/test-only/ws-boom"):
            pass
    assert len(reports) == 1
    assert reports[0]["error_type"] == "RuntimeError"
    assert reports[0]["context"]["transport"] == "websocket"
    assert reports[0]["context"]["path"] == "/api/v1/test-only/ws-boom"


def test_a_client_hanging_up_is_not_a_crash(client, reports):
    # The endpoint does not catch WebSocketDisconnect, so it propagates exactly
    # as it does in the running app — the point of the test is that the
    # reporter lets it through untouched rather than filing it.
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/test-only/ws-bye") as ws:
            ws.close()
    # A disconnect is how a socket normally ends; reporting it would bury the
    # real crashes under one row per closed tab.
    assert reports == []


# --- US-76.2: the message says it once ---------------------------------------


def test_the_merge_failure_message_is_not_doubled(settings_override, monkeypatch):
    async def _token(settings, user_token, org_id, repo_full_name=None):
        return "tok", "the stored PAT (…zzzz)"

    async def _merge(token, owner, repo, number, merge_method="squash"):
        # exactly what github.merge_pull_request raises
        raise github.GitHubError("GitHub merge failed: Not Found")

    monkeypatch.setattr(reviews.github_tokens, "resolve_for_user", _token)
    monkeypatch.setattr(reviews.github, "merge_pull_request", _merge)

    async def _no_pull(token, owner, repo, number):
        raise github.GitHubError("no pull")

    monkeypatch.setattr(reviews.github, "get_pull", _no_pull)

    with pytest.raises(ReportedHTTPException) as caught:
        asyncio.run(
            reviews._merge_pr(
                settings_override,
                "user-jwt",
                "aaaaaaaa-0000-4000-8000-000000000001",
                "https://github.com/o/r/pull/31",
            )
        )
    assert caught.value.detail == "GitHub merge failed: Not Found"
    assert caught.value.detail.count("GitHub merge failed") == 1
    assert caught.value.status_code == 409
