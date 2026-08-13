"""US-3.2: live SQL coverage — claim race, leases, reaper re-queue,
release, plan submit reaching the plan-review gate, worker auth lookup.

US-86.1's serial law (one in-flight unit per project, start to merge; only
the queue head offerable) shapes every scene here: each test stages in a
fresh empty project, and a scene needing two simultaneously claimable runs
puts each in a project of its own (_extra_project).

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
    """A fresh, empty project per test. The org's long-lived first project
    carries real in-flight and queued issues, and under US-86.1's serial law
    those would hold every run these tests stage there."""
    db.rollback()
    org = db.execute(
        "select org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no project")
    project = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/pool-test') returning id
        """,
        (org["org_id"], f"pool-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield {"project_id": project["id"], "org_id": org["org_id"]}
    db.rollback()
    db.execute("delete from public.projects where id = %s", (project["id"],))
    db.commit()


def _human_user_id(db, org_id) -> str | None:
    """A real signed-in person in the org — organization_members.user_id is
    NULL for agent members (principal_id is their identity), so an unordered
    pick can land on one and turn into the literal string 'None' downstream.
    Filter to a real user, deterministic order (test_workers_sql.py's fix)."""
    member = db.execute(
        """
        select user_id from public.organization_members
        where org_id = %s and user_id is not null
        order by user_id
        limit 1
        """,
        (org_id,),
    ).fetchone()
    return str(member["user_id"]) if member else None


def _set_auth(db, user_id: str):
    """Transaction-local JWT claims so the security-definer RPCs'
    is_org_member(auth.uid()) check resolves — reorder_factory_queue and
    set_run_paused (US-15.2) both require it. Resets on the next commit, so
    call again after any db.commit() and right before the RPC call."""
    db.execute("select set_config('request.jwt.claim.sub', %s, true)", (user_id,))
    db.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


@pytest.fixture
def workers(db, ctx):
    """Two active workers in the org; cleaned up after."""
    ids = []
    tokens = []
    for name, wtype in (("pool-test-a", "autonomous"), ("pool-test-b", "human")):
        token = f"sfw_test_{uuid.uuid4().hex}"
        row = db.execute(
            """
            insert into public.workers (org_id, name, type, token_hash, token_last4)
            values (%s, %s, %s, %s, %s) returning id
            """,
            (
                ctx["org_id"],
                name,
                wtype,
                hashlib.sha256(token.encode()).hexdigest(),
                token[-4:],
            ),
        ).fetchone()
        ids.append(row["id"])
        tokens.append(token)
        # US-31.3: the capability gate is fail-CLOSED — zero grant rows means
        # this worker is offered nothing. These tests are about pool ordering,
        # holds and pausing, not about grants, so grant every capability on
        # the fixture project and let each test exercise its own subject.
        for capability in (
            "prd", "breakdown", "plan", "code", "test", "release", "deploy",
        ):
            db.execute(
                """
                insert into public.worker_capabilities
                  (org_id, worker_id, project_id, capability)
                values (%s, %s, %s, %s)
                on conflict do nothing
                """,
                (ctx["org_id"], row["id"], ctx["project_id"], capability),
            )
    db.commit()
    yield [
        {"id": ids[0], "org_id": ctx["org_id"], "name": "pool-test-a", "type": "autonomous", "token": tokens[0]},
        {"id": ids[1], "org_id": ctx["org_id"], "name": "pool-test-b", "type": "human", "token": tokens[1]},
    ]
    db.rollback()
    db.execute("delete from public.workers where id = any(%s)", (ids,))
    db.commit()


def _extra_project(db, ctx, workers):
    """Another empty project, with access for both fixture workers. The
    serial law is per project, so a scene that needs two runs claimable at
    once puts each in a project of its own."""
    row = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/pool-test-b') returning id
        """,
        (ctx["org_id"], f"pool-test-b {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    for w in workers:
        db.execute(
            """
            insert into public.worker_capabilities
              (org_id, worker_id, project_id, capability)
            values (%s, %s, %s, 'access')
            on conflict do nothing
            """,
            (ctx["org_id"], w["id"], row["id"]),
        )
    db.commit()
    return row["id"]


def _drop_project(db, project_id):
    db.rollback()
    db.execute("delete from public.projects where id = %s", (project_id,))
    db.commit()


def _insert_issue_and_run(db, ctx, kind="code", run_status="queued", project_id=None):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '["ok"]'::jsonb, 'queued')
        """,
        (
            issue_id,
            ctx["org_id"],
            project_id or ctx["project_id"],
            f"pool-test {issue_id}",
        ),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context)
        values (%s, %s, %s, 'claude', %s, %s, '{}'::jsonb)
        """,
        (run_id, ctx["org_id"], issue_id, run_status, kind),
    )
    db.commit()
    return issue_id, run_id


def _cleanup(db, issue_id):
    db.rollback()
    # the removal guard blocks deleting queued/running issues
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_worker_token_lookup(db, settings, ctx, workers):
    w = app_db.get_worker_by_token(settings, workers[0]["token"])
    assert w is not None and str(w["id"]) == str(workers[0]["id"])
    assert app_db.get_worker_by_token(settings, "sfw_bogus") is None

    seen = db.execute(
        "select last_seen_at from public.workers where id = %s",
        (workers[0]["id"],),
    ).fetchone()
    assert seen["last_seen_at"] is not None

    db.execute(
        "update public.workers set status = 'revoked' where id = %s",
        (workers[0]["id"],),
    )
    db.commit()
    assert app_db.get_worker_by_token(settings, workers[0]["token"]) is None
    db.execute(
        "update public.workers set status = 'active' where id = %s",
        (workers[0]["id"],),
    )
    db.commit()


def test_claim_race_one_winner(db, settings, ctx, workers):
    issue_id, run_id = _insert_issue_and_run(db, ctx)
    try:
        a = app_db.claim_run(settings, str(run_id), workers[0])
        b = app_db.claim_run(settings, str(run_id), workers[1])
        assert a is not None and b is None

        run = db.execute(
            "select status, worker_id, claim_expires_at from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "running"
        assert str(run["worker_id"]) == str(workers[0]["id"])
        assert run["claim_expires_at"] is not None

        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "running"

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-claimed'
            """,
            (issue_id,),
        ).fetchone()
        assert ev is not None
        assert ev["payload"]["worker"] == "pool-test-a"
    finally:
        _cleanup(db, issue_id)


def test_human_lease_longer_than_autonomous(db, settings, ctx, workers):
    # Two claims held at once — the serial law allows one per project, so
    # the second run lives in a project of its own.
    other = _extra_project(db, ctx, workers)
    issue_a, run_a = _insert_issue_and_run(db, ctx)
    issue_b, run_b = _insert_issue_and_run(db, ctx, project_id=other)
    try:
        app_db.claim_run(settings, str(run_a), workers[0])  # autonomous
        app_db.claim_run(settings, str(run_b), workers[1])  # human
        rows = {
            str(r["id"]): r
            for r in db.execute(
                "select id, claim_expires_at, provider from public.runs "
                "where id = any(%s)",
                ([run_a, run_b],),
            ).fetchall()
        }
        assert (
            rows[str(run_b)]["claim_expires_at"]
            > rows[str(run_a)]["claim_expires_at"]
        )
        # design: the provider column stays for autonomous runs; a human
        # claim records provider = 'human', the worker row carrying identity
        assert rows[str(run_b)]["provider"] == "human"
        assert rows[str(run_a)]["provider"] == "claude"
    finally:
        _cleanup(db, issue_a)
        _cleanup(db, issue_b)
        _drop_project(db, other)


def test_expired_claim_requeues_then_reclaim_and_submit(db, settings, ctx, workers):
    issue_id, run_id = _insert_issue_and_run(db, ctx, kind="plan")
    try:
        app_db.claim_run(settings, str(run_id), workers[0])
        db.execute(
            "update public.runs set claim_expires_at = now() - interval '1 minute' "
            "where id = %s",
            (run_id,),
        )
        db.commit()

        requeued = app_db.requeue_expired_claims(settings)
        assert requeued >= 1

        run = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued"
        assert run["worker_id"] is None
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "queued"

        # Same worker re-claims and submits a plan — reaching plan-review.
        assert app_db.claim_run(settings, str(run_id), workers[0]) is not None
        ok = app_db.complete_run(
            settings,
            str(run_id),
            "succeeded",
            None,
            None,
            None,
            None,
            None,
            plan="# Plan body",
            test_plan="# Test plan body",
            worker_name=workers[0]["name"],
        )
        assert ok
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "plan-review"
        arts = db.execute(
            "select kind, status from public.artifacts where issue_id = %s",
            (issue_id,),
        ).fetchall()
        kinds = {a["kind"] for a in arts}
        assert {"plan", "test_plan"} <= kinds
        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'plan-ready'
            """,
            (issue_id,),
        ).fetchone()
        assert ev["payload"].get("worker") == workers[0]["name"]
    finally:
        _cleanup(db, issue_id)


def test_startup_reaper_leaves_live_claims_and_requeues_expired(
    db, settings, ctx, workers
):
    # Both runs are claimed at once, so each gets its own project (the
    # serial law admits one in-flight unit per project).
    other = _extra_project(db, ctx, workers)
    issue_live, run_live = _insert_issue_and_run(db, ctx)
    issue_exp, run_exp = _insert_issue_and_run(db, ctx, project_id=other)
    try:
        app_db.claim_run(settings, str(run_live), workers[1])  # 24h lease
        app_db.claim_run(settings, str(run_exp), workers[0])
        db.execute(
            "update public.runs set claim_expires_at = now() - interval '1 minute' "
            "where id = %s",
            (run_exp,),
        )
        db.commit()

        app_db.reap_orphaned_provider_runs(settings)

        live = db.execute(
            "select status from public.runs where id = %s", (run_live,)
        ).fetchone()
        assert live["status"] == "running"  # live lease untouched
        exp = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_exp,)
        ).fetchone()
        assert exp["status"] == "queued"  # expired lease back to the pool
        assert exp["worker_id"] is None
    finally:
        _cleanup(db, issue_live)
        _cleanup(db, issue_exp)
        _drop_project(db, other)


def test_release_returns_run_to_pool_with_note(db, settings, ctx, workers):
    issue_id, run_id = _insert_issue_and_run(db, ctx)
    try:
        app_db.claim_run(settings, str(run_id), workers[0])
        ok = app_db.release_claim(
            settings, str(run_id), workers[0], note="wrong stack for me"
        )
        assert ok

        run = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued" and run["worker_id"] is None

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-released'
            """,
            (issue_id,),
        ).fetchone()
        assert ev["payload"]["note"] == "wrong stack for me"
    finally:
        _cleanup(db, issue_id)


def test_prd_claim_and_submit_reaches_prd_review(db, settings, ctx, workers):
    issue_id = db.execute(
        """
        insert into public.issues
          (org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, 'feature', 'PRD pool test feature', 'body', '["ok"]'::jsonb, 'draft')
        returning id
        """,
        (ctx["org_id"], ctx["project_id"]),
    ).fetchone()["id"]
    db.commit()
    try:
        run_id = db.execute(
            "select public.dispatch_prd_draft(%s) as id", (str(issue_id),)
        ).fetchone()["id"]
        db.commit()

        claimed = app_db.claim_run(settings, str(run_id), workers[0])
        assert claimed is not None
        assert claimed["kind"] == "prd"

        # claiming a prd run must NOT flip the issue into 'planning'/'running'
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "draft"

        ok = app_db.complete_run(
            settings,
            str(run_id),
            "succeeded",
            "stdout",
            None,
            None,
            None,
            None,
            prd="## Problem\n\nx\n\n## Goals\n\nx\n\n"
            "## Out of scope\n\nx\n\n## Acceptance criteria\n\nx\n",
            worker_name=workers[0]["name"],
        )
        assert ok is True

        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "prd-review"

        artifact = db.execute(
            "select kind, status, version, created_by from public.artifacts "
            "where issue_id = %s and kind = 'prd' order by version desc limit 1",
            (issue_id,),
        ).fetchone()
        assert artifact["status"] == "draft"
        assert artifact["created_by"] == "agent"
        assert artifact["version"] == 1

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'prd-drafted'
            """,
            (issue_id,),
        ).fetchone()
        assert ev is not None
        assert ev["payload"].get("worker") == workers[0]["name"]
    finally:
        _cleanup(db, issue_id)


def test_prd_failure_leaves_issue_status_unchanged(db, settings, ctx, workers):
    issue_id = db.execute(
        """
        insert into public.issues
          (org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, 'feature', 'PRD failure pool test feature', 'body', '["ok"]'::jsonb, 'draft')
        returning id
        """,
        (ctx["org_id"], ctx["project_id"]),
    ).fetchone()["id"]
    db.commit()
    try:
        run_id = db.execute(
            "select public.dispatch_prd_draft(%s) as id", (str(issue_id),)
        ).fetchone()["id"]
        db.commit()

        claimed = app_db.claim_run(settings, str(run_id), workers[0])
        assert claimed is not None
        assert claimed["kind"] == "prd"

        # sanity: still 'draft' before the failure is recorded
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "draft"

        ok = app_db.complete_run(
            settings,
            str(run_id),
            "failed",
            None,
            None,
            None,
            None,
            "provider timed out",
            worker_name=workers[0]["name"],
        )
        assert ok is True

        # a failed prd run must NOT strand the issue in 'failed' — it has to
        # stay recoverable so Draft PRD can be dispatched again
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "draft"

        ev = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'run-failed'
            """,
            (issue_id,),
        ).fetchone()
        assert ev is not None
        assert ev["payload"].get("error") == "provider timed out"
        assert ev["payload"].get("worker") == workers[0]["name"]
    finally:
        _cleanup(db, issue_id)


def _insert_feature_with_children(db, ctx, child_statuses):
    """A feature parent plus child stories with the given statuses (US-15.3
    fixtures). Returns (feature_id, [child_ids])."""
    feature_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'feature', %s, 'body', '["ok"]'::jsonb, 'ready')
        """,
        (feature_id, ctx["org_id"], ctx["project_id"], f"feat {feature_id}"),
    )
    child_ids = []
    for st in child_statuses:
        cid = uuid.uuid4()
        db.execute(
            """
            insert into public.issues
              (id, org_id, project_id, type, title, body, acceptance_criteria,
               status, parent_id)
            values (%s, %s, %s, 'story', %s, 'body', '["ok"]'::jsonb, %s, %s)
            """,
            (cid, ctx["org_id"], ctx["project_id"], f"child {cid}", st, feature_id),
        )
        child_ids.append(cid)
    db.commit()
    return feature_id, child_ids


def _queue_run(db, ctx, issue_id, kind):
    run_id = uuid.uuid4()
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context)
        values (%s, %s, %s, 'claude', 'queued', %s, '{}'::jsonb)
        """,
        (run_id, ctx["org_id"], issue_id, kind),
    )
    db.commit()
    return run_id


def test_feature_story_held_until_all_siblings_approved(db, settings, ctx, workers):
    """US-15.3: a story's run is held while any sibling is still draft, and
    releases when the last draft sibling is approved (out of draft).

    The run is a PLAN run — the story's real next step. US-86.1 keeps a
    story's code run held until every sibling plan is approved (switch 2),
    so a code run could never isolate the curation rule this test is about."""
    feature_id, (draft_id, ready_id) = _insert_feature_with_children(
        db, ctx, ["draft", "queued"]
    )
    run_id = _queue_run(db, ctx, ready_id, "plan")

    def pool_ids():
        return {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}

    try:
        # Held: a non-abandoned draft sibling holds the whole feature's stories.
        assert str(run_id) not in pool_ids()
        # And a direct claim is refused (the safety net).
        assert app_db.claim_run(settings, str(run_id), workers[0]) is None

        # Approve the draft sibling (dispatching it moves it out of draft) —
        # now every sibling is approved and the held run releases.
        db.execute(
            "update public.issues set status = 'planning' where id = %s", (draft_id,)
        )
        db.commit()
        assert str(run_id) in pool_ids()
    finally:
        _cleanup(db, ready_id)
        _cleanup(db, draft_id)
        _cleanup(db, feature_id)


def test_abandoning_draft_sibling_releases_held_runs(db, settings, ctx, workers):
    """US-15.3: abandoning the draft sibling breaks the dependency and releases
    the held run."""
    feature_id, (draft_id, ready_id) = _insert_feature_with_children(
        db, ctx, ["draft", "queued"]
    )
    run_id = _queue_run(db, ctx, ready_id, "plan")

    def pool_ids():
        return {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}

    try:
        assert str(run_id) not in pool_ids()
        db.execute(
            "update public.issues set abandoned_at = now() where id = %s", (draft_id,)
        )
        db.commit()
        assert str(run_id) in pool_ids()
    finally:
        _cleanup(db, ready_id)
        _cleanup(db, draft_id)
        _cleanup(db, feature_id)


def test_standalone_story_run_waits_behind_the_queue_head(db, settings, ctx, workers):
    """US-86.1 replaced US-15.3's 'a standalone run is never held': a story
    with no parent feature is its own routing unit, and among queued units
    only the project's queue head is offered — the rest wait, softly, with
    'ahead in the queue'. Merging the head releases the next one."""
    issue_a, run_a = _insert_issue_and_run(db, ctx)  # created first: the head
    issue_b, run_b = _insert_issue_and_run(db, ctx)

    def pool_ids():
        return {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}

    try:
        pool = pool_ids()
        assert str(run_a) in pool  # the head is offered...
        assert str(run_b) not in pool  # ...the unit behind it is held
        reason = db.execute(
            "select public.run_hold_reason(%s) as reason", (run_b,)
        ).fetchone()["reason"]
        assert reason and "ahead in the queue" in reason

        # The head's work merging is what frees the next unit.
        db.execute(
            "update public.runs set status = 'succeeded' where id = %s", (run_a,)
        )
        db.execute(
            "update public.issues set status = 'merged' where id = %s", (issue_a,)
        )
        db.commit()
        assert str(run_b) in pool_ids()
    finally:
        _cleanup(db, issue_a)
        _cleanup(db, issue_b)


def test_paused_run_is_not_offered_or_claimable(db, settings, ctx, workers):
    """US-15.2: a paused run stays queued but is never offered by the pool and
    is refused on a direct claim; resuming brings it back."""
    user_id = _human_user_id(db, ctx["org_id"])
    if not user_id:
        pytest.skip("no human org member")
    issue_id, run_id = _insert_issue_and_run(db, ctx)

    def pool_ids():
        return {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}

    try:
        assert str(run_id) in pool_ids()  # baseline: offered
        _set_auth(db, user_id)
        db.execute(
            "select public.set_run_paused(%s, true)", (str(run_id),)
        )
        db.commit()
        assert str(run_id) not in pool_ids()  # paused: gone from the pool
        assert app_db.claim_run(settings, str(run_id), workers[0]) is None  # refused

        _set_auth(db, user_id)
        db.execute("select public.set_run_paused(%s, false)", (str(run_id),))
        db.commit()
        assert str(run_id) in pool_ids()  # resumed: offered again
    finally:
        _cleanup(db, issue_id)


def test_reorder_sets_pool_pull_order(db, settings, ctx, workers):
    """US-15.2 under the US-86.1 law: within a project only the queue head is
    offerable, so queue_rank can no longer flip two runs of the same project —
    the manager's rank orders the pull ACROSS projects. A ranked run pulls
    ahead of an older unranked one."""
    user_id = _human_user_id(db, ctx["org_id"])
    if not user_id:
        pytest.skip("no human org member")
    other = _extra_project(db, ctx, workers)
    issue_a, run_a = _insert_issue_and_run(db, ctx)  # older, left unranked
    issue_b, run_b = _insert_issue_and_run(db, ctx, project_id=other)
    try:
        # Both are their project's queue head, so both are offered; without a
        # rank, age would list A first. Rank B and it pulls ahead.
        _set_auth(db, user_id)
        db.execute(
            "select public.reorder_factory_queue(%s, %s::uuid[])",
            (str(other), [str(run_b)]),
        )
        db.commit()
        ordered = [
            str(r["id"])
            for r in app_db.list_worker_pool(settings, workers[0])
            if str(r["id"]) in {str(run_a), str(run_b)}
        ]
        assert ordered == [str(run_b), str(run_a)]
    finally:
        _cleanup(db, issue_a)
        _cleanup(db, issue_b)
        _drop_project(db, other)


def test_factory_queue_includes_paused_and_held_unlike_pool(db, settings, ctx, workers):
    """US-15.2: list_factory_queue shows the whole pipeline for an agent's
    context — including paused and running runs the claimable pool omits —
    each carrying its own state, ordered by the manager's rank."""
    user_id = _human_user_id(db, ctx["org_id"])
    if not user_id:
        pytest.skip("no human org member")
    issue_a, run_a = _insert_issue_and_run(db, ctx)  # will be paused
    issue_b, run_b = _insert_issue_and_run(db, ctx)  # stays queued, ranked after
    try:
        _set_auth(db, user_id)
        db.execute("select public.set_run_paused(%s, true)", (str(run_a),))
        db.execute(
            "select public.reorder_factory_queue(%s, %s::uuid[])",
            (str(ctx["project_id"]), [str(run_a), str(run_b)]),
        )
        db.commit()

        pool_ids = {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}
        assert str(run_a) not in pool_ids  # paused: the claimable pool omits it

        queue = app_db.list_factory_queue(settings, str(ctx["org_id"]))
        by_id = {str(r["id"]): r for r in queue}
        assert str(run_a) in by_id  # but the full queue still shows it
        assert by_id[str(run_a)]["paused_at"] is not None
        assert str(run_b) in by_id
        assert by_id[str(run_b)]["paused_at"] is None
    finally:
        _cleanup(db, issue_a)
        _cleanup(db, issue_b)


def test_factory_queue_project_scoping(db, settings, ctx, workers):
    issue_id, run_id = _insert_issue_and_run(db, ctx)
    try:
        scoped = app_db.list_factory_queue(
            settings, str(ctx["org_id"]), project_id=str(ctx["project_id"])
        )
        assert str(run_id) in {str(r["id"]) for r in scoped}

        other = app_db.list_factory_queue(
            settings, str(ctx["org_id"]), project_id=str(uuid.uuid4())
        )
        assert str(run_id) not in {str(r["id"]) for r in other}
    finally:
        _cleanup(db, issue_id)


def test_unchecked_kind_not_offered_or_claimable(db, settings, ctx, workers):
    """US-53.4: the kind checkboxes gate the pool server-side. A 'plan' run is
    not offered to — and a claim is refused for — an agent whose enabled_kinds
    excludes 'plan', with the same wording the runner's own gate uses; null
    means every kind, and re-checking the kind restores the offer."""
    issue_id, run_id = _insert_issue_and_run(db, ctx, kind="plan")

    def pool_ids():
        return {str(r["id"]) for r in app_db.list_worker_pool(settings, workers[0])}

    try:
        assert str(run_id) in pool_ids()  # no config row: every kind
        db.execute(
            """
            insert into public.runner_config (worker_id, org_id, enabled_kinds)
            values (%s, %s, '["code","test"]'::jsonb)
            on conflict (worker_id)
              do update set enabled_kinds = excluded.enabled_kinds
            """,
            (workers[0]["id"], ctx["org_id"]),
        )
        db.commit()
        assert str(run_id) not in pool_ids()  # plan unchecked: never offered
        refusal = app_db.worker_run_refusal(
            settings, str(workers[0]["id"]), str(run_id)
        )
        assert refusal == (
            "this agent does not do 'plan' work — it is unchecked in the "
            "agent's settings"
        )

        db.execute(
            "update public.runner_config set enabled_kinds = %s::jsonb "
            "where worker_id = %s",
            ('["plan"]', workers[0]["id"]),
        )
        db.commit()
        assert str(run_id) in pool_ids()  # re-checked: offered again
        assert (
            app_db.worker_run_refusal(settings, str(workers[0]["id"]), str(run_id))
            is None
        )

        db.execute(
            "update public.runner_config set enabled_kinds = null "
            "where worker_id = %s",
            (workers[0]["id"],),
        )
        db.commit()
        assert str(run_id) in pool_ids()  # null: every kind again
    finally:
        db.rollback()
        db.execute(
            "delete from public.runner_config where worker_id = %s",
            (workers[0]["id"],),
        )
        db.commit()
        _cleanup(db, issue_id)


def test_pool_lists_only_org_queued_runs(db, settings, ctx, workers):
    issue_id, run_id = _insert_issue_and_run(db, ctx)
    try:
        pool = app_db.list_worker_pool(settings, workers[0])
        ids = {str(r["id"]) for r in pool}
        assert str(run_id) in ids
        row = next(r for r in pool if str(r["id"]) == str(run_id))
        assert row["kind"] == "code"
        assert row["issue_title"].startswith("pool-test")
        assert row["repo_full_name"]
    finally:
        _cleanup(db, issue_id)
