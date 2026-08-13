"""US-13.11: staffed verification runs — dispatch guards, capability
gating via the us-13.10 generic predicate, completion semantics (no
issue-status change), and the zero-results rejection.

Runs against DATABASE_URL (apps/api/.env)."""

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
    """A fresh, empty project: US-86.1's serial law would hold any test run
    staged in the org's long-lived first project behind whatever that
    project already has in flight."""
    db.rollback()
    org = db.execute(
        "select org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no project")
    project = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/test-run-test') returning id
        """,
        (org["org_id"], f"test-run-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    token = f"sfw_tr_{uuid.uuid4().hex}"
    worker = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'test-run-worker', 'autonomous', %s, %s) returning id
        """,
        (
            org["org_id"],
            hashlib.sha256(token.encode()).hexdigest(),
            token[-4:],
        ),
    ).fetchone()
    db.commit()
    yield {
        "org_id": org["org_id"],
        "project_id": project["id"],
        "worker": {
            "id": worker["id"],
            "org_id": org["org_id"],
            "name": "test-run-worker",
            "type": "autonomous",
        },
    }
    db.rollback()
    db.execute("delete from public.workers where id = %s", (worker["id"],))
    db.execute("delete from public.projects where id = %s", (project["id"],))
    db.commit()


def _issue_with_submitted_code_run(db, ctx, status="in-review"):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, %s)
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"tr {issue_id}", status),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context,
           branch_ref, finished_at)
        values (%s, %s, %s, 'claude', 'succeeded', 'code', '{}'::jsonb,
                'factory/us-1-tr', now())
        """,
        (run_id, ctx["org_id"], issue_id),
    )
    # An active test case — dispatch_test_run refuses an issue with none.
    db.execute(
        """
        insert into public.test_cases
          (org_id, project_id, issue_id, title, steps, expected_result)
        values (%s, %s, %s, 'it works', 'do the thing', 'it works')
        """,
        (ctx["org_id"], ctx["project_id"], issue_id),
    )
    db.commit()
    return issue_id, run_id


def _cleanup(db, issue_id):
    db.rollback()
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.test_cases where issue_id = %s", (issue_id,))
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_dispatch_requires_a_submitted_code_run(db, settings, ctx):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, 'planned')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"tr {issue_id}"),
    )
    db.commit()
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        assert "no submitted code run" in out["error"]
    finally:
        _cleanup(db, issue_id)


def test_dispatch_refuses_when_no_active_test_cases(db, settings, ctx):
    """A dispatched run with zero test cases could never legally complete
    (submit_test_run refuses a zero-result hand-back) — refuse up front
    instead of queuing a run that sits there forever."""
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, 'in-review')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"tr {issue_id}"),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context,
           branch_ref, finished_at)
        values (%s, %s, %s, 'claude', 'succeeded', 'code', '{}'::jsonb,
                'factory/us-1-tr-notc', now())
        """,
        (run_id, ctx["org_id"], issue_id),
    )
    db.commit()
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        assert "no active test cases" in out["error"]
    finally:
        _cleanup(db, issue_id)


def test_dispatch_freezes_branch_and_refuses_duplicates(db, settings, ctx):
    issue_id, _ = _issue_with_submitted_code_run(db, ctx)
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"]), actor="mgr"
        )
        assert "run_id" in out
        row = db.execute(
            "select kind, status, input_context from public.runs where id = %s",
            (out["run_id"],),
        ).fetchone()
        assert row["kind"] == "test" and row["status"] == "queued"
        assert row["input_context"]["branch_ref"] == "factory/us-1-tr"
        assert row["input_context"]["run_kind"] == "test"
        # Issue status untouched by dispatch.
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "in-review"
        dup = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        assert "already queued or running" in dup["error"]
    finally:
        _cleanup(db, issue_id)


def test_test_runs_are_offered_by_the_test_checkbox_only(db, settings, ctx):
    """US-55.1: access is the project half; the `test` kind gates on the
    agent's own enabled_kinds checkbox."""
    issue_id, _ = _issue_with_submitted_code_run(db, ctx)
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        run_id = out["run_id"]
        # Access WITHOUT the `test` checkbox: the run is withheld.
        db.execute(
            """
            insert into public.worker_capabilities
              (worker_id, project_id, org_id, capability)
            values (%s, %s, %s, 'access')
            """,
            (ctx["worker"]["id"], ctx["project_id"], ctx["org_id"]),
        )
        db.execute(
            """
            insert into public.runner_config (worker_id, org_id, enabled_kinds)
            values (%s, %s, '["code"]'::jsonb)
            on conflict (worker_id)
              do update set enabled_kinds = excluded.enabled_kinds
            """,
            (ctx["worker"]["id"], ctx["org_id"]),
        )
        db.commit()
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert run_id not in pool_ids
        assert not app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), run_id
        )
        # Checking `test` offers it — the one predicate, no new gating code.
        db.execute(
            "update public.runner_config set enabled_kinds = "
            "'[\"code\",\"test\"]'::jsonb where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.commit()
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert run_id in pool_ids
        assert app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), run_id
        )
    finally:
        db.execute(
            "delete from public.runner_config where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.execute(
            "delete from public.worker_capabilities where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.commit()
        _cleanup(db, issue_id)


def test_claim_and_completion_leave_issue_status_alone(db, settings, ctx):
    issue_id, _ = _issue_with_submitted_code_run(db, ctx)
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        run_id = out["run_id"]
        claimed = app_db.claim_run(settings, run_id, ctx["worker"])
        assert claimed
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "in-review"  # claim didn't advance it
        ok = app_db.complete_run(
            settings,
            run_id,
            "succeeded",
            "pytest -q: 12 passed",
            None,
            None,
            None,
            None,
            worker_name="test-run-worker",
            trigger="submit",
        )
        assert ok
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "in-review"  # completion didn't either
        event = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'test-run-completed'
            order by created_at desc limit 1
            """,
            (issue_id,),
        ).fetchone()
        assert event is not None
        assert event["payload"]["run_id"] == run_id
    finally:
        _cleanup(db, issue_id)


def test_count_run_test_results_zero_for_fresh_run(db, settings, ctx):
    issue_id, _ = _issue_with_submitted_code_run(db, ctx)
    try:
        out = app_db.dispatch_test_run(
            settings, str(issue_id), str(ctx["org_id"])
        )
        assert app_db.count_run_test_results(settings, out["run_id"]) == 0
    finally:
        _cleanup(db, issue_id)
