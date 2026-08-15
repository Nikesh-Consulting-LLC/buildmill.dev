"""US-100.4: a template holds the files a project will publish — and only those.

The superadmin template routes used to accept three section types. Two of
them stopped meaning anything (guideline sections became the Agent
Instructions document, us-100.1; prompt sections are platform-global LLM
prompts no agent reads), and the rebuilt editors no longer offer them. These
pin the server side of that: what can be WRITTEN is narrowed, the document
travels with the row, and the count the list shows is filled files.

Existing guideline/prompt rows are deliberately left in the database
(migration 265 deletes nothing) — so the routes must refuse new writes to
those types without pretending the rows are gone.
"""

from __future__ import annotations

import pytest

TEMPLATE_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture(autouse=True)
def as_platform_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        assert fn == "is_platform_admin"
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


# --- AC6: the section_type guard ------------------------------------------


@pytest.mark.parametrize("retired", ["guideline", "prompt"])
def test_writing_a_retired_section_type_is_refused_with_the_reason(
    client, make_token, monkeypatch, retired
):
    async def must_not_write(*a, **k):
        raise AssertionError(f"a {retired} section must not be written")

    monkeypatch.setattr("app.routers.admin.admin_upsert", must_not_write)
    resp = client.put(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}/sections/{retired}/anything",
        json={"title": "t", "content": "c", "sort_order": 0},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "retired" in detail
    # The refusal says where the content now lives, so it is not a dead end.
    assert "agent_instructions" in detail or "prompt-templates" in detail


def test_an_unknown_section_type_is_still_unknown(client, make_token, monkeypatch):
    async def must_not_write(*a, **k):
        raise AssertionError("nothing should be written")

    monkeypatch.setattr("app.routers.admin.admin_upsert", must_not_write)
    resp = client.put(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}/sections/banana/x",
        json={"content": "c"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "unknown" in resp.json()["detail"]


def test_a_worker_instruction_still_writes(client, make_token, monkeypatch):
    written = {}

    async def fake_upsert(settings, path, rows, on_conflict):
        written["path"] = path
        written["rows"] = rows
        written["on_conflict"] = on_conflict
        return rows

    monkeypatch.setattr("app.routers.admin.admin_upsert", fake_upsert)
    resp = client.put(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}/sections/worker_instruction/code",
        json={"title": "", "content": "Build it well.", "sort_order": 0},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert written["path"] == "project_template_sections"
    assert written["rows"][0]["section_type"] == "worker_instruction"
    assert written["rows"][0]["section_key"] == "code"
    assert written["rows"][0]["content"] == "Build it well."


# --- AC1: the document rides the row ---------------------------------------


def test_patch_accepts_the_agent_instructions_document(client, make_token, monkeypatch):
    patched = {}

    async def fake_get(settings, path, params):
        return [{"id": TEMPLATE_ID, "is_default": False, "is_disabled": False}]

    async def fake_patch(settings, path, params, body):
        patched["path"] = path
        patched["params"] = params
        patched["body"] = body
        return [{"id": TEMPLATE_ID, **body}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_patch", fake_patch)
    resp = client.patch(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}",
        json={"agent_instructions": "# Conventions\n\nUse tabs."},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert patched["path"] == "project_templates"
    assert patched["body"] == {"agent_instructions": "# Conventions\n\nUse tabs."}


def test_duplicate_carries_the_document_and_only_the_files(
    client, make_token, monkeypatch
):
    """A duplicate is a new template. It inherits what the source publishes
    — the document and the per-task files — and not the retired
    guideline/prompt rows, which are rollback data on the source."""
    calls: list[tuple[str, dict]] = []

    async def fake_get(settings, path, params):
        calls.append(("get", {"path": path, **params}))
        if path == "project_templates" and params.get("select") == "*":
            return [
                {
                    "id": TEMPLATE_ID,
                    "key": "web",
                    "name": "Web",
                    "description": "d",
                    "category": "c",
                    "agent_instructions": "# Doc",
                }
            ]
        if path == "project_templates":
            return [{"key": "web"}]
        if path == "project_template_sections":
            assert params.get("section_type") == "eq.worker_instruction", (
                "duplicate must read only the files, not retired rows"
            )
            return [
                {
                    "section_type": "worker_instruction",
                    "section_key": "code",
                    "title": "",
                    "content": "Build.",
                    "sort_order": 0,
                }
            ]
        raise AssertionError(path)

    posted: list[tuple[str, object]] = []

    async def fake_post(settings, path, body):
        posted.append((path, body))
        if path == "project_templates":
            return [{**body, "id": "22222222-2222-2222-2222-222222222222"}]
        return body

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_post", fake_post)
    resp = client.post(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}/duplicate",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    template_post = next(b for p, b in posted if p == "project_templates")
    assert template_post["agent_instructions"] == "# Doc"
    assert template_post["key"] == "web-copy"
    section_post = next(b for p, b in posted if p == "project_template_sections")
    assert [s["section_type"] for s in section_post] == ["worker_instruction"]


# --- the count is filled files -----------------------------------------------


def test_the_list_counts_filled_files_document_included(client, make_token, monkeypatch):
    async def fake_get(settings, path, params):
        if path == "project_templates":
            return [
                {"id": "a", "name": "A", "agent_instructions": "# Doc"},
                {"id": "b", "name": "B", "agent_instructions": "   "},
            ]
        assert path == "project_template_sections"
        # Only worker_instruction rows with content are files.
        assert params["section_type"] == "eq.worker_instruction"
        assert params["content"] == "neq."
        return [{"template_id": "a"}, {"template_id": "a"}, {"template_id": "b"}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    resp = client.get("/api/v1/admin/project-templates", headers=_auth(make_token))
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()}
    assert by_id["a"]["file_count"] == 3  # doc + 2 files
    assert by_id["b"]["file_count"] == 1  # blank doc + 1 file
    assert "section_count" not in by_id["a"]


# --- AC3: the copy path carries the document ---------------------------------


def test_migration_267_copies_the_document_and_only_the_files():
    from pathlib import Path

    sql = (
        Path(__file__).resolve().parents[3]
        / "infra/supabase/migrations/267_the_copy_carries_the_document.sql"
    ).read_text(encoding="utf-8")
    assert "copy_project_template_into_org" in sql
    assert "agent_instructions" in sql
    assert "s.section_type = 'worker_instruction'" in sql
    for verb in ("drop column", "drop table", "delete from"):
        assert verb not in sql.lower(), f"267 must not {verb}"
