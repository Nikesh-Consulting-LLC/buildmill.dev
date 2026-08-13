"""US-14.8: live SQL coverage for the run-activity trace.

The point of the feature is that the factory can narrate a run from the
tool calls it already serves, without the agent cooperating. The point of
these tests is the part that is easy to get wrong: coalescing. A worker
reading forty files must leave one row, not forty, or the table grows with
the agent's chattiness and the trace stops being readable.

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
def run(db):
    """A throwaway run to hang activity off, rolled back after each test."""
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    issue = db.execute(
        """
        insert into public.issues (org_id, project_id, title, type, status)
        values (%s, %s, %s, 'chore', 'draft') returning id
        """,
        (project["org_id"], project["id"], f"us-14.8 probe {uuid.uuid4()}"),
    ).fetchone()
    row = db.execute(
        """
        insert into public.runs
          (org_id, project_id, issue_id, kind, status, input_context)
        values (%s, %s, %s, 'code', 'running', '{}'::jsonb) returning id
        """,
        (project["org_id"], project["id"], issue["id"]),
    ).fetchone()
    yield row["id"]
    db.rollback()


def _rows(db, run_id):
    return db.execute(
        # id breaks timestamp ties: inside one test transaction every insert
        # shares now(), and `at` alone returns them in arbitrary order.
        "select tool, at from public.run_activity where run_id = %s "
        "order by at, id",
        (run_id,),
    ).fetchall()


def test_records_the_tool_that_ran(db, run):
    wrote = db.execute(
        "select public.record_run_activity(%s, %s) as w", (run, "get_workspace")
    ).fetchone()["w"]
    assert wrote is True
    rows = _rows(db, run)
    assert [r["tool"] for r in rows] == ["get_workspace"]


def test_repeats_of_the_same_tool_coalesce(db, run):
    """Forty file reads are one activity, not forty rows."""
    for _ in range(40):
        db.execute(
            "select public.record_run_activity(%s, %s)", (run, "read_repo_file")
        )
    assert [r["tool"] for r in _rows(db, run)] == ["read_repo_file"]


def test_a_different_tool_always_records(db, run):
    """Coalescing must never hide a change in what the agent is doing —
    that transition is the whole signal the manager is reading."""
    for tool in ["get_work_context", "get_repo_tree", "read_repo_file",
                 "validate_submission"]:
        db.execute("select public.record_run_activity(%s, %s)", (run, tool))
    assert [r["tool"] for r in _rows(db, run)] == [
        "get_work_context",
        "get_repo_tree",
        "read_repo_file",
        "validate_submission",
    ]


def test_same_tool_records_again_once_the_window_lapses(db, run):
    """A long run that is still reading files should still look alive."""
    db.execute("select public.record_run_activity(%s, %s)", (run, "read_repo_file"))
    # zero-second window: the next identical call is outside it
    db.execute(
        "select public.record_run_activity(%s, %s, 0)", (run, "read_repo_file")
    )
    assert len(_rows(db, run)) == 2


def test_unknown_run_is_ignored_not_an_error(db):
    """Narration is never load-bearing: a bad id must not raise into the
    heartbeat path and cost a worker its claim."""
    wrote = db.execute(
        "select public.record_run_activity(%s, %s) as w",
        (str(uuid.uuid4()), "get_workspace"),
    ).fetchone()["w"]
    assert wrote is False


def test_no_arguments_are_stored(db, run):
    """Only the tool name is recorded — the table has nowhere to put a
    file path or a diff, and that is deliberate."""
    cols = {
        r["column_name"]
        for r in db.execute(
            "select column_name from information_schema.columns "
            "where table_schema = 'public' and table_name = 'run_activity'"
        ).fetchall()
    }
    assert cols == {"id", "org_id", "run_id", "tool", "at"}


def test_rls_is_on_and_there_is_no_insert_policy(db):
    """Members read their own org's trace; writes come from the API's
    direct connection, so an insert policy would only widen the surface."""
    enabled = db.execute(
        "select relrowsecurity from pg_class where relname = 'run_activity'"
    ).fetchone()["relrowsecurity"]
    assert enabled is True
    cmds = [
        r["cmd"]
        for r in db.execute(
            "select cmd from pg_policies where tablename = 'run_activity'"
        ).fetchall()
    ]
    assert cmds == ["SELECT"]
