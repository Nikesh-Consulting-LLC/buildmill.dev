"""US-5.13: live SQL coverage — the "Working with Build Mill" guidelines
section is seeded on project creation, backfilled everywhere, unique per
project, and flows into the assembled guidelines (and therefore dispatch
context, MCP, and AGENTS.md) with zero new plumbing.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

SECTION_KEY = "buildmill-workflow"
TITLE = "Working with Build Mill"


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


@pytest.fixture
def temp_project(db):
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/bm-section-test') returning id, org_id",
        (org["id"], f"bm-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def test_new_project_gets_the_section(db, temp_project):
    row = db.execute(
        "select title, content, sort_order from public.project_guidelines "
        "where project_id = %s and section_key = %s",
        (temp_project["id"], SECTION_KEY),
    ).fetchone()
    assert row is not None
    assert row["title"] == TITLE
    assert "Build Mill" in row["content"]
    # compactness guardrail: this rides every run's input_context
    assert len(row["content"].splitlines()) <= 100


def test_backfill_covers_all_projects_and_is_idempotent(db, temp_project):
    # Scoped to this test's own project: the seed is once-per-project, and a
    # manager may legitimately delete the section afterwards (migration 055's
    # backfill comment says so) — a global "no project lacks it" assertion
    # over the live database punishes that legal deletion.
    missing = db.execute(
        """
        select count(*) as n from public.projects p
        left join public.project_guidelines g
          on g.project_id = p.id and g.section_key = %s
        where g.id is null and p.id = %s
        """,
        (SECTION_KEY, temp_project["id"]),
    ).fetchone()
    assert missing["n"] == 0

    db.execute(
        """
        insert into public.project_guidelines
          (org_id, project_id, section_key, title, content, sort_order)
        select p.org_id, p.id, %s, %s,
               public.default_buildmill_workflow_section(), 999
        from public.projects p
        on conflict (project_id, section_key) where section_key <> 'custom'
        do nothing
        """,
        (SECTION_KEY, TITLE),
    )
    db.commit()
    dupes = db.execute(
        """
        select project_id from public.project_guidelines
        where section_key = %s group by project_id having count(*) > 1
        """,
        (SECTION_KEY,),
    ).fetchall()
    assert dupes == []


def test_section_flows_into_assembled_guidelines(db, temp_project):
    assembled = db.execute(
        "select public.assemble_project_guidelines(%s) as md",
        (temp_project["id"],),
    ).fetchone()["md"]
    assert f"## {TITLE}" in assembled
    assert "factory''s own git remote".replace("''", "'") in assembled
