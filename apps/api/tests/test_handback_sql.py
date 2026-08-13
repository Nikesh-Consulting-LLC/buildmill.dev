"""US-3.4: push-detection hand-back — lease-expiry auto-submit of pushed
work, idempotency vs. explicit submit, release-after-push.

Runs against DATABASE_URL (apps/api/.env); GitHub calls are mocked.
"""

from __future__ import annotations

import asyncio
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
    token = f"sfw_hb_{uuid.uuid4().hex}"
    row = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'handback-worker', 'human', %s, %s) returning id
        """,
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    db.commit()
    yield {
        "id": row["id"],
        "org_id": ctx["org_id"],
        "name": "handback-worker",
        "type": "human",
    }
    db.rollback()
    db.execute("delete from public.workers where id = %s", (row["id"],))
    db.commit()


@pytest.fixture
def mocked_github(monkeypatch):
    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def fake_branch(token, owner, repo, branch):
        return {"name": branch}

    async def fake_open_pulls(token, owner, repo):
        return []

    async def fake_create_pull(token, owner, repo, head, base, title, body=""):
        return {"html_url": f"https://github.com/{owner}/{repo}/pull/99"}

    async def fake_diff(token, owner, repo, base, head):
        return "diff --git a/x b/x"

    monkeypatch.setattr(
        "app.routers.worker.github_tokens.token_for_org", fake_token
    )
    monkeypatch.setattr("app.routers.worker.github.get_branch", fake_branch)
    monkeypatch.setattr("app.routers.worker.github.list_open_pulls", fake_open_pulls)
    monkeypatch.setattr("app.routers.worker.github.create_pull", fake_create_pull)
    monkeypatch.setattr("app.routers.worker.github.get_compare_diff", fake_diff)


def _insert_claimed_run(db, ctx, worker, pushed_sha=None, lease="- interval '1 minute'"):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, 'running')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"handback {issue_id}"),
    )
    db.execute(
        f"""
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context,
           worker_id, claimed_at, claim_expires_at, pushed_head_sha, pushed_at)
        values (%s, %s, %s, 'human', 'running', 'code',
                '{{"repo_full_name": "acme/webshop", "default_branch": "main"}}'::jsonb,
                %s, now(), now() {lease}, %s,
                case when %s::text is null then null else now() end)
        """,
        (run_id, ctx["org_id"], issue_id, worker["id"], pushed_sha, pushed_sha),
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


def test_sync_sweep_skips_pushed_runs(db, settings, ctx, worker):
    """An expired claim WITH pushed work is the reconciler's business —
    the plain requeue sweep must leave it alone."""
    issue_id, run_id = _insert_claimed_run(db, ctx, worker, pushed_sha="a" * 40)
    try:
        app_db.requeue_expired_claims(settings)
        run = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "running"
        assert run["worker_id"] is not None
    finally:
        _cleanup(db, issue_id)


def test_expired_pushed_claim_auto_submits(db, settings, ctx, worker, mocked_github):
    from app import reconcile

    issue_id, run_id = _insert_claimed_run(db, ctx, worker, pushed_sha="a" * 40)
    try:
        handled = asyncio.run(reconcile.reconcile_pushed_expired_claims(settings))
        assert handled >= 1

        run = db.execute(
            "select status, pr_url, branch_ref, diff from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "succeeded"
        assert run["pr_url"] == "https://github.com/acme/webshop/pull/99"
        assert run["branch_ref"] == f"factory/issue-{issue_id}"
        assert run["diff"] == "diff --git a/x b/x"

        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "in-review"

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-succeeded'
            """,
            (issue_id,),
        ).fetchone()
        assert ev["payload"]["trigger"] == "lease-expiry"
        assert ev["payload"]["worker"] == "handback-worker"
    finally:
        _cleanup(db, issue_id)


def test_late_explicit_submit_after_auto_submit_is_noop(
    db, settings, ctx, worker, mocked_github
):
    from app import reconcile
    from app.routers.worker import Submit, perform_submit

    issue_id, run_id = _insert_claimed_run(db, ctx, worker, pushed_sha="a" * 40)
    try:
        asyncio.run(reconcile.reconcile_pushed_expired_claims(settings))

        result = asyncio.run(
            perform_submit(
                settings,
                worker,
                str(run_id),
                Submit(branch_ref=f"factory/issue-{issue_id}"),
            )
        )
        assert result["ok"] is True  # no-op, not an error

        events = db.execute(
            """
            select count(*) as n from public.issue_events
            where issue_id = %s and type = 'run-succeeded'
            """,
            (issue_id,),
        ).fetchone()
        assert events["n"] == 1  # never a duplicate submission
    finally:
        _cleanup(db, issue_id)


def test_expired_claim_without_push_still_requeues(db, settings, ctx, worker):
    from app import reconcile

    issue_id, run_id = _insert_claimed_run(db, ctx, worker, pushed_sha=None)
    try:
        asyncio.run(reconcile.reconcile_pushed_expired_claims(settings))
        app_db.requeue_expired_claims(settings)
        run = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued"
        assert run["worker_id"] is None
    finally:
        _cleanup(db, issue_id)


def test_release_after_push_notes_branch(db, settings, ctx, worker):
    issue_id, run_id = _insert_claimed_run(
        db, ctx, worker, pushed_sha="b" * 40, lease="+ interval '1 hour'"
    )
    try:
        ok = app_db.release_claim(settings, str(run_id), worker, note="handing back")
        assert ok

        run = db.execute(
            "select status, worker_id, pushed_head_sha from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "queued" and run["worker_id"] is None
        assert run["pushed_head_sha"] == "b" * 40  # branch survives for the next claimer

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-released'
            """,
            (issue_id,),
        ).fetchone()
        assert ev["payload"]["pushed_head_sha"] == "b" * 40
        assert ev["payload"]["note"] == "handing back"
    finally:
        _cleanup(db, issue_id)
