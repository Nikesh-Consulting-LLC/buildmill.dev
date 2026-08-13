"""US-15.5: record_run_trace appends an entry to a run the worker holds, and
refuses a run the caller doesn't hold (no spoofing another agent's trace).

Runs against DATABASE_URL (apps/api/.env). Skips if unreachable or no project.
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
def running_run(db, ctx):
    """A running run held by a fresh worker — the precondition record_run_trace
    accepts."""
    token = f"sfw_tr_{uuid.uuid4().hex}"
    worker_id = db.execute(
        "insert into public.workers (org_id, name, type, token_hash, token_last4) "
        "values (%s, 'trace-worker', 'autonomous', %s, %s) returning id",
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()["id"]
    issue_id = uuid.uuid4()
    db.execute(
        "insert into public.issues (id, org_id, project_id, type, title, body, "
        "acceptance_criteria, status) values (%s, %s, %s, 'story', %s, 'b', "
        "'[]'::jsonb, 'running')",
        (issue_id, ctx["org_id"], ctx["project_id"], f"trace {issue_id}"),
    )
    run_id = uuid.uuid4()
    db.execute(
        "insert into public.runs (id, org_id, issue_id, project_id, provider, status, "
        "kind, input_context, worker_id) values (%s, %s, %s, %s, 'claude', 'running', "
        "'code', '{}'::jsonb, %s)",
        (run_id, ctx["org_id"], issue_id, ctx["project_id"], worker_id),
    )
    db.commit()
    yield {"run_id": run_id, "worker_id": worker_id, "issue_id": issue_id}
    db.rollback()
    db.execute("delete from public.run_trace where run_id = %s", (run_id,))
    db.execute("delete from public.runs where id = %s", (run_id,))
    # guard_issue_removal refuses deleting a queued/running issue; park it
    # in a deletable status first.
    db.execute(
        "update public.issues set status = 'draft' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.execute("delete from public.workers where id = %s", (worker_id,))
    db.commit()


def test_holder_can_append_and_it_is_attributed(db, settings, running_run):
    entry_id = app_db.record_run_trace(
        settings,
        str(running_run["run_id"]),
        str(running_run["worker_id"]),
        "decision",
        "chose the incremental approach",
    )
    assert entry_id is not None
    db.rollback()
    row = db.execute(
        "select kind, content, issue_id from public.run_trace where id = %s",
        (entry_id,),
    ).fetchone()
    assert row["kind"] == "decision"
    assert row["issue_id"] == running_run["issue_id"]


def test_a_different_worker_cannot_append(db, settings, running_run):
    entry_id = app_db.record_run_trace(
        settings,
        str(running_run["run_id"]),
        str(uuid.uuid4()),  # not the holder
        "step",
        "sneaky",
    )
    assert entry_id is None


def test_append_refused_when_run_not_running(db, settings, running_run):
    db.execute(
        "update public.runs set status = 'succeeded' where id = %s",
        (running_run["run_id"],),
    )
    db.commit()
    entry_id = app_db.record_run_trace(
        settings,
        str(running_run["run_id"]),
        str(running_run["worker_id"]),
        "step",
        "too late",
    )
    assert entry_id is None
