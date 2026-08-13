"""US-7.3: live SQL coverage — the dev-branch resolver for all three
strategies (story leaf, feature-with-stories on the parent branch, main) and
that a stored branch_ref wins. Also that the project release-branch columns
round-trip.

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
def project(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/branch-test') returning id, org_id",
        (org["id"], f"branch-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute("delete from public.issues where project_id = %s", (row["id"],))
    db.execute("delete from public.epics where project_id = %s", (row["id"],))
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def _issue(db, project, title, type_, parent_id=None):
    return db.execute(
        "insert into public.issues (org_id, project_id, title, type, parent_id) "
        "values (%s, %s, %s, %s, %s) returning id",
        (project["org_id"], project["id"], title, type_, parent_id),
    ).fetchone()["id"]


def _run(**kw):
    base = {
        "issue_id": kw.get("issue_id"),
        "issue_title": kw.get("issue_title", "Some Title"),
        "parent_id": kw.get("parent_id"),
        "input_context": {},
        "branch_ref": kw.get("branch_ref"),
        "dev_branch_strategy": kw.get("dev_branch_strategy", "story"),
        "default_branch": kw.get("default_branch", "main"),
    }
    return base


def test_story_strategy_names_from_story_title(db, settings, project):
    story = _issue(db, project, "Add offline photo sync", "story")
    db.commit()
    branch, strategy, mode = app_db.resolve_working_branch(
        settings,
        _run(issue_id=story, issue_title="Add offline photo sync",
             dev_branch_strategy="story"),
    )
    assert strategy == "story"
    assert mode == "pr"
    assert branch.startswith("factory/add-offline-photo-sync-")


def test_work_item_strategy_names_from_parent(db, settings, project):
    feature = _issue(db, project, "Photo library revamp", "feature")
    story = _issue(db, project, "A tiny story", "story", parent_id=feature)
    db.commit()
    branch, strategy, mode = app_db.resolve_working_branch(
        settings,
        _run(issue_id=story, issue_title="A tiny story", parent_id=feature,
             dev_branch_strategy="work_item"),
    )
    assert strategy == "work_item"
    assert mode == "pr"
    # the branch derives from the PARENT feature's title, so the feature's
    # stories share one branch.
    assert branch.startswith("factory/photo-library-revamp-")
    assert str(feature)[:6] in branch


def test_main_strategy_uses_default_branch_direct(db, settings, project):
    story = _issue(db, project, "Whatever", "story")
    db.commit()
    branch, strategy, mode = app_db.resolve_working_branch(
        settings,
        _run(issue_id=story, issue_title="Whatever",
             dev_branch_strategy="main", default_branch="trunk"),
    )
    assert strategy == "main"
    assert mode == "direct"
    assert branch == "trunk"


def test_stored_branch_ref_wins(db, settings, project):
    story = _issue(db, project, "Renamed since", "story")
    db.commit()
    branch, _strategy, _mode = app_db.resolve_working_branch(
        settings,
        _run(issue_id=story, issue_title="Renamed since",
             dev_branch_strategy="story", branch_ref="factory/frozen-name-abc123"),
    )
    assert branch == "factory/frozen-name-abc123"


def test_release_branch_columns_round_trip(db, project):
    db.execute(
        "update public.projects set uat_branch = 'release/uat', "
        "production_branch = 'release/prod', dev_branch_strategy = 'work_item' "
        "where id = %s",
        (project["id"],),
    )
    db.commit()
    row = db.execute(
        "select uat_branch, production_branch, dev_branch_strategy "
        "from public.projects where id = %s",
        (project["id"],),
    ).fetchone()
    assert row["uat_branch"] == "release/uat"
    assert row["production_branch"] == "release/prod"
    assert row["dev_branch_strategy"] == "work_item"
