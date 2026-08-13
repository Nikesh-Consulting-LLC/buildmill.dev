"""US-5.23: live SQL coverage — project environment settings round-trip,
rendered markdown, absent-when-empty, and AGENTS.md export inclusion.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
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
def project(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/env-test') returning id, org_id",
        (org["id"], f"env-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def _md(db, project_id):
    return db.execute(
        "select public.project_environment_md(%s) as md", (project_id,)
    ).fetchone()["md"]


def test_environment_empty_stays_absent(db, settings, project):
    assert _md(db, project["id"]) is None
    assert app_db.get_project_environment(settings, str(project["id"])) is None


def test_environment_round_trip_and_render(db, settings, project):
    db.execute(
        "update public.projects set env_runtime = 'Python 3.12', "
        "env_setup_commands = %s, env_notes = %s where id = %s",
        (
            json.dumps(["pip install -e .[dev]", "cp .env.example .env"]),
            "Tests need the sample .env in place.",
            project["id"],
        ),
    )
    db.commit()
    md = _md(db, project["id"])
    assert "- Runtime: Python 3.12" in md
    assert "Setup, in order:" in md
    assert "- `pip install -e .[dev]`" in md
    assert "Tests need the sample .env in place." in md

    env = app_db.get_project_environment(settings, str(project["id"]))
    assert env["runtime"] == "Python 3.12"
    assert env["setup_commands"] == [
        "pip install -e .[dev]",
        "cp .env.example .env",
    ]
    assert env["notes"] == "Tests need the sample .env in place."
    assert env["markdown"] == md


def test_export_includes_environment_when_present(db, project):
    db.execute(
        "update public.projects set env_runtime = 'Node 22' where id = %s",
        (project["id"],),
    )
    db.commit()
    assembled = db.execute(
        "select public.assemble_project_guidelines(%s) as md", (project["id"],)
    ).fetchone()["md"]
    assert "## Environment (runtime & setup)" in assembled
    assert "- Runtime: Node 22" in assembled
    # the seeded Build Mill section still leads the document
    assert "Working with Build Mill" in assembled


def test_export_omits_environment_when_empty(db, project):
    assembled = db.execute(
        "select public.assemble_project_guidelines(%s) as md", (project["id"],)
    ).fetchone()["md"]
    assert "## Environment (runtime & setup)" not in assembled


def _assemble(db, project_id):
    return db.execute(
        "select public.assemble_project_guidelines(%s) as md", (project_id,)
    ).fetchone()["md"]


def test_export_does_not_double_matching_heading(db, project):
    """084: a section whose content already leads with '## <title>' must not
    render the heading twice (the us-7.8 brainstorm drafted sections that way)."""
    db.execute(
        "insert into public.project_guidelines "
        "(org_id, project_id, section_key, title, content, sort_order) "
        "values (%s, %s, 'run-commands', 'Run commands', %s, 10)",
        (project["org_id"], project["id"], "## Run commands\n\nnpm run build\n"),
    )
    db.commit()
    assembled = _assemble(db, project["id"])
    assert assembled.count("## Run commands") == 1
    # the body survived the heading strip
    assert "npm run build" in assembled


def test_export_preserves_non_matching_leading_heading(db, project):
    """A leading heading that isn't the section title is real content — keep it,
    and still add the section's own '## <title>' above it."""
    db.execute(
        "insert into public.project_guidelines "
        "(org_id, project_id, section_key, title, content, sort_order) "
        "values (%s, %s, 'tech-stack', 'Tech stack', %s, 20)",
        (project["org_id"], project["id"], "## Backend\n\nPython\n"),
    )
    db.commit()
    assembled = _assemble(db, project["id"])
    assert assembled.count("## Tech stack") == 1
    assert "## Backend" in assembled
    assert "Python" in assembled
