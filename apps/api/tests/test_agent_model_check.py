"""us-116.6: what a NEW agent with these roles would resolve — the wizard's
pre-creation warning, from the same resolver a run and a session use."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import db, model_resolution
from app.auth import AuthUser, verify_token
from app.config import get_settings
from app.main import app
from app.routers import agents

ORG = str(uuid.uuid4())


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[verify_token] = lambda: AuthUser(
        id="actor-1", email="me@example.com", token="jwt"
    )
    app.dependency_overrides[get_settings] = lambda: object()

    async def fake_get(settings, token, table, params):
        assert table == "organizations"
        return [{"id": ORG}] if params.get("id") == f"eq.{ORG}" else []

    monkeypatch.setattr(agents, "postgrest_get", fake_get)
    monkeypatch.setattr(db, "presets_by_id", lambda s, o: {})
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_it_answers_the_floor_for_a_blank_agent(client, monkeypatch):
    monkeypatch.setattr(db, "org_default_preset", lambda s, o: {"id": "p", "name": "Balanced", "model": None,
                                                                "settings": {}, "version": 1, "tool_grants": []})
    monkeypatch.setattr(db, "org_default_provider_model", lambda s, o: "grok-4.6")
    r = client.get(f"/api/v1/agents/model-check?org={ORG}&kinds=release,deploy")
    assert r.status_code == 200, r.text
    assert r.json() == {"resolves": True, "model": "grok-4.6", "kind": "release",
                        "source": "org-default-provider", "tried": ["release"]}


def test_it_says_when_nothing_would_resolve(client, monkeypatch):
    monkeypatch.setattr(db, "org_default_preset", lambda s, o: None)
    monkeypatch.setattr(db, "org_default_provider_model", lambda s, o: None)
    r = client.get(f"/api/v1/agents/model-check?org={ORG}&kinds=code")
    assert r.json()["resolves"] is False
    assert r.json()["tried"] == ["code"]


def test_it_uses_the_same_resolver_as_runs_and_sessions(client, monkeypatch):
    seen = {}

    def spy(inputs):
        seen["config"] = inputs.config
        return model_resolution.SessionModel(model="m", kind="code", resolved=None, tried=["code"])

    monkeypatch.setattr(model_resolution, "resolve_session", spy)
    monkeypatch.setattr(db, "org_default_preset", lambda s, o: None)
    monkeypatch.setattr(db, "org_default_provider_model", lambda s, o: None)
    client.get(f"/api/v1/agents/model-check?org={ORG}&kinds=code,test")
    assert seen["config"]["enabled_kinds"] == ["code", "test"]
    assert seen["config"]["model_overrides"] == {}


def test_an_org_the_caller_cannot_see_is_404(client):
    assert client.get(f"/api/v1/agents/model-check?org={uuid.uuid4()}&kinds=code").status_code == 404
