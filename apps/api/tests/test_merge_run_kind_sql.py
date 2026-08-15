"""US-98.1: `merge` is a real run kind, live — migration 261.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.

The vocabulary side (HANDBACK_SHAPE, ROUTE_KINDS, run-kinds.ts) is pinned
without a database by test_runner_kind_coverage and test_run_kind_vocabulary.
What needs a real database is the half those cannot see: that the constraints
actually accept the kind, that the baked default exists and says the thing the
kind exists to enforce, that `instruction_kind_for` leaves it alone, and that
every project got a row.
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


def test_runs_accepts_the_merge_kind(db):
    db.rollback()
    defn = db.execute(
        "select pg_get_constraintdef(oid) as d from pg_constraint "
        "where conname = 'runs_kind_check'"
    ).fetchone()["d"]
    assert "'merge'" in defn, defn


def test_worker_instructions_accepts_the_merge_kind(db):
    db.rollback()
    defn = db.execute(
        "select pg_get_constraintdef(oid) as d from pg_constraint "
        "where conname = 'worker_instructions_run_kind_check'"
    ).fetchone()["d"]
    assert "'merge'" in defn, defn


def test_the_merge_default_exists_and_states_its_rule(db):
    """The all-or-nothing rule is enforced at submission (us-98.5), but an
    agent should learn it from its instructions rather than from a refusal."""
    db.rollback()
    text = db.execute(
        "select public.baked_worker_instruction('merge') as t"
    ).fetchone()["t"]
    assert text, "no baked default for 'merge'"
    assert "ALL OR NOTHING" in text
    assert "READING BOTH SIDES" in text
    # The apostrophe survived the nested dollar-quoting of the 187-style
    # append. It did not the first time this migration was applied by hand.
    assert "repository's own checks" in text
    assert "''" not in text, "a doubled quote leaked through the append"


def test_instruction_kind_for_leaves_merge_alone(db):
    """`merge` is not in ('plan','code'), so the mapping short-circuits and
    returns it unchanged — no arm was added for it. That is easy to break
    later, which is the whole reason this test exists (us-98.1 AC3)."""
    db.rollback()
    assert (
        db.execute(
            "select public.instruction_kind_for(null, 'merge') as k"
        ).fetchone()["k"]
        == "merge"
    )
    issue = db.execute(
        "select id from public.issues where type = 'chore' limit 1"
    ).fetchone()
    if issue:
        assert (
            db.execute(
                "select public.instruction_kind_for(%s, 'merge') as k",
                (issue["id"],),
            ).fetchone()["k"]
            == "merge"
        ), "a chore's merge run must not resolve to the 'chore' instruction"


def test_every_project_has_a_merge_instruction(db):
    db.rollback()
    row = db.execute(
        "select (select count(*) from public.projects) as projects, "
        "(select count(*) from public.worker_instructions "
        " where run_kind = 'merge') as merges"
    ).fetchone()
    assert row["merges"] == row["projects"], row


def test_a_new_project_is_seeded_with_merge(db):
    """The trigger, not just the backfill."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    project = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/merge-seed-test') returning id",
        (org["id"], f"merge-seed {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    try:
        content = db.execute(
            "select content from public.worker_instructions "
            "where project_id = %s and run_kind = 'merge'",
            (project["id"],),
        ).fetchone()
        assert content is not None, "the seeding trigger skipped 'merge'"
        assert "ALL OR NOTHING" in content["content"]
    finally:
        db.rollback()
        db.execute("delete from public.projects where id = %s", (project["id"],))
        db.commit()
