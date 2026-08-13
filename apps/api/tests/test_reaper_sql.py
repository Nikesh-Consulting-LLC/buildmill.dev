"""US-2.15: live SQL coverage — orphaned provider-run reaper.

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
    url = _database_url()
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url=url,
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


def _insert_issue_and_run(db, ctx, issue_status, run_status, run_age_minutes=0):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '["ok"]'::jsonb, %s)
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"reaper-test {issue_id}", issue_status),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context, created_at)
        values (%s, %s, %s, 'claude', %s, 'code', '{}'::jsonb,
                now() - make_interval(mins => %s))
        """,
        (run_id, ctx["org_id"], issue_id, run_status, run_age_minutes),
    )
    db.commit()
    return issue_id, run_id


def _cleanup(db, issue_id):
    db.rollback()
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_reaper_flips_stuck_running_run_and_issue(db, settings, ctx):
    issue_id, run_id = _insert_issue_and_run(db, ctx, "running", "running")
    try:
        reaped = app_db.reap_orphaned_provider_runs(settings)
        assert reaped >= 1

        run = db.execute(
            "select status, error from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "failed"
        assert "interrupt" in (run["error"] or "").lower()

        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "failed"

        events = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-failed'
            """,
            (issue_id,),
        ).fetchall()
        assert any(
            json.loads(json.dumps(e["payload"])).get("run_id") == str(run_id)
            for e in events
        )
    finally:
        _cleanup(db, issue_id)


def test_reaper_leaves_queued_pool_items(db, settings, ctx):
    """US-3.2 supersedes the stale-queued rule: 'queued' IS the pool, and
    pool items wait for a claim however long that takes."""
    issue_id, run_id = _insert_issue_and_run(
        db, ctx, "ready", "queued", run_age_minutes=45
    )
    try:
        app_db.reap_orphaned_provider_runs(settings)
        run = db.execute(
            "select status from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued"
    finally:
        _cleanup(db, issue_id)


def test_reaper_leaves_fresh_queued_run(db, settings, ctx):
    issue_id, run_id = _insert_issue_and_run(
        db, ctx, "ready", "queued", run_age_minutes=1
    )
    try:
        app_db.reap_orphaned_provider_runs(settings)
        run = db.execute(
            "select status from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued"
    finally:
        _cleanup(db, issue_id)


def test_complete_run_persists_usage(db, settings, ctx):
    """US-2.15: complete_run stores tokens/cost when provided."""
    issue_id, run_id = _insert_issue_and_run(db, ctx, "running", "running")
    try:
        ok = app_db.complete_run(
            settings,
            str(run_id),
            "succeeded",
            "stdout",
            None,
            "factory/x",
            None,
            None,
            tokens_in=12345,
            tokens_out=4100,
            cost_usd=0.42,
        )
        assert ok
        run = db.execute(
            "select tokens_in, tokens_out, cost_usd from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["tokens_in"] == 12345
        assert run["tokens_out"] == 4100
        assert float(run["cost_usd"]) == 0.42
    finally:
        _cleanup(db, issue_id)
