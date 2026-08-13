"""US-13.6: unattended runs cannot stall silently — heartbeat stamping,
the kind-guard on release/requeue (a prd/breakdown claim never knocks its
issue to 'queued'), and the enriched claim-expired event.

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
    """A fresh, empty project: US-86.1's serial law would hold (and so make
    unclaimable) any run staged in the org's long-lived first project while
    that project has work in flight or queued ahead."""
    db.rollback()
    org = db.execute(
        "select org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no project")
    project = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/liveness-test') returning id
        """,
        (org["org_id"], f"liveness-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield {"project_id": project["id"], "org_id": org["org_id"]}
    db.rollback()
    db.execute("delete from public.projects where id = %s", (project["id"],))
    db.commit()


@pytest.fixture
def worker(db, ctx):
    token = f"sfw_lv_{uuid.uuid4().hex}"
    row = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'liveness-worker', 'autonomous', %s, %s) returning id
        """,
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    db.commit()
    yield {
        "id": row["id"],
        "org_id": ctx["org_id"],
        "name": "liveness-worker",
        "type": "autonomous",
    }
    db.rollback()
    db.execute("delete from public.workers where id = %s", (row["id"],))
    db.commit()


def _insert_run(
    db,
    ctx,
    worker,
    kind: str,
    issue_status: str,
    run_status: str = "running",
    lease: str = "+ interval '10 minutes'",
    issue_type: str = "story",
):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, %s, %s, 'b', '["ok"]'::jsonb, %s)
        """,
        (
            issue_id,
            ctx["org_id"],
            ctx["project_id"],
            issue_type,
            f"liveness {issue_id}",
            issue_status,
        ),
    )
    claimed = run_status == "running"
    db.execute(
        f"""
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context,
           worker_id, claimed_at, claim_expires_at)
        values (%s, %s, %s, 'claude', %s, %s, '{{}}'::jsonb,
                %s,
                case when %s then now() - interval '30 minutes' end,
                case when %s then now() {lease} end)
        """,
        (
            run_id,
            ctx["org_id"],
            issue_id,
            run_status,
            kind,
            worker["id"] if claimed else None,
            claimed,
            claimed,
        ),
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


def test_claim_and_extend_stamp_last_heartbeat(db, settings, ctx, worker):
    issue_id, run_id = _insert_run(
        db, ctx, worker, "code", "queued", run_status="queued"
    )
    try:
        claimed = app_db.claim_run(settings, str(run_id), worker)
        assert claimed
        row = db.execute(
            "select last_heartbeat_at from public.runs where id = %s", (run_id,)
        ).fetchone()
        first = row["last_heartbeat_at"]
        assert first is not None
        app_db.extend_claim(settings, str(run_id), str(worker["id"]))
        row = db.execute(
            "select last_heartbeat_at from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert row["last_heartbeat_at"] >= first
    finally:
        _cleanup(db, issue_id)


def test_release_prd_claim_leaves_issue_status(db, settings, ctx, worker):
    """The documented gap, closed: releasing a prd/breakdown claim must
    not knock the issue to 'queued' — claiming one never advanced it."""
    issue_id, run_id = _insert_run(
        db, ctx, worker, "prd", "prd-review", issue_type="feature"
    )
    try:
        assert app_db.release_claim(settings, str(run_id), worker, note="cannot proceed")
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "prd-review"
        run = db.execute(
            "select status, last_heartbeat_at from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "queued"
        assert run["last_heartbeat_at"] is None
    finally:
        _cleanup(db, issue_id)


def test_release_code_claim_still_requeues_issue(db, settings, ctx, worker):
    issue_id, run_id = _insert_run(db, ctx, worker, "code", "running")
    try:
        assert app_db.release_claim(settings, str(run_id), worker)
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "queued"
    finally:
        _cleanup(db, issue_id)


def test_expired_sweep_kind_guard_and_reason(db, settings, ctx, worker):
    """An expired breakdown claim requeues the run, leaves the feature at
    'ready', and records who held it and for how long."""
    issue_id, run_id = _insert_run(
        db,
        ctx,
        worker,
        "breakdown",
        "ready",
        lease="- interval '1 minute'",
        issue_type="feature",
    )
    try:
        swept = app_db.requeue_expired_claims(settings)
        assert swept >= 1
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "ready"
        event = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'claim-expired'
            order by created_at desc limit 1
            """,
            (issue_id,),
        ).fetchone()
        assert event is not None
        assert event["payload"]["worker"] == "liveness-worker"
        assert event["payload"]["held_minutes"] >= 29
        assert "lease expired" in event["payload"]["note"]
    finally:
        _cleanup(db, issue_id)


def test_force_requeue_kind_guard(db, settings, ctx, worker):
    issue_id, run_id = _insert_run(
        db, ctx, worker, "prd", "prd-review", issue_type="feature"
    )
    try:
        assert app_db.force_requeue_run(
            settings, str(run_id), note="requeued by the manager"
        )
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "prd-review"
    finally:
        _cleanup(db, issue_id)
