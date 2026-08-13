"""US-79.5: the database not answering is a 504 in words (prod BUG-6).

Twice in prod a `httpx.ConnectTimeout` to Supabase climbed out unhandled: a
naked 500 for the manager, a raw traceback for the inbox. Every PostgREST
request now leaves through `supabase._send`, which names the moment
(`SupabaseUnreachable`), retries reads once, and never blind-retries a write.
"""

import asyncio

import httpx
import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import app_issues, supabase
from app.main import app
from app.supabase import (
    SupabaseUnreachable,
    postgrest_get,
    postgrest_post,
    rpc,
)

_router = APIRouter()


@_router.get("/api/v1/test-only/db-unreachable")
def _db_unreachable():
    raise SupabaseUnreachable("RPC release_signoff_blocker", "ConnectTimeout")


app.include_router(_router)


class _Recorder:
    """Stands in for httpx.AsyncClient; fails the first `failures` requests."""

    calls = 0
    failures = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def request(self, method, url, **kwargs):
        cls = type(self)
        cls.calls += 1
        if cls.calls <= cls.failures:
            raise httpx.ConnectTimeout("no route to Supabase")
        return httpx.Response(
            200, json=[], request=httpx.Request(method, url)
        )


@pytest.fixture()
def transport(monkeypatch):
    _Recorder.calls = 0
    _Recorder.failures = 0
    monkeypatch.setattr(supabase.httpx, "AsyncClient", _Recorder)
    monkeypatch.setattr(supabase, "_RETRY_BACKOFF_SECONDS", 0)
    return _Recorder


def test_a_read_retries_once_then_names_the_failure(transport, settings_override):
    transport.failures = 99
    with pytest.raises(SupabaseUnreachable) as exc_info:
        asyncio.run(postgrest_get(settings_override, "t", "issues", {"select": "id"}))
    assert transport.calls == 2, "a read should be asked exactly twice"
    assert exc_info.value.operation == "GET issues"
    assert exc_info.value.cause == "ConnectTimeout"
    # The operation names the table, never the query values.
    assert "select" not in str(exc_info.value)


def test_a_read_that_recovers_on_the_retry_answers(transport, settings_override):
    transport.failures = 1
    rows = asyncio.run(
        postgrest_get(settings_override, "t", "issues", {"select": "id"})
    )
    assert rows == []
    assert transport.calls == 2


def test_a_write_is_never_blind_retried(transport, settings_override):
    transport.failures = 99
    with pytest.raises(SupabaseUnreachable):
        asyncio.run(postgrest_post(settings_override, "t", "issues", {"title": "x"}))
    assert transport.calls == 1, "a timed-out write must not be sent twice"


def test_an_rpc_is_never_blind_retried(transport, settings_override):
    transport.failures = 99
    with pytest.raises(SupabaseUnreachable) as exc_info:
        asyncio.run(rpc(settings_override, "t", "release_signoff_blocker", {}))
    assert transport.calls == 1
    assert exc_info.value.operation == "RPC release_signoff_blocker"


# --- the answer on the wire --------------------------------------------------


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


def test_the_answer_is_a_504_in_words(client, reports):
    resp = client.get("/api/v1/test-only/db-unreachable")
    assert resp.status_code == 504
    detail = resp.json()["detail"]
    assert detail == (
        "the database did not answer (ConnectTimeout) — "
        "nothing was recorded; try again"
    )
    # Still an incident: reported, titled by the typed error, operation attached.
    assert len(reports) == 1
    assert reports[0]["error_type"] == "SupabaseUnreachable"
    assert reports[0]["context"]["operation"] == "RPC release_signoff_blocker"
