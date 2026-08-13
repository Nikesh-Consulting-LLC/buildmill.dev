"""US-79.8: every agent failure leaves a full report in `agent_failures`.

Covers the three writers — a run reporting `failed` (complete_run), a lease
expiring without a submission (requeue_expired_claims), a heartbeat going
stale (requeue_stale_heartbeats, both landings) — plus the issue-less case
that `record_run_attempt` skips by design, and the RLS boundary: platform
admins read across orgs, ordinary org members read nothing.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. The writer
tests commit fixtures (the app_db functions open their own connections) and
clean up after themselves; the RLS test is one thrown-away transaction.
"""

from __future__ import annotations

import hashlib
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
    conn.rollback()
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
    token = f"sfw_af_{uuid.uuid4().hex}"
    row = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'af-worker', 'autonomous', %s, %s) returning id
        """,
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    # The stale-heartbeat sweep only considers workers with a runner_config
    # row (it is the runner's own liveness contract).
    db.execute(
        "insert into public.runner_config (org_id, worker_id) values (%s, %s) "
        "on conflict (worker_id) do nothing",
        (ctx["org_id"], row["id"]),
    )
    db.commit()
    yield {
        "id": row["id"],
        "org_id": ctx["org_id"],
        "name": "af-worker",
        "type": "autonomous",
    }
    db.rollback()
    db.execute("delete from public.workers where id = %s", (row["id"],))
    db.commit()


def _insert_run(
    db,
    ctx,
    worker,
    kind: str = "code",
    issue_status: str = "queued",
    lease: str = "+ interval '10 minutes'",
    with_issue: bool = True,
    heartbeat: str | None = None,
    claude_session_id: str | None = None,
):
    issue_id = uuid.uuid4() if with_issue else None
    run_id = uuid.uuid4()
    if with_issue:
        db.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, title, body, acceptance_criteria, status)
            values (%s, %s, %s, 'story', %s, 'b', '["ok"]'::jsonb, %s)
            """,
            (issue_id, ctx["org_id"], ctx["project_id"], f"af {issue_id}", issue_status),
        )
    db.execute(
        f"""
        insert into public.runs
          (id, org_id, project_id, issue_id, provider, status, kind,
           input_context, preset_name, preset_version,
           worker_id, claimed_at, claim_expires_at, last_heartbeat_at,
           claude_session_id)
        values (%s, %s, %s, %s, 'claude', 'running', %s,
                %s::jsonb, 'Balanced', 2,
                %s, now() - interval '30 minutes', now() {lease},
                {heartbeat or "null"}, %s)
        """,
        (
            run_id,
            ctx["org_id"],
            ctx["project_id"],
            issue_id,
            kind,
            json.dumps({"story": "do the thing", "acceptance_criteria": ["ok"]}),
            worker["id"],
            claude_session_id,
        ),
    )
    db.commit()
    return issue_id, run_id


def _cleanup(db, issue_id, run_id):
    db.rollback()
    # agent_failures deliberately has no FK to runs/issues — delete explicitly.
    db.execute("delete from public.agent_failures where run_id = %s", (run_id,))
    if issue_id:
        db.execute(
            "update public.issues set status = 'failed' where id = %s", (issue_id,)
        )
        db.execute("delete from public.issues where id = %s", (issue_id,))
    else:
        db.execute("delete from public.runs where id = %s", (run_id,))
    db.commit()


def _failure_for(db, run_id):
    db.rollback()
    return db.execute(
        "select * from public.agent_failures where run_id = %s "
        "order by created_at desc limit 1",
        (run_id,),
    ).fetchone()


def test_expired_claim_records_agent_failure(db, settings, ctx, worker):
    """The story's founding case: the agent died holding the claim. The
    heartbeat is kept fresh so the stale sweep (which rides along inside
    requeue_expired_claims) leaves this run for the lease sweep itself."""
    issue_id, run_id = _insert_run(
        db, ctx, worker, lease="- interval '1 minute'", heartbeat="now()"
    )
    try:
        assert app_db.requeue_expired_claims(settings) >= 1
        row = _failure_for(db, run_id)
        assert row is not None
        assert row["category"] == "lease-expired"
        assert row["org_id"] == ctx["org_id"]
        assert row["project_id"] == ctx["project_id"]
        assert row["issue_id"] == issue_id
        assert row["kind"] == "code"
        # The snapshot: requeue nulled the run's worker, but the report kept it.
        assert row["worker_id"] == worker["id"]
        assert row["worker_name"] == "af-worker"
        assert row["worker_type"] == "autonomous"
        assert row["preset_name"] == "Balanced"
        assert row["preset_version"] == 2
        assert "died holding the claim" in row["error"]
        assert row["detail"]["held_minutes"] >= 29
        assert row["resumable"] is False
        assert row["status"] == "new"
    finally:
        _cleanup(db, issue_id, run_id)


def test_stale_heartbeat_records_agent_failure(db, settings, ctx, worker):
    issue_id, run_id = _insert_run(
        db, ctx, worker, heartbeat="now() - interval '5 minutes'"
    )
    try:
        assert app_db.requeue_stale_heartbeats(settings) >= 1
        row = _failure_for(db, run_id)
        assert row is not None
        assert row["category"] == "heartbeat-stale"
        assert row["worker_name"] == "af-worker"
        assert "requeued for another worker" in row["error"]
        assert row["detail"]["silent_seconds"] >= 290
        assert row["resumable"] is False
    finally:
        _cleanup(db, issue_id, run_id)


def test_parked_resume_landing_is_marked_resumable(db, settings, ctx, worker):
    """US-59.4's parked landing is the same death, flagged so the console
    can tell 'waiting for its worker' from 'requeued for anyone'."""
    issue_id, run_id = _insert_run(
        db,
        ctx,
        worker,
        heartbeat="now() - interval '5 minutes'",
        claude_session_id="sess-af-1",
    )
    try:
        assert app_db.requeue_stale_heartbeats(settings) >= 1
        run = db.execute(
            "select status from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "paused"
        row = _failure_for(db, run_id)
        assert row is not None
        assert row["category"] == "heartbeat-stale"
        assert row["resumable"] is True
        assert "parked" in row["error"]
    finally:
        _cleanup(db, issue_id, run_id)


def test_failed_run_records_agent_failure(db, settings, ctx, worker):
    issue_id, run_id = _insert_run(db, ctx, worker)
    try:
        assert app_db.complete_run(
            settings,
            str(run_id),
            "failed",
            stdout="line one\nthe last thing it printed",
            diff=None,
            branch_ref=None,
            pr_url=None,
            error="module crashed: boom",
        )
        row = _failure_for(db, run_id)
        assert row is not None
        assert row["category"] == "run-failed"
        assert row["error"] == "module crashed: boom"
        assert row["detail"]["outcome"] == "failed"
        assert "the last thing it printed" in row["detail"]["stdout_tail"]
        assert row["worker_name"] == "af-worker"
    finally:
        _cleanup(db, issue_id, run_id)


def test_issueless_failed_run_is_still_captured(db, settings, ctx, worker):
    """record_run_attempt skips issue-less runs by design; the failure log
    must not — a deploy or release-prep failure is still an agent failure."""
    _, run_id = _insert_run(db, ctx, worker, kind="deploy", with_issue=False)
    try:
        assert app_db.complete_run(
            settings,
            str(run_id),
            "failed",
            stdout=None,
            diff=None,
            branch_ref=None,
            pr_url=None,
            error="ssh unreachable",
        )
        row = _failure_for(db, run_id)
        assert row is not None
        assert row["category"] == "run-failed"
        assert row["issue_id"] is None
        assert row["kind"] == "deploy"
        assert row["error"] == "ssh unreachable"
    finally:
        _cleanup(db, None, run_id)


# --------------------------------------------------------------------- RLS


def _set_auth(db, user_id: str) -> None:
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


def _org(db, label: str, platform_admin: bool = False) -> dict:
    """User + principal + membership + org, built inside the transaction."""
    user_id = str(uuid.uuid4())
    suffix = user_id[:8]
    db.execute(
        "insert into auth.users (id, aud, role, email) values (%s, 'authenticated',"
        " 'authenticated', %s)",
        (user_id, f"{label}-{suffix}@test.invalid"),
    )
    principal = db.execute(
        "select id from public.principals where auth_user_id = %s", (user_id,)
    ).fetchone()
    org = db.execute(
        "insert into public.organizations (name, shortname, is_platform_admin)"
        " values (%s, %s, %s) returning id",
        (f"T-{label}", f"{label}-{suffix}", platform_admin),
    ).fetchone()
    db.execute(
        "insert into public.organization_members (org_id, principal_id, status)"
        " values (%s, %s, 'active')",
        (org["id"], principal["id"]),
    )
    return {"user_id": user_id, "org_id": org["id"]}


def test_platform_admin_reads_all_and_members_read_none(db):
    db.rollback()
    member_org = _org(db, "af-a")
    admin_org = _org(db, "af-p", platform_admin=True)
    row = db.execute(
        """
        insert into public.agent_failures
          (org_id, kind, worker_name, worker_type, category, error)
        values (%s, 'code', 'w', 'autonomous', 'lease-expired', 'boom')
        returning id
        """,
        (member_org["org_id"],),
    ).fetchone()

    # An ordinary member — even of the org the failure belongs to — has no
    # policy at all: this is a superadmin surface (US-79.8).
    _set_auth(db, member_org["user_id"])
    db.execute("set local role authenticated")
    visible = db.execute(
        "select count(*) as n from public.agent_failures where id = %s",
        (row["id"],),
    ).fetchone()
    assert visible["n"] == 0, "an org member saw agent failures"
    listed = db.execute(
        "select count(*) as n from public.list_agent_failures()"
    ).fetchone()
    assert listed["n"] == 0, "the listing leaked to an org member"
    cur = db.execute(
        "update public.agent_failures set status = 'reviewed' where id = %s",
        (row["id"],),
    )
    assert cur.rowcount == 0, "an org member could triage agent failures"
    db.execute("reset role")

    # A platform admin reads and triages it, across the org boundary.
    _set_auth(db, admin_org["user_id"])
    db.execute("set local role authenticated")
    visible = db.execute(
        "select count(*) as n from public.agent_failures where id = %s",
        (row["id"],),
    ).fetchone()
    assert visible["n"] == 1, "the platform admin could not see the failure"
    listed = db.execute(
        "select org_name, category from public.list_agent_failures()"
        " where id = %s",
        (row["id"],),
    ).fetchone()
    assert listed is not None
    assert listed["category"] == "lease-expired"
    assert listed["org_name"].startswith("T-af-a")
    db.execute(
        "update public.agent_failures set status = 'reviewed' where id = %s",
        (row["id"],),
    )
    updated = db.execute(
        "select status from public.agent_failures where id = %s", (row["id"],)
    ).fetchone()
    assert updated["status"] == "reviewed"
    db.execute("reset role")
    db.rollback()


def test_run_context_function_honors_the_admin_gate(db):
    db.rollback()
    member_org = _org(db, "af-c")
    admin_org = _org(db, "af-q", platform_admin=True)
    project = db.execute(
        "insert into public.projects (org_id, name, slug, repo_full_name)"
        " values (%s, 'P', %s, 'o/r') returning id",
        (member_org["org_id"], f"af-c-{uuid.uuid4().hex[:8]}"),
    ).fetchone()
    run_id = uuid.uuid4()
    db.execute(
        """
        insert into public.runs
          (id, org_id, project_id, provider, status, kind, input_context)
        values (%s, %s, %s, 'claude', 'failed', 'deploy', %s::jsonb)
        """,
        (run_id, member_org["org_id"], project["id"], json.dumps({"story": "s"})),
    )
    failure = db.execute(
        """
        insert into public.agent_failures (org_id, run_id, kind, category)
        values (%s, %s, 'deploy', 'run-failed') returning id
        """,
        (member_org["org_id"], run_id),
    ).fetchone()

    _set_auth(db, member_org["user_id"])
    db.execute("set local role authenticated")
    denied = db.execute(
        "select public.agent_failure_run_context(%s) as ctx", (failure["id"],)
    ).fetchone()
    assert denied["ctx"] is None, "the instruction bundle leaked to a member"
    db.execute("reset role")

    _set_auth(db, admin_org["user_id"])
    db.execute("set local role authenticated")
    allowed = db.execute(
        "select public.agent_failure_run_context(%s) as ctx", (failure["id"],)
    ).fetchone()
    assert allowed["ctx"] == {"story": "s"}
    db.execute("reset role")
    db.rollback()
