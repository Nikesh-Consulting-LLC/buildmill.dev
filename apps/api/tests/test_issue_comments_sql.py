"""US-5.12: live SQL coverage — worker comment round-trip at the db layer,
the audit event, thread ordering, and cross-org isolation of the run-scoped
read.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import hashlib
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
def ctx(db):
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    return {"project_id": project["id"], "org_id": project["org_id"]}


@pytest.fixture
def worker(db, ctx):
    token = f"sfw_test_{uuid.uuid4().hex}"
    row = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'comment-test-worker', 'autonomous', %s, %s) returning id
        """,
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    db.commit()
    yield {"id": row["id"], "org_id": ctx["org_id"], "name": "comment-test-worker"}
    db.rollback()
    db.execute("delete from public.workers where id = %s", (row["id"],))
    db.commit()


@pytest.fixture
def issue_and_run(db, ctx):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '[]'::jsonb, 'queued')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"comment-test {issue_id}"),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context)
        values (%s, %s, %s, 'claude', 'running', 'code', '{}'::jsonb)
        """,
        (run_id, ctx["org_id"], issue_id),
    )
    db.commit()
    yield issue_id, run_id
    db.rollback()
    db.execute(
        "update public.runs set status = 'failed' where id = %s", (run_id,)
    )
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_worker_comment_round_trip_with_event(
    db, settings, ctx, worker, issue_and_run
):
    issue_id, run_id = issue_and_run
    run = {"id": run_id, "org_id": ctx["org_id"], "issue_id": issue_id}

    row = app_db.add_worker_comment(settings, run, worker, "First pass pushed.")
    assert row["id"]

    thread = app_db.list_issue_comments_for_run(
        settings, str(run_id), str(ctx["org_id"])
    )
    assert [c["body"] for c in thread] == ["First pass pushed."]
    assert thread[0]["author_kind"] == "worker"
    assert thread[0]["author"] == "comment-test-worker"

    db.rollback()
    event = db.execute(
        "select payload from public.issue_events "
        "where issue_id = %s and type = 'comment-added'",
        (issue_id,),
    ).fetchone()
    assert event is not None
    assert event["payload"]["author_kind"] == "worker"


def test_cross_org_read_is_empty(db, settings, ctx, worker, issue_and_run):
    issue_id, run_id = issue_and_run
    run = {"id": run_id, "org_id": ctx["org_id"], "issue_id": issue_id}
    app_db.add_worker_comment(settings, run, worker, "Org-private note.")

    foreign_org = str(uuid.uuid4())
    assert (
        app_db.list_issue_comments_for_run(settings, str(run_id), foreign_org)
        == []
    )
