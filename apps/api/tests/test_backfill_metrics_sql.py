"""US-2.16: change-metrics backfill — computes metrics for old succeeded
runs with a stored diff; leaves diff-less runs at null.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app.config import Settings
from scripts.backfill_run_metrics import backfill


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


DIFF = (
    "diff --git a/src/health.py b/src/health.py\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/src/health.py\n"
    "@@ -0,0 +1,2 @@\n"
    "+line one\n"
    "+line two\n"
)


def _insert_run(db, ctx, *, diff, metrics_null=True):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, 'in-review')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"backfill {issue_id}"),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context, diff)
        values (%s, %s, %s, 'claude', 'succeeded', 'code', '{}'::jsonb, %s)
        """,
        (run_id, ctx["org_id"], issue_id, diff),
    )
    db.commit()
    return issue_id, run_id


def _cleanup(db, issue_id):
    db.rollback()
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_backfill_computes_metrics_for_run_with_diff(db, settings, ctx):
    issue_id, run_id = _insert_run(db, ctx, diff=DIFF)
    try:
        backfill(settings)
        run = db.execute(
            "select lines_added, lines_removed, files_changed, change_breakdown "
            "from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["lines_added"] == 2
        assert run["lines_removed"] == 0
        assert run["files_changed"] == 1
        assert run["change_breakdown"]
    finally:
        _cleanup(db, issue_id)


def test_backfill_leaves_run_without_diff_null(db, settings, ctx):
    issue_id, run_id = _insert_run(db, ctx, diff=None)
    try:
        backfill(settings)
        run = db.execute(
            "select lines_added from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["lines_added"] is None  # nothing to compute — stays "—"
    finally:
        _cleanup(db, issue_id)


def test_backfill_is_idempotent(db, settings, ctx):
    issue_id, run_id = _insert_run(db, ctx, diff=DIFF)
    try:
        backfill(settings)
        first = db.execute(
            "select lines_added from public.runs where id = %s", (run_id,)
        ).fetchone()["lines_added"]
        # a second pass skips already-computed rows and changes nothing
        backfill(settings)
        second = db.execute(
            "select lines_added from public.runs where id = %s", (run_id,)
        ).fetchone()["lines_added"]
        assert first == second == 2
    finally:
        _cleanup(db, issue_id)
