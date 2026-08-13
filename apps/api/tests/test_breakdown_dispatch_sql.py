"""US-15.9: a feature can only be broken down once.

dispatch_breakdown must refuse a second run while a breakdown for the same
issue is already queued/running/succeeded — the old guard only checked for
existing *children*, which aren't created until a breakdown run completes, so
two dispatches seconds apart both slipped through and doubled the children.

Runs against DATABASE_URL (apps/api/.env). Skips if the DB is unreachable or
no project exists.
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


def _ready_feature_with_prd(db, project):
    """A childless 'ready' feature with an approved PRD — the exact
    precondition dispatch_breakdown accepts. Mirrors test_dispatch_issue_sql:
    no epic_id/item_no, so the assign_issue_number trigger numbers it under the
    project's active epic (the same assumption the sibling suite already makes)."""
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%(id)s, %(org_id)s, %(project_id)s, 'feature', %(title)s,
                'body', %(ac)s::jsonb, 'ready')
        """,
        {
            "id": issue_id,
            "org_id": project["org_id"],
            "project_id": project["id"],
            "title": f"sql-test breakdown {issue_id}",
            "ac": json.dumps([]),
        },
    )
    db.execute(
        """
        insert into public.artifacts
          (org_id, issue_id, kind, content, version, status, created_by)
        values (%s, %s, 'prd', '# PRD', 1, 'approved', 'agent')
        """,
        (project["org_id"], issue_id),
    )
    db.commit()
    return issue_id


def _cleanup(db, issue_id):
    db.rollback()
    db.execute("delete from public.runs where issue_id = %s", (issue_id,))
    db.execute("delete from public.issue_events where issue_id = %s", (issue_id,))
    db.execute("delete from public.artifacts where issue_id = %s", (issue_id,))
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_first_breakdown_dispatch_queues_a_run(db, project):
    issue_id = _ready_feature_with_prd(db, project)
    try:
        run_id = db.execute(
            "select public.dispatch_breakdown(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, status from public.runs where id = %s", (run_id,)
        ).fetchone()
        assert run["kind"] == "breakdown"
        assert run["status"] == "queued"
    finally:
        _cleanup(db, issue_id)


def test_second_breakdown_dispatch_is_refused_while_first_queued(db, project):
    """The bug: with the first run still queued (no children yet), the old
    guard saw 'no children' and let a second run through."""
    issue_id = _ready_feature_with_prd(db, project)
    try:
        db.execute("select public.dispatch_breakdown(%s)", (issue_id,))
        db.commit()

        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_breakdown(%s)", (issue_id,))
            db.commit()
        assert "already in progress or complete" in str(exc.value).lower()

        db.rollback()
        n = db.execute(
            "select count(*) as n from public.runs"
            " where issue_id = %s and kind = 'breakdown'",
            (issue_id,),
        ).fetchone()["n"]
        assert n == 1, "the refused second dispatch must not leave a run behind"
    finally:
        _cleanup(db, issue_id)


def test_second_breakdown_dispatch_is_refused_after_first_succeeded(db, project):
    issue_id = _ready_feature_with_prd(db, project)
    try:
        run_id = db.execute(
            "select public.dispatch_breakdown(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.execute(
            "update public.runs set status = 'succeeded' where id = %s", (run_id,)
        )
        db.commit()

        with pytest.raises(Exception) as exc:
            db.execute("select public.dispatch_breakdown(%s)", (issue_id,))
            db.commit()
        assert "already in progress or complete" in str(exc.value).lower()
    finally:
        _cleanup(db, issue_id)


def test_failed_breakdown_allows_a_fresh_dispatch(db, project):
    """A breakdown that failed leaves the feature un-split — re-dispatch must
    still be allowed (only queued/running/succeeded block)."""
    issue_id = _ready_feature_with_prd(db, project)
    try:
        run_id = db.execute(
            "select public.dispatch_breakdown(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.execute(
            "update public.runs set status = 'failed' where id = %s", (run_id,)
        )
        db.commit()

        run2 = db.execute(
            "select public.dispatch_breakdown(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        assert run2 != run_id
        status = db.execute(
            "select status from public.runs where id = %s", (run2,)
        ).fetchone()["status"]
        assert status == "queued"
    finally:
        _cleanup(db, issue_id)
