"""US-79.3: a caller that hangs up mid-request is not a defect (prod BUG-4).

An agent's tunnel dropped mid-POST to the LLM gateway; `request.body()` raised
`ClientDisconnect`, which climbed the middleware stack — wrapped in a
one-member ExceptionGroup along the way — and was filed in the System issues
inbox as a crash. Nothing failed on our side; the caller left.
"""

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app import app_issues
from app.errors import is_client_disconnect, unwrap
from app.main import app

_router = APIRouter()


@_router.get("/api/v1/test-only/hangup-bare")
def _hangup_bare():
    raise ClientDisconnect()


@_router.get("/api/v1/test-only/hangup-wrapped")
def _hangup_wrapped():
    raise ExceptionGroup("collapsed", [ClientDisconnect()])


@_router.get("/api/v1/test-only/hangup-and-worse")
def _hangup_and_worse():
    raise ExceptionGroup(
        "two failures", [ClientDisconnect(), RuntimeError("deliberate: real crash")]
    )


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


def test_a_bare_hangup_files_nothing(client, reports):
    resp = client.get("/api/v1/test-only/hangup-bare")
    assert resp.status_code == 204
    assert reports == [], "a caller hanging up was reported as a system error"


def test_a_group_wrapped_hangup_files_nothing(client, reports):
    resp = client.get("/api/v1/test-only/hangup-wrapped")
    assert resp.status_code == 204
    assert reports == []


def test_a_hangup_beside_a_real_failure_still_files(client, reports):
    resp = client.get("/api/v1/test-only/hangup-and-worse")
    assert resp.status_code == 500
    assert len(reports) == 1, "a multi-member group hides a real crash"


def test_unwrap_stops_at_multi_member_groups():
    lone = ClientDisconnect()
    assert unwrap(ExceptionGroup("x", [lone])) is lone
    nested = ExceptionGroup("outer", [ExceptionGroup("inner", [lone])])
    assert unwrap(nested) is lone
    both = ExceptionGroup("x", [ClientDisconnect(), RuntimeError("real")])
    assert unwrap(both) is both
    assert not is_client_disconnect(both)
