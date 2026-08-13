"""US-15.14: reset a wrongly-started run — discard this attempt's draft
output, restore the issue's pre-dispatch status, and re-queue for a fresh
claim. US-68.1: reset a work item to a chosen stage (PRD, Elaboration,
Planning, Dispatch for Coding) instead of always wiping it to Triage.

Runs against DATABASE_URL (apps/api/.env). Skips if unreachable or no project.
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


def _draft_story(db, ctx):
    """A 'draft' story — dispatch_issue turns it into a plan run and moves the
    issue to 'queued', stamping prev_issue_status='draft'."""
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '[]'::jsonb, 'draft')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"reset-test {issue_id}"),
    )
    db.commit()
    return issue_id


def _cleanup(db, issue_id):
    db.rollback()
    db.execute(
        "update public.issues set status = 'draft' where id = %s and status in "
        "('queued','running','planning')",
        (issue_id,),
    )
    db.execute("delete from public.runs where issue_id = %s", (issue_id,))
    db.execute("delete from public.issue_events where issue_id = %s", (issue_id,))
    db.execute("delete from public.artifacts where issue_id = %s", (issue_id,))
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_reset_requeues_and_restores_status(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        # after dispatch: run queued, issue queued, prev stamped
        assert (
            db.execute(
                "select status from public.issues where id = %s", (issue_id,)
            ).fetchone()["status"]
            == "queued"
        )

        summary = app_db.reset_run(settings, str(run_id), actor="tester")
        assert summary is not None
        assert summary["reset_to_status"] == "draft"

        db.rollback()  # see the committed effect of reset_run
        run = db.execute(
            "select status, worker_id from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["status"] == "queued"
        assert run["worker_id"] is None
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert issue["status"] == "draft"
        events = db.execute(
            "select count(*) as n from public.issue_events "
            "where issue_id = %s and type = 'run-reset'",
            (issue_id,),
        ).fetchone()
        assert events["n"] == 1
    finally:
        _cleanup(db, issue_id)


def test_reset_releases_a_running_claim(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    worker_id = None
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        # simulate a live claim — with a real worker row: runs.worker_id
        # gained a foreign key, so a fabricated uuid no longer claims.
        worker_id = db.execute(
            "insert into public.workers (org_id, name, type, token_hash, token_last4) "
            "values (%s, 'reset-worker', 'autonomous', %s, '0000') returning id",
            (ctx["org_id"], f"h{uuid.uuid4().hex}"),
        ).fetchone()["id"]
        db.execute(
            "update public.runs set status = 'running', worker_id = %s, "
            "claimed_at = now() where id = %s",
            (worker_id, run_id),
        )
        db.commit()

        summary = app_db.reset_run(settings, str(run_id))
        assert summary is not None

        db.rollback()
        run = db.execute(
            "select status, worker_id, claimed_at from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "queued"
        assert run["worker_id"] is None
        assert run["claimed_at"] is None
    finally:
        _cleanup(db, issue_id)
        if worker_id is not None:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_reset_discards_draft_plan_artifact(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.execute(
            "insert into public.artifacts (org_id, issue_id, kind, content, version, "
            "status, created_by) values (%s, %s, 'plan', '# Plan', 1, 'draft', 'agent')",
            (ctx["org_id"], issue_id),
        )
        db.commit()

        summary = app_db.reset_run(settings, str(run_id))
        assert summary is not None
        assert summary["discarded"].get("plan_drafts") == 1

        db.rollback()
        n = db.execute(
            "select count(*) as n from public.artifacts where issue_id = %s "
            "and kind = 'plan'",
            (issue_id,),
        ).fetchone()["n"]
        assert n == 0
    finally:
        _cleanup(db, issue_id)


def test_reset_refuses_a_terminal_run(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.execute(
            "update public.runs set status = 'succeeded' where id = %s", (run_id,)
        )
        db.commit()
        assert app_db.reset_run(settings, str(run_id)) is None
    finally:
        _cleanup(db, issue_id)


# -------------------------------- US-68.1: reset to a chosen stage


def _feature_with_child(db, ctx, child_status="planned"):
    feature_id = uuid.uuid4()
    child_id = uuid.uuid4()
    db.execute(
        "insert into public.issues (id, org_id, project_id, type, title, body, "
        "acceptance_criteria, status) values (%s, %s, %s, 'feature', %s, 'b', "
        "'[]'::jsonb, 'ready')",
        (feature_id, ctx["org_id"], ctx["project_id"], f"fr-feat {feature_id}"),
    )
    db.execute(
        "insert into public.issues (id, org_id, project_id, parent_id, type, title, "
        "body, acceptance_criteria, status) values (%s, %s, %s, %s, 'story', %s, "
        "'b', '[]'::jsonb, %s)",
        (
            child_id,
            ctx["org_id"],
            ctx["project_id"],
            feature_id,
            f"fr-child {child_id}",
            child_status,
        ),
    )
    db.commit()
    return feature_id, child_id


def _cleanup_tree(db, feature_id, child_id):
    db.rollback()
    for iid in (child_id, feature_id):
        db.execute(
            "update public.issues set status = 'draft' where id = %s and status in "
            "('queued','running','planning')",
            (iid,),
        )
        db.execute("delete from public.runs where issue_id = %s", (iid,))
        db.execute("delete from public.issue_events where issue_id = %s", (iid,))
        db.execute("delete from public.artifacts where issue_id = %s", (iid,))
    db.execute("delete from public.issues where id = %s", (child_id,))
    db.execute("delete from public.issues where id = %s", (feature_id,))
    db.commit()


def test_reset_planning_cascades_to_children_and_keeps_feature(db, settings, ctx):
    feature_id, child_id = _feature_with_child(db, ctx)
    try:
        db.execute(
            "insert into public.artifacts (org_id, issue_id, kind, content, version, "
            "status, created_by) values (%s, %s, 'plan', '# P', 1, 'approved', 'agent')",
            (ctx["org_id"], child_id),
        )
        db.commit()

        summary = app_db.reset_issue_to_stage(
            settings, str(feature_id), "planning", actor="tester"
        )
        assert summary is not None
        assert summary["reset_count"] == 1
        assert summary["artifacts_deleted"] == 1
        assert summary["target_status"] == "ready"

        db.rollback()
        # the child lands at 'ready'; the feature itself is untouched
        assert (
            db.execute(
                "select status from public.issues where id = %s", (child_id,)
            ).fetchone()["status"]
            == "ready"
        )
        assert (
            db.execute(
                "select status from public.issues where id = %s", (feature_id,)
            ).fetchone()["status"]
            == "ready"
        )
        assert (
            db.execute(
                "select count(*) as n from public.artifacts where issue_id = %s",
                (child_id,),
            ).fetchone()["n"]
            == 0
        )
        assert (
            db.execute(
                "select count(*) as n from public.issue_events where issue_id = %s "
                "and type = 'issue-reset'",
                (child_id,),
            ).fetchone()["n"]
            == 1
        )
    finally:
        _cleanup_tree(db, feature_id, child_id)


def test_reset_blocked_when_a_child_is_merged(db, settings, ctx):
    feature_id, child_id = _feature_with_child(db, ctx, child_status="merged")
    try:
        with pytest.raises(app_db.ResetBlocked):
            app_db.reset_issue_to_stage(settings, str(feature_id), "planning")
        # nothing changed — the feature is still ready
        db.rollback()
        assert (
            db.execute(
                "select status from public.issues where id = %s", (feature_id,)
            ).fetchone()["status"]
            == "ready"
        )
    finally:
        _cleanup_tree(db, feature_id, child_id)


def test_reset_elaboration_choice_of_destination(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        db.execute(
            "update public.issues set status = 'ready' where id = %s", (issue_id,)
        )
        db.execute(
            "insert into public.artifacts (org_id, issue_id, kind, content, version, "
            "status, created_by) values (%s, %s, 'elaboration', 'e', 1, 'draft', 'agent')",
            (ctx["org_id"], issue_id),
        )
        db.commit()

        summary = app_db.reset_issue_to_stage(
            settings,
            str(issue_id),
            "elaboration",
            destination_status="ready",
            note="tighten the acceptance criteria",
            actor="tester",
        )
        assert summary is not None
        assert summary["target_status"] == "ready"
        assert summary["artifacts_deleted"] == 1

        db.rollback()
        assert (
            db.execute(
                "select status from public.issues where id = %s", (issue_id,)
            ).fetchone()["status"]
            == "ready"
        )
        event = db.execute(
            "select payload from public.issue_events where issue_id = %s "
            "and type = 'issue-reset'",
            (issue_id,),
        ).fetchone()
        assert event["payload"]["note"] == "tighten the acceptance criteria"
    finally:
        _cleanup(db, issue_id)


def test_reset_coding_requires_an_approved_plan(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        db.execute(
            "update public.issues set status = 'planned' where id = %s", (issue_id,)
        )
        db.commit()

        with pytest.raises(app_db.ResetStageError):
            app_db.reset_issue_to_stage(settings, str(issue_id), "coding")

        db.rollback()
        assert (
            db.execute(
                "select status from public.issues where id = %s", (issue_id,)
            ).fetchone()["status"]
            == "planned"
        )
    finally:
        _cleanup(db, issue_id)


def test_reset_coding_lands_on_planned_with_approved_plan(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        db.execute(
            "update public.issues set status = 'needs-fixes' where id = %s",
            (issue_id,),
        )
        db.execute(
            "insert into public.artifacts (org_id, issue_id, kind, content, version, "
            "status, created_by) values (%s, %s, 'plan', '# P', 1, 'approved', 'agent')",
            (ctx["org_id"], issue_id),
        )
        db.commit()

        summary = app_db.reset_issue_to_stage(settings, str(issue_id), "coding")
        assert summary is not None
        assert summary["target_status"] == "planned"
        assert summary["artifacts_deleted"] == 0

        db.rollback()
        assert (
            db.execute(
                "select status from public.issues where id = %s", (issue_id,)
            ).fetchone()["status"]
            == "planned"
        )
        # the approved plan is kept
        assert (
            db.execute(
                "select count(*) as n from public.artifacts where issue_id = %s "
                "and kind = 'plan'",
                (issue_id,),
            ).fetchone()["n"]
            == 1
        )
    finally:
        _cleanup(db, issue_id)


def test_reset_prd_abandons_children_and_deletes_prd(db, settings, ctx):
    feature_id, child_id = _feature_with_child(db, ctx, child_status="draft")
    try:
        db.execute(
            "insert into public.artifacts (org_id, issue_id, kind, content, version, "
            "status, created_by) values (%s, %s, 'prd', '# PRD', 1, 'approved', 'agent')",
            (ctx["org_id"], feature_id),
        )
        db.commit()

        summary = app_db.reset_issue_to_stage(
            settings, str(feature_id), "prd", actor="tester"
        )
        assert summary is not None
        assert summary["children_abandoned"] == 1
        assert summary["artifacts_deleted"] == 1
        assert summary["target_status"] == "draft"

        db.rollback()
        assert (
            db.execute(
                "select status, abandoned_at is not null as abandoned "
                "from public.issues where id = %s",
                (child_id,),
            ).fetchone()["abandoned"]
        )
        assert (
            db.execute(
                "select status from public.issues where id = %s", (feature_id,)
            ).fetchone()["status"]
            == "draft"
        )
        assert (
            db.execute(
                "select count(*) as n from public.artifacts where issue_id = %s "
                "and kind = 'prd'",
                (feature_id,),
            ).fetchone()["n"]
            == 0
        )
    finally:
        _cleanup_tree(db, feature_id, child_id)


def test_reset_prd_refused_on_a_story(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        with pytest.raises(app_db.ResetStageError):
            app_db.reset_issue_to_stage(settings, str(issue_id), "prd")
    finally:
        _cleanup(db, issue_id)


# --------------------------------- US-15.15: cooperative stop + acknowledge


def _claimed_run(db, ctx, issue_id):
    """Dispatch a plan run on a draft story and simulate a live claim."""
    run_id = db.execute(
        "select public.dispatch_issue(%s) as id", (issue_id,)
    ).fetchone()["id"]
    worker_id = db.execute(
        "insert into public.workers (org_id, name, type, token_hash, token_last4) "
        "values (%s, 'stop-worker', 'autonomous', %s, '0000') returning id",
        (ctx["org_id"], f"h{uuid.uuid4().hex}"),
    ).fetchone()["id"]
    db.execute(
        "update public.runs set status = 'running', worker_id = %s, claimed_at = now() "
        "where id = %s",
        (worker_id, run_id),
    )
    db.execute("update public.issues set status = 'planning' where id = %s", (issue_id,))
    db.commit()
    return run_id, worker_id


def test_request_stop_only_flags_a_running_run(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        # queued, not running → refused
        assert app_db.request_run_stop(settings, str(run_id)) is False
        db.execute(
            "update public.runs set status = 'running' where id = %s", (run_id,)
        )
        db.commit()
        assert app_db.request_run_stop(settings, str(run_id)) is True
        db.rollback()
        assert (
            db.execute(
                "select stop_requested_at from public.runs where id = %s", (run_id,)
            ).fetchone()["stop_requested_at"]
            is not None
        )
    finally:
        db.execute("delete from public.workers where name = 'stop-worker'")
        db.commit()
        _cleanup(db, issue_id)


def test_acknowledge_stop_requeues_and_restores(db, settings, ctx):
    issue_id = _draft_story(db, ctx)
    run_id, worker_id = _claimed_run(db, ctx, issue_id)
    try:
        # no stop requested yet → nothing to acknowledge
        assert app_db.acknowledge_stop(settings, str(run_id), str(worker_id)) is None

        app_db.request_run_stop(settings, str(run_id))
        # a different worker cannot acknowledge
        assert (
            app_db.acknowledge_stop(settings, str(run_id), str(uuid.uuid4())) is None
        )

        summary = app_db.acknowledge_stop(
            settings, str(run_id), str(worker_id), note="reverted my branch"
        )
        assert summary is not None
        assert summary["reset_to_status"] == "draft"

        db.rollback()
        run = db.execute(
            "select status, worker_id, stop_requested_at from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["status"] == "queued"
        assert run["worker_id"] is None
        assert run["stop_requested_at"] is None
        assert (
            db.execute(
                "select status from public.issues where id = %s", (issue_id,)
            ).fetchone()["status"]
            == "draft"
        )
        assert (
            db.execute(
                "select count(*) as n from public.issue_events where issue_id = %s "
                "and type = 'run-stopped'",
                (issue_id,),
            ).fetchone()["n"]
            == 1
        )
    finally:
        db.rollback()
        db.execute("delete from public.workers where id = %s", (worker_id,))
        db.commit()
        _cleanup(db, issue_id)
