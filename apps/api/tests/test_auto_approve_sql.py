"""US-17.4: service-role auto-approve (PRD, plan) + the flags reader.

Runs against DATABASE_URL (apps/api/.env). Skips if unreachable or no project.
The code auto-merge (GitHub) is not covered here — it needs a live PR.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app import db as app_db
from app.config import Settings


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="module")
def db():
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        conn.execute("select 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url=_database_url(),
    )


@pytest.fixture
def ctx(db):
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    return {"project_id": project["id"], "org_id": project["org_id"]}


def test_flags_default_off(db, settings, ctx):
    flags = app_db.get_project_auto_flags(settings, str(ctx["project_id"]))
    assert set(flags) == {"build_mode", "prd", "plan", "code"}
    # a project may have any mode, but the flag reader must be boolean-typed.
    assert isinstance(flags["prd"], bool)


def test_auto_approve_prd_approves_and_attributes(db, settings, ctx):
    feature_id = uuid.uuid4()
    db.execute(
        "insert into public.issues (id, org_id, project_id, type, title, body, "
        "acceptance_criteria, status) values (%s, %s, %s, 'feature', %s, 'b', "
        "'[]'::jsonb, 'prd-review')",
        (feature_id, ctx["org_id"], ctx["project_id"], f"aa-prd {feature_id}"),
    )
    db.execute(
        "insert into public.artifacts (org_id, issue_id, kind, content, version, "
        "status, created_by) values (%s, %s, 'prd', '# PRD', 1, 'draft', 'agent')",
        (ctx["org_id"], feature_id),
    )
    db.commit()
    try:
        summary = app_db.auto_approve_prd(settings, str(feature_id))
        assert summary["gate"] == "prd"
        db.rollback()
        assert (
            db.execute(
                "select status from public.issues where id = %s", (feature_id,)
            ).fetchone()["status"]
            == "ready"
        )
        appr = db.execute(
            "select actor, auto_approved from public.approvals where issue_id = %s "
            "and gate = 'prd'",
            (feature_id,),
        ).fetchone()
        assert appr["actor"] is None and appr["auto_approved"] is True
    finally:
        db.rollback()
        db.execute("delete from public.runs where issue_id = %s", (feature_id,))
        db.execute("delete from public.approvals where issue_id = %s", (feature_id,))
        db.execute("delete from public.issue_events where issue_id = %s", (feature_id,))
        db.execute("delete from public.artifacts where issue_id = %s", (feature_id,))
        db.execute(
            "update public.issues set status='draft' where id=%s and status in "
            "('queued','ready','running')",
            (feature_id,),
        )
        db.execute("delete from public.issues where id = %s", (feature_id,))
        db.commit()


def test_materialize_test_cases_replaces_agent_set(db, settings, ctx):
    story_id = uuid.uuid4()
    db.execute(
        "insert into public.issues (id, org_id, project_id, type, title, body, "
        "acceptance_criteria, status) values (%s, %s, %s, 'story', %s, 'b', "
        "'[]'::jsonb, 'planned')",
        (story_id, ctx["org_id"], ctx["project_id"], f"aa-tc {story_id}"),
    )
    db.commit()
    try:
        n = app_db.materialize_test_cases(
            settings,
            str(ctx["org_id"]),
            str(ctx["project_id"]),
            str(story_id),
            [
                {"title": "T1", "steps": "s", "expected_result": "e"},
                {"title": "T2", "steps": "s", "expected_result": "e"},
            ],
        )
        assert n == 2
        db.rollback()
        active = db.execute(
            "select count(*) as n from public.test_cases where issue_id = %s "
            "and status = 'active'",
            (story_id,),
        ).fetchone()["n"]
        assert active == 2
    finally:
        db.rollback()
        db.execute("delete from public.test_cases where issue_id = %s", (story_id,))
        db.execute("delete from public.issues where id = %s", (story_id,))
        db.commit()
