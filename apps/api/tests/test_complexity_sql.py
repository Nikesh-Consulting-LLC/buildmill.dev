"""US-7.1: live SQL coverage — set_issue_complexity writes the estimate and
never downgrades a 'plan' basis with a later 'story' call.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

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
def issue(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    proj = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/cx-test') returning id, org_id",
        (org["id"], f"cx-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    row = db.execute(
        "insert into public.issues (org_id, project_id, title, type) "
        "values (%s, %s, 'Score me', 'story') returning id",
        (proj["org_id"], proj["id"]),
    ).fetchone()
    db.commit()
    yield proj, row
    db.rollback()
    db.execute("delete from public.issues where project_id = %s", (proj["id"],))
    db.execute("delete from public.epics where project_id = %s", (proj["id"],))
    db.execute("delete from public.projects where id = %s", (proj["id"],))
    db.commit()


def _complexity(db, issue_id):
    return db.execute(
        "select complexity, touches_critical, data_model_impact, "
        "complexity_basis, complexity_model from public.issues where id = %s",
        (issue_id,),
    ).fetchone()


def test_write_and_readback(db, settings, issue):
    _proj, row = issue
    app_db.set_issue_complexity(
        settings, str(row["id"]),
        complexity="low", touches_critical=False,
        data_model_impact="none", rationale="tiny", basis="story", model="m1",
    )
    got = _complexity(db, row["id"])
    assert got["complexity"] == "low"
    assert got["complexity_basis"] == "story"
    assert got["complexity_model"] == "m1"


def test_plan_basis_not_downgraded_by_story(db, settings, issue):
    _proj, row = issue
    # First a plan-basis estimate.
    app_db.set_issue_complexity(
        settings, str(row["id"]),
        complexity="high", touches_critical=True,
        data_model_impact="needs_migration", rationale="from plan",
        basis="plan", model="planner",
    )
    # A later story-basis call must NOT overwrite it.
    app_db.set_issue_complexity(
        settings, str(row["id"]),
        complexity="trivial", touches_critical=False,
        data_model_impact="none", rationale="from story",
        basis="story", model="storyer",
    )
    got = _complexity(db, row["id"])
    assert got["complexity"] == "high"
    assert got["complexity_basis"] == "plan"

    # But a fresh plan-basis call CAN overwrite.
    app_db.set_issue_complexity(
        settings, str(row["id"]),
        complexity="medium", touches_critical=False,
        data_model_impact="backward_compatible", rationale="re-planned",
        basis="plan", model="planner2",
    )
    got = _complexity(db, row["id"])
    assert got["complexity"] == "medium"
    assert got["complexity_model"] == "planner2"
