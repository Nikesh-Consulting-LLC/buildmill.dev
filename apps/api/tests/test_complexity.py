"""US-7.1: advisory complexity scoring — the scorer parses/validates the
strict-JSON contract, the endpoint gates on dispatchable type, and failures are
best-effort (values untouched, caller never fails)."""

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app import complexity, llm
from app.config import Settings
from app.llm import LlmCallError

SETTINGS = Settings(
    supabase_url="https://test.supabase.co",
    supabase_publishable_key="sb_publishable_test",
    cors_origins="http://localhost:3000",
    database_url="postgresql://test",
)


def _run(coro):
    return asyncio.run(coro)


def _reply(text, model="claude-x"):
    async def fake(settings, org_id, function_key, *, messages, temperature=None, timeout=None):
        fake.captured = {"fn": function_key}
        return SimpleNamespace(text=text, model=model, provider_name="p")

    return fake


def test_score_story_basis_parses_contract(monkeypatch):
    monkeypatch.setattr(
        llm,
        "complete_as_org",
        _reply(
            '{"complexity":"medium","touches_critical":true,'
            '"data_model_impact":"backward_compatible","rationale":"adds a column"}'
        ),
    )
    out = _run(
        llm.score_complexity(
            SETTINGS, "org-1", basis="story", item_type="story",
            title="Add CSV export", details="…",
        )
    )
    assert out["complexity"] == "medium"
    assert out["touches_critical"] is True
    assert out["data_model_impact"] == "backward_compatible"
    assert out["model"] == "claude-x"


def test_plan_basis_uses_plan_function(monkeypatch):
    fake = _reply(
        '{"complexity":"high","touches_critical":false,'
        '"data_model_impact":"needs_migration","rationale":"backfills"}'
    )
    monkeypatch.setattr(llm, "complete_as_org", fake)
    _run(
        llm.score_complexity(
            SETTINGS, "org-1", basis="plan", item_type="story",
            title="t", details="d", implementation_plan="p", test_plan="tp",
        )
    )
    assert fake.captured["fn"] == "plan_complexity_score"


def test_invalid_enum_raises(monkeypatch):
    monkeypatch.setattr(
        llm,
        "complete_as_org",
        _reply('{"complexity":"impossible","touches_critical":false,'
               '"data_model_impact":"none","rationale":"x"}'),
    )
    with pytest.raises(LlmCallError):
        _run(
            llm.score_complexity(
                SETTINGS, "org-1", basis="story", item_type="bug",
                title="t", details="d",
            )
        )


def test_malformed_json_raises(monkeypatch):
    monkeypatch.setattr(llm, "complete_as_org", _reply("not json at all"))
    with pytest.raises(LlmCallError):
        _run(
            llm.score_complexity(
                SETTINGS, "org-1", basis="story", item_type="chore",
                title="t", details="d",
            )
        )


def test_score_and_store_is_best_effort_on_no_provider(monkeypatch):
    monkeypatch.setattr(
        complexity.db,
        "get_issue_scoring_context",
        lambda s, i: {
            "org_id": "org-1", "type": "story", "title": "t",
            "body": "b", "acceptance_criteria": [], "complexity_basis": None,
            "plan": None, "test_plan": None,
        },
    )

    async def boom(*a, **k):
        raise llm.LlmNotConfigured()

    monkeypatch.setattr(complexity.llm, "score_complexity", boom)
    assert _run(complexity.score_and_store_issue(SETTINGS, str(uuid.uuid4()))) is False


def test_score_and_store_skips_downgrade(monkeypatch):
    """A story-level call is skipped when the current basis is already plan."""
    monkeypatch.setattr(
        complexity.db,
        "get_issue_scoring_context",
        lambda s, i: {
            "org_id": "org-1", "type": "story", "title": "t",
            "body": "b", "acceptance_criteria": [], "complexity_basis": "plan",
            "plan": None, "test_plan": None,
        },
    )
    called = {"scored": False}

    async def should_not_run(*a, **k):
        called["scored"] = True
        return {}

    monkeypatch.setattr(complexity.llm, "score_complexity", should_not_run)
    out = _run(
        complexity.score_and_store_issue(SETTINGS, str(uuid.uuid4()), basis="story")
    )
    assert out is False
    assert called["scored"] is False


def test_endpoint_skips_non_dispatchable(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"type": "feature"}]

    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/issues/{uuid.uuid4()}/complexity-score",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"scored": False}


def test_endpoint_scores_dispatchable(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"type": "story"}]

    async def fake_score(settings, issue_id, **kw):
        return True

    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.routers.issues.complexity.score_and_store_issue", fake_score
    )
    resp = client.post(
        f"/api/v1/issues/{uuid.uuid4()}/complexity-score",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"scored": True}
