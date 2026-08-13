"""US-18.1: content TLDR — registry, summary-only prompt, and reply parsing.

US-25.3 adds the whole-work-item summary alongside it: its own registry entry,
per-type source scoping, the missing-source note, and the source hash that
decides whether a stored summary still stands.
"""

import asyncio

import pytest

from app import llm
from app.config import Settings


@pytest.fixture(autouse=True)
def _no_prompt_override(monkeypatch):
    # resolve_prompt does `from . import db; db.get_prompt_override(...)`; keep it
    # off the (bogus) database so these are pure unit tests.
    from app import db

    monkeypatch.setattr(db, "get_prompt_override", lambda *a, **k: None)


def _settings():
    return Settings(
        supabase_url="https://x.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://user:pass@127.0.0.1:1/none",
    )


def test_content_tldr_is_registered():
    assert "content_tldr" in llm.LLM_FUNCTIONS
    meta = llm.LLM_FUNCTIONS["content_tldr"]
    assert set(meta["variables"]) == {"kind_label", "content"}


def test_tldr_prompt_is_summary_only():
    prompt = llm.resolve_prompt(
        _settings(),
        "content_tldr",
        {"kind_label": "story", "content": "Do X, then Y."},
    )
    assert "SUMMARIZE ONLY" in prompt
    assert "Do X, then Y." in prompt
    assert "story" in prompt


def test_summarize_content_parses_headline_and_bullets(monkeypatch):
    async def fake_complete(*args, **kwargs):
        return llm.LlmResult(
            text='{"headline": "Adds auth", "bullets": ["a role check", "a roster view"]}',
            provider_name="p",
            provider_type="openai",
            model="m",
            used_fallback=False,
        )

    monkeypatch.setattr(llm, "complete", fake_complete)
    out = asyncio.run(
        llm.summarize_content(_settings(), "tok", "some story content", "story")
    )
    assert out["headline"] == "Adds auth"
    assert out["bullets"] == ["a role check", "a roster view"]


def test_summarize_content_rejects_empty_reply(monkeypatch):
    async def fake_complete(*args, **kwargs):
        return llm.LlmResult(
            text='{"headline": "", "bullets": []}',
            provider_name="p",
            provider_type="openai",
            model="m",
            used_fallback=False,
        )

    monkeypatch.setattr(llm, "complete", fake_complete)
    try:
        asyncio.run(llm.summarize_content(_settings(), "tok", "x", "story"))
        raised = False
    except llm.LlmCallError:
        raised = True
    assert raised


# --------------------------------------------------------------------------
# US-25.3: the whole-work-item summary
# --------------------------------------------------------------------------


def test_work_item_tldr_is_registered_and_distinct():
    """It must be its own registry entry, so Admin lists and can reset it
    separately from us-18.1's single-block digest."""
    assert "work_item_tldr" in llm.LLM_FUNCTIONS
    meta = llm.LLM_FUNCTIONS["work_item_tldr"]
    assert set(meta["variables"]) == {"type_label", "sources", "missing_note"}
    assert meta["template"] is not None
    assert meta["template"] != llm.LLM_FUNCTIONS["content_tldr"]["template"]


def test_work_item_prompt_carries_every_source_and_names_the_missing(monkeypatch):
    """A missing source is named in the prompt, not silently dropped — a story
    with no approved plan must not produce a thinner summary the manager
    cannot tell apart from a complete one."""
    captured = {}

    async def fake_complete_as_org(settings, org_id, key, *, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        captured["key"] = key
        captured["org_id"] = org_id
        return llm.LlmResult(
            text='{"headline": "Adds auth", "bullets": ["a role check"]}',
            provider_name="p",
            provider_type="openai",
            model="m",
            used_fallback=False,
        )

    monkeypatch.setattr(llm, "complete_as_org", fake_complete_as_org)
    out = asyncio.run(
        llm.summarize_work_item(
            _settings(),
            "org-1",
            "story",
            [("Story", "Let a manager log in."), ("Acceptance criteria", "- it works")],
            ["an approved plan"],
        )
    )
    assert out["headline"] == "Adds auth"
    assert captured["key"] == "work_item_tldr"
    assert captured["org_id"] == "org-1"
    prompt = captured["prompt"]
    assert "Let a manager log in." in prompt
    assert "- it works" in prompt
    assert "an approved plan" in prompt
    assert "SUMMARIZE ONLY" in prompt


def test_work_item_prompt_omits_the_missing_note_when_nothing_is_missing(monkeypatch):
    captured = {}

    async def fake_complete_as_org(settings, org_id, key, *, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return llm.LlmResult(
            text='{"headline": "h", "bullets": ["b"]}',
            provider_name="p",
            provider_type="openai",
            model="m",
            used_fallback=False,
        )

    monkeypatch.setattr(llm, "complete_as_org", fake_complete_as_org)
    asyncio.run(
        llm.summarize_work_item(
            _settings(), "org-1", "feature", [("Description", "A thing.")], []
        )
    )
    assert "not written yet" not in captured["prompt"]


def test_source_hash_tracks_content_and_headings():
    """The hash is what decides a stored summary is still current, so it has to
    move when any source does — and stay put when none has."""
    from app.routers.llm import _source_hash

    base = [("Story", "one"), ("Approved plan", "two")]
    assert _source_hash(base) == _source_hash([("Story", "one"), ("Approved plan", "two")])
    assert _source_hash(base) != _source_hash([("Story", "one"), ("Approved plan", "2")])
    # A source appearing under a different heading is a different summary.
    assert _source_hash(base) != _source_hash([("Story", "one"), ("Instruction set", "two")])
    # And a source disappearing must not hash the same as it being present.
    assert _source_hash(base) != _source_hash([("Story", "one")])


def test_collect_sources_scopes_by_type(monkeypatch):
    """A feature and a story are different questions — feeding a story's plan
    into a feature summary would bury the PRD."""
    from app.routers import llm as llm_router

    async def fake_get(settings, token, path, params):
        assert path == "artifacts"
        kinds = params["kind"]
        return [{"kind": "prd", "content": "the PRD", "version": 2}] if "prd" in kinds else [
            {"kind": "plan", "content": "the plan", "version": 3}
        ]

    monkeypatch.setattr(llm_router, "postgrest_get", fake_get)

    label, sources, missing = asyncio.run(
        llm_router._collect_sources(
            _settings(),
            "tok",
            {"id": "i1", "type": "feature", "body": "a description"},
        )
    )
    assert label == "feature"
    assert [h for h, _ in sources] == ["Description", "Approved PRD"]
    assert missing == []

    label, sources, missing = asyncio.run(
        llm_router._collect_sources(
            _settings(),
            "tok",
            {
                "id": "i2",
                "type": "story",
                "body": "a story",
                "acceptance_criteria": ["first", "second"],
                "instruction_set": None,
            },
        )
    )
    assert label == "story"
    assert [h for h, _ in sources] == ["Story", "Acceptance criteria", "Approved plan"]
    # The instruction set is absent, so it is named rather than dropped.
    assert missing == ["an instruction set"]
    assert "- first" in dict(sources)["Acceptance criteria"]
