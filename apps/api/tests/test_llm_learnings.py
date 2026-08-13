"""US-1.21: POST /llm/learnings/{project_id}/update."""

import uuid
from types import SimpleNamespace

from app import llm as llm_module

PROJECT_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


def _config(monkeypatch, provider="anthropic", model="claude-sonnet-5"):
    """One default provider serving every function (US-3.17 shape)."""

    async def fake_postgrest_get(settings, token, path, params):
        if path == "llm_providers":
            return [
                {
                    "id": "p1",
                    "org_id": ORG_ID,
                    "name": provider.capitalize(),
                    "provider_type": provider,
                    "base_url": None,
                    "models": [model],
                    "is_default": True,
                    "default_model": model,
                    "vault_secret_id": "11111111-2222-3333-4444-555555555555",
                }
            ]
        if path == "llm_function_routes":
            return []
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(llm_module, "postgrest_get", fake_postgrest_get)


def _completion_reply(text):
    async def fake_acompletion(**kwargs):
        fake_acompletion.captured = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
        )

    return fake_acompletion


def _patch_postgrest(monkeypatch, *, existing_content=None, captured_upsert=None):
    async def fake_get(settings, token, path, params):
        if path == "projects":
            return [{"org_id": ORG_ID}]
        if path == "project_learnings":
            if existing_content is None:
                return []
            return [{"content": existing_content}]
        raise AssertionError(f"unexpected path {path}")

    async def fake_upsert(settings, token, path, body, on_conflict):
        assert path == "project_learnings"
        assert on_conflict == "project_id"
        if captured_upsert is not None:
            captured_upsert.update(body)
        return [{**body, "updated_at": "2026-01-01T00:00:00Z"}]

    monkeypatch.setattr("app.routers.llm.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.llm.postgrest_upsert", fake_upsert)


def test_update_learnings_requires_auth(client):
    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update", json={"context": "x"}
    )
    assert resp.status_code == 401


def test_update_learnings_merges_and_persists(client, make_token, monkeypatch):
    captured = {}
    _patch_postgrest(
        monkeypatch, existing_content="## Old\n\nStale note.", captured_upsert=captured
    )
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    fake = _completion_reply("## Old\n\nUpdated note.\n\n## New\n\nJust learned this.")
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update",
        json={"context": "Just learned this."},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "Just learned this." in body["content"]
    assert body["last_updated_by"] == "llm"

    assert captured["org_id"] == ORG_ID
    assert captured["project_id"] == PROJECT_ID
    assert captured["last_updated_by"] == "llm"
    # the existing content was passed to the LLM as context to merge into
    assert "Stale note" in fake.captured["messages"][0]["content"]


def test_update_learnings_no_existing_row_starts_from_blank(client, make_token, monkeypatch):
    _patch_postgrest(monkeypatch, existing_content=None)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    fake = _completion_reply("## First learning\n\nSomething new.")
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update",
        json={"context": "Something new."},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert "(nothing yet)" in fake.captured["messages"][0]["content"]


def test_update_learnings_project_not_found_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.llm.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update",
        json={"context": "x"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_update_learnings_no_settings_is_409(client, make_token, monkeypatch):
    _patch_postgrest(monkeypatch, existing_content="")

    async def no_providers(settings, token, path, params):
        return []

    monkeypatch.setattr(llm_module, "postgrest_get", no_providers)

    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update",
        json={"context": "x"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
    assert "Settings" in resp.json()["detail"]


def test_update_learnings_provider_error_is_502_and_leaves_content_unchanged(
    client, make_token, monkeypatch
):
    upsert_called = False

    async def fake_upsert(*args, **kwargs):
        nonlocal upsert_called
        upsert_called = True
        return [{}]

    _patch_postgrest(monkeypatch, existing_content="unchanged")
    monkeypatch.setattr("app.routers.llm.postgrest_upsert", fake_upsert)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")

    async def boom(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm_module.litellm, "acompletion", boom)

    resp = client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/update",
        json={"context": "x"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 502
    assert "rate limited" in resp.json()["detail"]
    assert upsert_called is False


# ----------------- US-5.31: POST /llm/learnings/{p}/submissions/{s}/decide

SUB_ID = str(uuid.uuid4())


def _patch_decide_postgrest(
    monkeypatch,
    *,
    sub_status="pending",
    existing_content="## Old\n\nStale note.",
    captured_upsert=None,
):
    async def fake_get(settings, token, path, params):
        if path == "learning_submissions":
            return [
                {
                    "id": SUB_ID,
                    "org_id": ORG_ID,
                    "text": "Node 22 required.",
                    "status": sub_status,
                }
            ]
        if path == "project_learnings":
            if existing_content is None:
                return []
            return [{"content": existing_content}]
        raise AssertionError(f"unexpected path {path}")

    async def fake_upsert(settings, token, path, body, on_conflict):
        assert path == "project_learnings"
        if captured_upsert is not None:
            captured_upsert.update(body)
        return [body]

    monkeypatch.setattr("app.routers.llm.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.llm.postgrest_upsert", fake_upsert)


def _decide(client, make_token, decision, note=""):
    return client.post(
        f"/api/v1/llm/learnings/{PROJECT_ID}/submissions/{SUB_ID}/decide",
        json={"decision": decision, "note": note},
        headers={"Authorization": f"Bearer {make_token()}"},
    )


def test_decide_approve_merges_and_stamps(client, make_token, monkeypatch):
    captured_upsert: dict = {}
    stamped: dict = {}
    _patch_decide_postgrest(monkeypatch, captured_upsert=captured_upsert)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")
    fake = _completion_reply("## Old\n\nStale note.\n\n## Node\n\nNode 22 required.")
    monkeypatch.setattr(llm_module.litellm, "acompletion", fake)

    def fake_decide(settings, sub_id, status, decided_by, note=None):
        stamped.update(sub_id=sub_id, status=status, decided_by=decided_by, note=note)
        return True

    monkeypatch.setattr("app.db.decide_learning_submission", fake_decide)

    resp = _decide(client, make_token, "approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"
    # the submission text was merged into the existing document
    assert "Stale note" in fake.captured["messages"][0]["content"]
    assert "Node 22 required." in fake.captured["messages"][0]["content"]
    assert captured_upsert["last_updated_by"] == "llm"
    assert stamped["status"] == "approved"
    assert stamped["sub_id"] == SUB_ID


def test_decide_reject_leaves_document_untouched(client, make_token, monkeypatch):
    stamped: dict = {}

    async def never_upsert(*a, **k):
        raise AssertionError("reject must not write the learnings document")

    _patch_decide_postgrest(monkeypatch)
    monkeypatch.setattr("app.routers.llm.postgrest_upsert", never_upsert)

    async def never_completion(**kwargs):
        raise AssertionError("reject must not call the LLM")

    monkeypatch.setattr(llm_module.litellm, "acompletion", never_completion)

    def fake_decide(settings, sub_id, status, decided_by, note=None):
        stamped.update(status=status, note=note)
        return True

    monkeypatch.setattr("app.db.decide_learning_submission", fake_decide)

    resp = _decide(client, make_token, "reject", note="not durable")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert stamped == {"status": "rejected", "note": "not durable"}


def test_decide_merge_failure_keeps_submission_pending(
    client, make_token, monkeypatch
):
    _patch_decide_postgrest(monkeypatch)
    _config(monkeypatch)
    monkeypatch.setattr(llm_module, "read_vault_secret", lambda s, sid: "sk-test")

    async def boom(**kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(llm_module.litellm, "acompletion", boom)

    def never_decide(*a, **k):
        raise AssertionError("a failed merge must not stamp the submission")

    monkeypatch.setattr("app.db.decide_learning_submission", never_decide)

    resp = _decide(client, make_token, "approve")
    assert resp.status_code == 502
    assert "stays pending" in resp.json()["detail"]


def test_decide_already_decided_is_409(client, make_token, monkeypatch):
    _patch_decide_postgrest(monkeypatch, sub_status="approved")
    resp = _decide(client, make_token, "approve")
    assert resp.status_code == 409
    assert "already approved" in resp.json()["detail"]


def test_decide_cross_org_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []  # RLS hides other orgs' submissions

    monkeypatch.setattr("app.routers.llm.postgrest_get", fake_get)
    resp = _decide(client, make_token, "approve")
    assert resp.status_code == 404
