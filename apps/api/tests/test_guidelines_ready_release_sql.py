"""US-7.4: live SQL coverage — a new project gets the Versioning & Release
guidelines section exactly once, it flows through assemble_project_guidelines,
and the guidelines-ready flag round-trips.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row


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
def project(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/rel-test') returning id, org_id",
        (org["id"], f"rel-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute(
        "delete from public.project_guidelines where project_id = %s", (row["id"],)
    )
    db.execute("delete from public.epics where project_id = %s", (row["id"],))
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def test_new_project_gets_release_section_once(db, project):
    rows = db.execute(
        "select title, content from public.project_guidelines "
        "where project_id = %s and section_key = 'release'",
        (project["id"],),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["title"] == "Versioning & Release"
    # US-21.7: date-based versions, and the release is the thing that ships.
    assert "`YYYY.MM.DD.N`" in rows[0]["content"]
    assert "pinned to a commit" in rows[0]["content"]


def test_release_section_flows_into_assembled_guidelines(db, project):
    assembled = db.execute(
        "select public.assemble_project_guidelines(%s) as md", (project["id"],)
    ).fetchone()["md"]
    assert "Versioning & Release" in assembled


def test_backfill_is_idempotent(db, project):
    # Re-running the seed insert must not create a duplicate.
    db.execute(
        "insert into public.project_guidelines "
        "(org_id, project_id, section_key, title, content, sort_order) "
        "select org_id, id, 'release', 'Versioning & Release', "
        "public.default_guidelines_release_section(), 998 "
        "from public.projects where id = %s "
        "on conflict (project_id, section_key) where section_key <> 'custom' "
        "do nothing",
        (project["id"],),
    )
    db.commit()
    count = db.execute(
        "select count(*) as n from public.project_guidelines "
        "where project_id = %s and section_key = 'release'",
        (project["id"],),
    ).fetchone()["n"]
    assert count == 1


def test_guidelines_ready_flag_round_trips(db, project):
    row = db.execute(
        "select guidelines_ready_at, guidelines_ready_by from public.projects "
        "where id = %s",
        (project["id"],),
    ).fetchone()
    assert row["guidelines_ready_at"] is None  # new project not ready

    db.execute(
        "update public.projects set guidelines_ready_at = now(), "
        "guidelines_ready_by = %s where id = %s",
        (project["org_id"], project["id"]),
    )
    db.commit()
    row = db.execute(
        "select guidelines_ready_at from public.projects where id = %s",
        (project["id"],),
    ).fetchone()
    assert row["guidelines_ready_at"] is not None
