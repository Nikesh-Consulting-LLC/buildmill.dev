"""US-5.14: live SQL coverage — seeding on project creation, backfill
idempotence, blank-content fallback, and the live-read composition function.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from seed_kinds import seeded_kinds, seeded_run_kinds

# US-75.1: the expected kinds are read from the seeding trigger rather than
# kept as a fourth hard-coded copy here. The pin these tests exist for is
# unchanged — every kind the trigger names must actually land on a new
# project, and the reseed must add nothing — but adding a kind no longer
# turns three unrelated tests red. (The old list stopped at 'wireframe' and
# missed us-63/us-67's test_case_elaborate and deploy_script_generate.)


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
    """A throwaway project (trigger-seeded); cascades away on delete."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/instructions-test') returning id, org_id",
        (org["id"], f"wi-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def _rows(db, project_id):
    return db.execute(
        "select run_kind, content, updated_by from public.worker_instructions "
        "where project_id = %s",
        (project_id,),
    ).fetchall()


def test_new_project_is_seeded_with_every_kind_the_trigger_names(db, temp_project):
    instruction_kinds, prompt_kinds = seeded_kinds(db)
    rows = _rows(db, temp_project["id"])
    assert {r["run_kind"] for r in rows} == instruction_kinds | prompt_kinds
    for r in rows:
        # The two thinking-prompt kinds are seeded blank on purpose when the
        # org template carries no section for them; only the instruction kinds
        # are promised content.
        if r["run_kind"] in instruction_kinds:
            assert r["content"].strip(), f"{r['run_kind']} seeded empty"
        assert r["updated_by"] is None  # factory default, no editor stamp


def test_backfill_covers_existing_projects(db):
    missing = db.execute(
        """
        select count(*) as n
        from public.projects p
        cross join (values ('prd'), ('plan'), ('code'), ('release')) as k(kind)
        left join public.worker_instructions wi
          on wi.project_id = p.id and wi.run_kind = k.kind
        where wi.id is null
        """
    ).fetchone()
    assert missing["n"] == 0


def test_reseed_is_idempotent(db, temp_project):
    db.execute(
        """
        insert into public.worker_instructions (org_id, project_id, run_kind, content)
        select %s, %s, k.kind, public.default_worker_instruction(k.kind)
        from (values ('prd'), ('plan'), ('code'), ('release')) as k(kind)
        on conflict (project_id, run_kind) do nothing
        """,
        (temp_project["org_id"], temp_project["id"]),
    )
    db.commit()
    # The trigger already seeded every kind it names; the partial manual
    # reseed inserts nothing new (on conflict do nothing).
    assert len(_rows(db, temp_project["id"])) == len(seeded_run_kinds(db))


def test_worker_instruction_for_prefers_content_falls_back_when_blank(
    db, temp_project
):
    pid = temp_project["id"]

    def resolved(kind):
        return db.execute(
            "select public.worker_instruction_for(%s, %s) as t", (pid, kind)
        ).fetchone()["t"]

    default_plan = db.execute(
        "select public.default_worker_instruction('plan') as t"
    ).fetchone()["t"]

    db.execute(
        "update public.worker_instructions set content = 'Custom plan drill.' "
        "where project_id = %s and run_kind = 'plan'",
        (pid,),
    )
    db.commit()
    assert resolved("plan") == "Custom plan drill."

    db.execute(
        "update public.worker_instructions set content = '   ' "
        "where project_id = %s and run_kind = 'plan'",
        (pid,),
    )
    db.commit()
    assert resolved("plan") == default_plan
