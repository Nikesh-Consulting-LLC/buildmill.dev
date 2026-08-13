"""US-7.5: live SQL coverage — the fourth 'release' worker-instruction block
seeds and resets to default, existing prd/plan/code are unchanged, the serve
path accepts the new kind, and the ready flag round-trips.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from seed_kinds import seeded_run_kinds


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
        "values (%s, %s, 'acme/wi-test') returning id, org_id",
        (org["id"], f"wi-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute(
        "delete from public.worker_instructions where project_id = %s", (row["id"],)
    )
    db.execute("delete from public.epics where project_id = %s", (row["id"],))
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def test_all_four_kinds_seeded(db, project):
    kinds = {
        r["run_kind"]
        for r in db.execute(
            "select run_kind from public.worker_instructions where project_id = %s",
            (project["id"],),
        ).fetchall()
    }
    # US-75.1: read from the seeding trigger instead of a hard-coded copy.
    # This list had gone stale twice (it stopped at wireframe and missed
    # test_case_elaborate / deploy_script_generate); what the test is really
    # for — 'release' is seeded alongside every other kind — is unchanged.
    assert kinds == seeded_run_kinds(db)
    assert "release" in kinds


def test_release_default_describes_version_scheme(db):
    text = db.execute(
        "select public.default_worker_instruction('release') as t"
    ).fetchone()["t"]
    # US-21.7: the release instruction is now a live contract, not reference
    # material for a run kind that never ran, and the version is date-based.
    assert "get_release_changes" in text
    assert "PINNED commit" in text
    assert "read from the release, never chosen" in text


def test_serve_path_accepts_release_kind(db, project):
    served = db.execute(
        "select public.worker_instruction_for(%s, 'release') as t",
        (project["id"],),
    ).fetchone()["t"]
    assert "get_release_changes" in served

    # Stored content wins over the default.
    db.execute(
        "update public.worker_instructions set content = 'Custom release note.' "
        "where project_id = %s and run_kind = 'release'",
        (project["id"],),
    )
    db.commit()
    served = db.execute(
        "select public.worker_instruction_for(%s, 'release') as t",
        (project["id"],),
    ).fetchone()["t"]
    assert served == "Custom release note."


def test_prd_plan_code_unchanged(db, project):
    for kind in ("prd", "plan", "code"):
        got = db.execute(
            "select content from public.worker_instructions "
            "where project_id = %s and run_kind = %s",
            (project["id"], kind),
        ).fetchone()["content"]
        default = db.execute(
            "select public.default_worker_instruction(%s) as t", (kind,)
        ).fetchone()["t"]
        assert got == default


def test_ready_flag_round_trips(db, project):
    row = db.execute(
        "select worker_instructions_ready_at from public.projects where id = %s",
        (project["id"],),
    ).fetchone()
    assert row["worker_instructions_ready_at"] is None
    db.execute(
        "update public.projects set worker_instructions_ready_at = now(), "
        "worker_instructions_ready_by = %s where id = %s",
        (project["org_id"], project["id"]),
    )
    db.commit()
    row = db.execute(
        "select worker_instructions_ready_at from public.projects where id = %s",
        (project["id"],),
    ).fetchone()
    assert row["worker_instructions_ready_at"] is not None
