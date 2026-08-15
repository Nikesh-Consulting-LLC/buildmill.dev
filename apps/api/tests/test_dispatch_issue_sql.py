"""US-2.5: live SQL coverage for dispatch_issue kind selection.

Runs against DATABASE_URL (apps/api/.env). Skips if the DB is unreachable
or no project exists.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row


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


@pytest.fixture
def project(db):
    db.rollback()
    row = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not row:
        pytest.skip("no project in database")
    return row


def _insert_issue(db, project, **extra):
    issue_id = uuid.uuid4()
    cols = {
        "id": issue_id,
        "org_id": project["org_id"],
        "project_id": project["id"],
        "type": "story",
        "title": f"sql-test {issue_id}",
        "body": "body",
        "acceptance_criteria": json.dumps(["ok"]),
        "status": "draft",
    }
    cols.update(extra)
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%(id)s, %(org_id)s, %(project_id)s, %(type)s, %(title)s,
                %(body)s, %(acceptance_criteria)s::jsonb, %(status)s)
        """,
        cols,
    )
    db.commit()
    return issue_id


def _cleanup_issue(db, issue_id):
    db.rollback()
    # Clear active status so guard_issue_removal allows delete.
    db.execute(
        """
        update public.issues
        set status = 'draft'
        where id = %s and status in ('queued', 'running', 'planning')
        """,
        (issue_id,),
    )
    db.execute("delete from public.runs where issue_id = %s", (issue_id,))
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_draft_dispatches_plan_kind(db, project):
    issue_id = _insert_issue(db, project, status="draft")
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, status from public.runs where id = %s", (run_id,)
        ).fetchone()
        issue = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert run["kind"] == "plan"
        assert run["status"] == "queued"
        assert issue["status"] == "queued"
    finally:
        _cleanup_issue(db, issue_id)


def test_code_blocked_without_approved_plan(db, project):
    issue_id = _insert_issue(db, project, status="planned")
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s)", (issue_id,))
            db.commit()
        assert "approved plan" in str(exc.value).lower() or "not dispatchable" in str(
            exc.value
        ).lower()
    finally:
        _cleanup_issue(db, issue_id)


def test_planned_with_approved_plan_dispatches_code(db, project):
    issue_id = _insert_issue(db, project, status="planned")
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values
              (%s, %s, 'plan', '# Plan', 1, 'approved', 'agent'),
              (%s, %s, 'test_plan', '# Tests', 1, 'approved', 'agent')
            """,
            (project["org_id"], issue_id, project["org_id"], issue_id),
        )
        db.commit()
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, input_context from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "code"
        assert run["input_context"].get("plan") == "# Plan"
        assert run["input_context"].get("test_plan") == "# Tests"
    finally:
        _cleanup_issue(db, issue_id)


def test_named_plan_kind_replans_a_story_that_already_has_a_plan(db, project):
    """Migration 166: `planned` + an approved plan infers `code`; naming
    `plan` re-plans instead.

    This is the whole reason the parameter exists — without it a manager who
    wants one story's plan rewritten has to hand-edit its status first, and the
    inference will happily build against the plan they were trying to replace.
    """
    issue_id = _insert_issue(db, project, status="planned")
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'plan', '# Plan', 1, 'approved', 'agent')
            """,
            (project["org_id"], issue_id),
        )
        db.commit()
        run_id = db.execute(
            "select public.dispatch_issue(%s, 'plan') as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, input_context from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "plan"
        # The plan being replaced rides along, so the agent revises rather
        # than rewriting from nothing.
        assert run["input_context"].get("previous_plan") == "# Plan"

        # Selected by type, not by recency: seed_issue_instructions writes its
        # own event in the same transaction, so `now()` ties and "the latest
        # row" is whichever the planner happens to return first.
        event = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'plan-dispatched'
            order by created_at desc limit 1
            """,
            (issue_id,),
        ).fetchone()
        assert event["payload"].get("kind_chosen_by") == "manager"
    finally:
        db.rollback()
        db.execute("delete from public.artifacts where issue_id = %s", (issue_id,))
        db.commit()
        _cleanup_issue(db, issue_id)


def test_named_code_kind_still_needs_an_approved_plan(db, project):
    """Naming a phase chooses between the legal ones; it buys past nothing."""
    issue_id = _insert_issue(db, project, status="planned")
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s, 'code')", (issue_id,))
            db.commit()
        assert "approved plan" in str(exc.value).lower()

        db.rollback()
        runs = db.execute(
            "select count(*) as n from public.runs where issue_id = %s", (issue_id,)
        ).fetchone()
        assert runs["n"] == 0, "a refused dispatch must not leave a run behind"
    finally:
        _cleanup_issue(db, issue_id)


def test_named_plan_kind_refused_from_an_illegal_status(db, project):
    issue_id = _insert_issue(db, project, status="queued")
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s, 'plan')", (issue_id,))
            db.commit()
        assert "not dispatchable for planning" in str(exc.value).lower()
    finally:
        _cleanup_issue(db, issue_id)


def test_unknown_named_kind_is_refused(db, project):
    issue_id = _insert_issue(db, project, status="ready")
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s, 'review')", (issue_id,))
            db.commit()
        assert "unknown run kind" in str(exc.value).lower()
    finally:
        _cleanup_issue(db, issue_id)


def test_default_inference_is_untouched_by_the_new_parameter(db, project):
    """The one-argument call is still the one-argument call — same kind, and
    an event that says the factory chose it rather than a person."""
    issue_id = _insert_issue(db, project, status="ready")
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "plan"

        event = db.execute(
            """
            select payload from public.issue_events
            where issue_id = %s and type = 'plan-dispatched'
            order by created_at desc limit 1
            """,
            (issue_id,),
        ).fetchone()
        assert event["payload"].get("kind_chosen_by") == "inferred"
    finally:
        _cleanup_issue(db, issue_id)


def test_feature_without_prd_cannot_plan(db, project):
    issue_id = _insert_issue(
        db, project, type="feature", status="ready", acceptance_criteria="[]"
    )
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s)", (issue_id,))
            db.commit()
        assert "not planned directly" in str(exc.value).lower()
    finally:
        _cleanup_issue(db, issue_id)


def test_feature_with_approved_prd_still_cannot_plan(db, project):
    """US-11.2: a feature is never planned directly, PRD or no PRD.

    Before migration 104 this was the hole: dispatch_issue never consulted
    issues.type when choosing the kind, so a childless feature that was
    'ready' with an approved PRD satisfied every guard and produced a plan
    run in the pool that no worker should ever have been offered.
    """
    issue_id = _insert_issue(
        db, project, type="feature", status="ready", acceptance_criteria="[]"
    )
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'prd', '# PRD', 1, 'approved', 'agent')
            """,
            (project["org_id"], issue_id),
        )
        db.commit()
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s)", (issue_id,))
            db.commit()
        assert "not planned directly" in str(exc.value).lower()

        db.rollback()
        runs = db.execute(
            "select count(*) as n from public.runs where issue_id = %s", (issue_id,)
        ).fetchone()
        assert runs["n"] == 0, "a refused dispatch must not leave a run behind"
    finally:
        db.rollback()
        db.execute("delete from public.artifacts where issue_id = %s", (issue_id,))
        db.commit()
        _cleanup_issue(db, issue_id)


# ---------------------------------------------------------------- us-96.1
# A chore is one shot: dispatch builds it, retry never re-plans, and its
# instructions resolve to the 'chore' kind.


def test_chore_dispatches_code_kind_from_draft(db, project):
    """A draft chore's dispatch creates a code run with no plan gate, no
    plan/test_plan context keys, and a 'dispatched' (not 'plan-dispatched')
    event."""
    issue_id = _insert_issue(db, project, type="chore", status="draft")
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, input_context from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "code"
        assert "plan" not in run["input_context"]
        assert "test_plan" not in run["input_context"]
        event = db.execute(
            """
            select type from public.issue_events
            where issue_id = %s and type in ('dispatched', 'plan-dispatched')
            order by created_at desc limit 1
            """,
            (issue_id,),
        ).fetchone()
        assert event["type"] == "dispatched"
    finally:
        _cleanup_issue(db, issue_id)


def test_chore_refuses_named_plan_kind(db, project):
    issue_id = _insert_issue(db, project, type="chore", status="draft")
    try:
        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s, 'plan')", (issue_id,))
            db.commit()
        assert "no planning phase" in str(exc.value)
    finally:
        _cleanup_issue(db, issue_id)


def test_chore_retry_is_always_code(db, project):
    """needs-fixes and failed both infer 'code' — a chore never re-plans,
    where a story at 'failed' without an approved plan would."""
    issue_id = _insert_issue(db, project, type="chore", status="needs-fixes")
    try:
        kind = db.execute(
            "select public.dispatch_kind_for(%s) as k", (issue_id,)
        ).fetchone()["k"]
        assert kind == "code"
        db.rollback()
        db.execute(
            "update public.issues set status = 'failed' where id = %s",
            (issue_id,),
        )
        db.commit()
        kind = db.execute(
            "select public.dispatch_kind_for(%s) as k", (issue_id,)
        ).fetchone()["k"]
        assert kind == "code"
    finally:
        _cleanup_issue(db, issue_id)


def test_chore_instruction_kind_resolves_chore(db, project):
    """instruction_kind_for maps a chore's code run to 'chore' and leaves a
    feature-child story on plan/code. (A parentless story maps to the
    standalone pair since us-96.3 — pinned separately below.)"""
    chore_id = _insert_issue(db, project, type="chore", status="draft")
    feature_id = _insert_issue(
        db, project, type="feature", status="draft", acceptance_criteria="[]"
    )
    child_id = _insert_issue(db, project, status="draft")
    # _insert_issue's INSERT enumerates fixed columns, so parenting is a
    # second statement, not a kwarg.
    db.execute(
        "update public.issues set parent_id = %s where id = %s",
        (feature_id, child_id),
    )
    db.commit()
    try:
        row = db.execute(
            """
            select public.instruction_kind_for(%s, 'code') as chore_code,
                   public.instruction_kind_for(%s, 'plan') as chore_plan,
                   public.instruction_kind_for(%s, 'code') as child_code,
                   public.instruction_kind_for(%s, 'plan') as child_plan,
                   public.instruction_kind_for(null, 'code') as no_issue
            """,
            (chore_id, chore_id, child_id, child_id),
        ).fetchone()
        assert row["chore_code"] == "chore"
        # A chore never has a plan run; identity here is harmless and keeps
        # the mapping total.
        assert row["chore_plan"] == "plan"
        assert row["child_code"] == "code"
        assert row["child_plan"] == "plan"
        assert row["no_issue"] == "code"
    finally:
        _cleanup_issue(db, chore_id)
        _cleanup_issue(db, child_id)
        _cleanup_issue(db, feature_id)


# ---------------------------------------------------------------- us-96.2
# A bug explains itself: the lifecycle is untouched (plan-kind run), but the
# instruction resolution turns it into an RCA.


def test_bug_dispatch_stays_plan_kind_with_rca_instructions(db, project):
    """A draft bug still dispatches a plan-kind run — us-96.2 changes the
    words, not the machinery — and the seeded instruction set carries the
    bug_rca text, not the story plan text."""
    issue_id = _insert_issue(db, project, type="bug", status="draft")
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "plan"
        seeded = db.execute(
            "select instruction_set from public.issues where id = %s",
            (issue_id,),
        ).fetchone()["instruction_set"]
        assert "root cause analysis" in seeded
        assert "NO diffs" in seeded
    finally:
        _cleanup_issue(db, issue_id)


def test_bug_instruction_kinds_resolve_rca_and_fix(db, project):
    issue_id = _insert_issue(db, project, type="bug", status="draft")
    try:
        row = db.execute(
            """
            select public.instruction_kind_for(%s, 'plan') as p,
                   public.instruction_kind_for(%s, 'code') as c
            """,
            (issue_id, issue_id),
        ).fetchone()
        assert row["p"] == "bug_rca"
        assert row["c"] == "bug_fix"
    finally:
        _cleanup_issue(db, issue_id)


# ---------------------------------------------------------------- us-96.3
def test_standalone_story_instruction_kinds(db, project):
    """A story with no parent feature resolves the standalone pair; the
    feature-child side is pinned in the us-96.1 block above."""
    story_id = _insert_issue(db, project, status="draft")
    try:
        row = db.execute(
            """
            select public.instruction_kind_for(%s, 'plan') as p,
                   public.instruction_kind_for(%s, 'code') as c
            """,
            (story_id, story_id),
        ).fetchone()
        assert row["p"] == "standalone_plan"
        assert row["c"] == "standalone_code"
    finally:
        _cleanup_issue(db, story_id)


# ---------------------------------------------------------------- us-96.4
# The feature owns the steering wheel: initial planning flows through the
# batch; remediation and revision stay individual.


def _switch2(db, project) -> bool:
    return db.execute(
        "select coalesce(route_feature_as_one, true) as s "
        "from public.projects where id = %s",
        (project["id"],),
    ).fetchone()["s"]


def test_feature_owns_the_initial_plan(db, project):
    if not _switch2(db, project):
        pytest.skip("route_feature_as_one is off for this project")
    feature_id = _insert_issue(
        db, project, type="feature", status="ready", acceptance_criteria="[]"
    )
    child_id = _insert_issue(db, project, status="draft")
    db.execute(
        "update public.issues set parent_id = %s where id = %s",
        (feature_id, child_id),
    )
    db.commit()
    try:
        refusal = db.execute(
            "select public.issue_dispatch_refusal(%s, 'plan') as r", (child_id,)
        ).fetchone()["r"]
        assert refusal is not None and "owns the plan" in refusal

        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_issue(%s, 'plan')", (child_id,))
            db.commit()
        assert "owns the plan" in str(exc.value)
        db.rollback()

        # Revision exemption: a plan artifact in ANY state lifts the refusal
        # (the /replan endpoint supersedes artifacts, then dispatches).
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'plan', '# old plan', 1, 'superseded', 'agent')
            """,
            (project["org_id"], child_id),
        )
        db.commit()
        refusal = db.execute(
            "select public.issue_dispatch_refusal(%s, 'plan') as r", (child_id,)
        ).fetchone()["r"]
        assert refusal is None

        # Trouble exemption: failed dispatches individually even unplanned.
        db.execute("delete from public.artifacts where issue_id = %s", (child_id,))
        db.execute(
            "update public.issues set status = 'failed' where id = %s", (child_id,)
        )
        db.commit()
        refusal = db.execute(
            "select public.issue_dispatch_refusal(%s, 'plan') as r", (child_id,)
        ).fetchone()["r"]
        assert refusal is None
    finally:
        db.rollback()
        db.execute("delete from public.artifacts where issue_id = %s", (child_id,))
        db.commit()
        _cleanup_issue(db, child_id)
        _cleanup_issue(db, feature_id)


def test_feature_batch_plans_late_arrivals(db, project):
    """A story added after its siblings were planned used to wedge the
    feature ('not ready to build' from the batch, refused individually).
    The batch now plans exactly the unplanned children."""
    if not _switch2(db, project):
        pytest.skip("route_feature_as_one is off for this project")
    feature_id = _insert_issue(
        db, project, type="feature", status="ready", acceptance_criteria="[]"
    )
    planned_id = _insert_issue(db, project, status="planned")
    late_id = _insert_issue(db, project, status="draft")
    db.execute(
        "update public.issues set parent_id = %s where id in (%s, %s)",
        (feature_id, planned_id, late_id),
    )
    db.execute(
        """
        insert into public.artifacts
          (org_id, issue_id, kind, content, version, status, created_by)
        values (%s, %s, 'plan', '# Plan', 1, 'approved', 'agent')
        """,
        (project["org_id"], planned_id),
    )
    db.commit()
    try:
        result = db.execute(
            "select public.dispatch_feature_batch(%s) as r", (feature_id,)
        ).fetchone()["r"]
        db.commit()
        assert result["phase"] == "plan"
        dispatched_ids = {d["issue_id"] for d in result["dispatched"]}
        assert str(late_id) in dispatched_ids
        assert str(planned_id) not in dispatched_ids
        assert result["skipped"] == []
    finally:
        db.rollback()
        db.execute("delete from public.artifacts where issue_id = %s", (planned_id,))
        db.commit()
        _cleanup_issue(db, late_id)
        _cleanup_issue(db, planned_id)
        _cleanup_issue(db, feature_id)
