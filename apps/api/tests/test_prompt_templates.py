"""US-5.17: prompt-template resolution + superadmin endpoints.

Endpoint-level: db and the platform-admin RPC are monkeypatched; the SQL
side (override chains, RPC scoping, seed behavior) is covered in
test_prompt_templates_sql.py.
"""

import pytest

from app import llm

HDR = None  # set per-test via make_token


@pytest.fixture
def as_superadmin(monkeypatch):
    monkeypatch.setattr(
        "app.routers.admin.rpc", _rpc_true := _async_const(True)
    )
    return _rpc_true


def _async_const(value):
    async def _f(*args, **kwargs):
        return value

    return _f


@pytest.fixture(autouse=True)
def stub_template_db(monkeypatch):
    monkeypatch.setattr("app.db.list_prompt_overrides", lambda s: [])
    monkeypatch.setattr(
        "app.db.get_baked_worker_instructions",
        lambda s: {k: f"baked {k}" for k in ("prd", "plan", "code")},
    )
    monkeypatch.setattr(
        "app.db.get_baked_guideline_sections",
        lambda s, keys: {k: f"baked {k}" for k in keys},
    )


# ------------------------------------------------------------- resolution


def test_render_template_substitutes_only_known_variables():
    text = 'Use {existing} but keep {"json": "braces"} and {unknown} alone.'
    out = llm.render_template(text, {"existing": "X"})
    assert "Use X" in out
    assert '{"json": "braces"}' in out
    assert "{unknown}" in out


def test_resolve_prompt_serves_override_live(settings_override, monkeypatch):
    monkeypatch.setattr(
        "app.db.get_prompt_override", lambda s, k: "Override for {existing}!"
    )
    out = llm.resolve_prompt(
        settings_override, "learnings_merge", {"existing": "doc", "context": "c"}
    )
    assert out == "Override for doc!"


def test_resolve_prompt_absent_and_blank_fall_back(settings_override, monkeypatch):
    monkeypatch.setattr("app.db.get_prompt_override", lambda s, k: None)
    out = llm.resolve_prompt(
        settings_override, "learnings_merge", {"existing": "doc", "context": "c"}
    )
    assert "lessons learned" in out
    assert "doc" in out


def test_resolve_prompt_bad_override_falls_back_with_warning(
    settings_override, monkeypatch, caplog
):
    """An override using a placeholder the code no longer supplies never
    breaks the feature — warn and serve the factory default."""
    monkeypatch.setattr(
        "app.db.get_prompt_override", lambda s, k: "Needs {gone_variable}."
    )
    with caplog.at_level("WARNING"):
        out = llm.resolve_prompt(
            settings_override,
            "learnings_merge",
            {"existing": "doc", "context": "c"},
        )
    assert "lessons learned" in out
    assert "gone_variable" in caplog.text


def test_resolve_prompt_survives_db_failure(settings_override, monkeypatch, caplog):
    def boom(s, k):
        raise RuntimeError("db down")

    monkeypatch.setattr("app.db.get_prompt_override", boom)
    with caplog.at_level("WARNING"):
        out = llm.resolve_prompt(
            settings_override, "story_breakdown", {}
        )
    assert "engineering stories" in out


# -------------------------------------------------------------- endpoints


def test_all_routes_403_for_non_superadmin(client, make_token, monkeypatch):
    monkeypatch.setattr("app.routers.admin.rpc", _async_const(False))
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert (
        client.get("/api/v1/admin/prompt-templates", headers=headers).status_code
        == 403
    )
    assert (
        client.put(
            "/api/v1/admin/prompt-templates/learnings_merge",
            json={"content": "x"},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.delete(
            "/api/v1/admin/prompt-templates/learnings_merge", headers=headers
        ).status_code
        == 403
    )


def test_list_groups_all_template_classes(
    client, make_token, as_superadmin
):
    resp = client.get(
        "/api/v1/admin/prompt-templates",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    keys = {i["key"] for i in body}
    groups = {i["group"] for i in body}
    assert groups == {"thinking", "help"}
    assert "prd_draft" not in keys  # pool-dispatched: no thinking prompt
    # Phase 67 (us-67.2): worker/guideline defaults and the three
    # project-shaped prompts moved to /admin/project-templates.
    assert "story_breakdown" not in keys
    assert "test_case_elaborate" not in keys
    assert "deploy_script_generate" not in keys
    assert "learnings_merge" in keys
    assert "help/overview/intro" in keys  # us-2.30
    assert all(i["default"] for i in body)
    assert all(i["override"] is None for i in body)


def test_upsert_rejects_unknown_placeholders(
    client, make_token, as_superadmin, monkeypatch
):
    captured = {}
    monkeypatch.setattr(
        "app.db.upsert_prompt_override",
        lambda s, k, c, u: captured.update(key=k, content=c)
        or {"prompt_key": k, "content": c, "updated_by": u, "updated_at": "now"},
    )
    headers = {"Authorization": f"Bearer {make_token()}"}

    resp = client.put(
        "/api/v1/admin/prompt-templates/learnings_merge",
        json={"content": "Only {existing} and {bogus}."},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "{bogus}" in resp.json()["detail"]
    assert not captured

    resp = client.put(
        "/api/v1/admin/prompt-templates/learnings_merge",
        json={"content": "Merge {context} into {existing}."},
        headers=headers,
    )
    assert resp.status_code == 200
    assert captured["key"] == "learnings_merge"


def test_upsert_help_templates_skip_placeholder_validation(
    client, make_token, as_superadmin, monkeypatch
):
    """US-2.30: help texts take no variables — braces are literal markdown."""
    monkeypatch.setattr(
        "app.db.upsert_prompt_override",
        lambda s, k, c, u: {
            "prompt_key": k,
            "content": c,
            "updated_by": u,
            "updated_at": "now",
        },
    )
    resp = client.put(
        "/api/v1/admin/prompt-templates/help/pipeline/build",
        json={"content": "The agent builds {whatever} — braces stay literal."},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200


def test_help_override_wins_and_reset_restores(
    client, make_token, as_superadmin, monkeypatch
):
    """US-2.30: default served, then the override wins, then reset (delete)
    restores the factory default — through the generic endpoints."""
    store: dict[str, str] = {}
    monkeypatch.setattr(
        "app.db.upsert_prompt_override",
        lambda s, k, c, u: store.update({k: c})
        or {"prompt_key": k, "content": c, "updated_by": None, "updated_at": "now"},
    )
    monkeypatch.setattr(
        "app.db.delete_prompt_override", lambda s, k: store.pop(k, None) is not None
    )
    monkeypatch.setattr(
        "app.db.list_prompt_overrides",
        lambda s: [
            {"prompt_key": k, "content": c, "updated_by": None, "updated_at": "now"}
            for k, c in store.items()
        ],
    )
    headers = {"Authorization": f"Bearer {make_token()}"}

    def fetch_item():
        body = client.get(
            "/api/v1/admin/prompt-templates", headers=headers
        ).json()
        return next(i for i in body if i["key"] == "help/pipeline/uat")

    item = fetch_item()
    assert item["override"] is None
    assert item["default"]

    resp = client.put(
        "/api/v1/admin/prompt-templates/help/pipeline/uat",
        json={"content": "OVERRIDDEN UAT TEXT"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert fetch_item()["override"]["content"] == "OVERRIDDEN UAT TEXT"

    resp = client.delete(
        "/api/v1/admin/prompt-templates/help/pipeline/uat", headers=headers
    )
    assert resp.status_code == 200
    assert fetch_item()["override"] is None


def test_upsert_unknown_key_and_blank_content(
    client, make_token, as_superadmin
):
    headers = {"Authorization": f"Bearer {make_token()}"}
    assert (
        client.put(
            "/api/v1/admin/prompt-templates/not_a_template",
            json={"content": "x"},
            headers=headers,
        ).status_code
        == 404
    )
    assert (
        client.put(
            "/api/v1/admin/prompt-templates/learnings_merge",
            json={"content": "   "},
            headers=headers,
        ).status_code
        == 422
    )
