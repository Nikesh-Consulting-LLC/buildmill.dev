"""US-5.11: live SQL coverage — instruction set seeded at dispatch from the
US-5.14 kind templates + story/AC, never overwritten once present, and
inherited automatically by retries (it lives on the issue, not the run).

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

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
def ctx(db):
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    return {"project_id": project["id"], "org_id": project["org_id"]}


@pytest.fixture
def story_issue(db, ctx):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'The story body.',
                '["works offline", "has tests"]'::jsonb, 'draft')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"iset-test {issue_id}"),
    )
    db.commit()
    yield issue_id
    db.rollback()
    # the removal guard blocks deleting queued/running items — settle first
    db.execute(
        "update public.runs set status = 'failed' where issue_id = %s",
        (issue_id,),
    )
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def _instruction_set(db, issue_id):
    return db.execute(
        "select instruction_set from public.issues where id = %s", (issue_id,)
    ).fetchone()["instruction_set"]


def test_dispatch_seeds_from_kind_template_story_and_criteria(
    db, ctx, story_issue
):
    db.execute("select public.dispatch_issue(%s)", (story_issue,))
    db.commit()
    seeded = _instruction_set(db, story_issue)
    assert seeded is not None
    assert "## Expectations — plan run" in seeded
    assert "The story body." in seeded
    assert "- works offline" in seeded
    assert "- has tests" in seeded
    # the kind expectations come from the US-5.14 template source
    template = db.execute(
        "select public.worker_instruction_for(%s, 'plan') as t",
        (ctx["project_id"],),
    ).fetchone()["t"]
    assert template.split(".")[0] in seeded

    event = db.execute(
        "select 1 from public.issue_events "
        "where issue_id = %s and type = 'instructions-seeded'",
        (story_issue,),
    ).fetchone()
    assert event is not None


def test_manager_content_is_never_overwritten_and_retries_inherit(
    db, ctx, story_issue
):
    db.execute(
        "update public.issues set instruction_set = 'MANAGER PLAN — keep me' "
        "where id = %s",
        (story_issue,),
    )
    db.execute("select public.dispatch_issue(%s)", (story_issue,))
    db.commit()
    assert _instruction_set(db, story_issue) == "MANAGER PLAN — keep me"

    # retry path: fail the run, re-dispatch — the issue-attached set survives
    db.execute(
        "update public.runs set status = 'failed' where issue_id = %s",
        (story_issue,),
    )
    db.execute(
        "update public.issues set status = 'failed' where id = %s",
        (story_issue,),
    )
    db.execute("select public.dispatch_issue(%s)", (story_issue,))
    db.commit()
    assert _instruction_set(db, story_issue) == "MANAGER PLAN — keep me"


def test_prd_dispatch_seeds_prd_expectations(db, ctx):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'feature', %s, 'Raw idea.', '[]'::jsonb, 'draft')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"iset-prd {issue_id}"),
    )
    try:
        db.execute("select public.dispatch_prd_draft(%s)", (issue_id,))
        db.commit()
        seeded = _instruction_set(db, issue_id)
        assert "## Expectations — prd run" in seeded
        assert "Raw idea." in seeded
    finally:
        db.rollback()
        db.execute(
            "update public.runs set status = 'failed' where issue_id = %s",
            (issue_id,),
        )
        db.execute(
            "update public.issues set status = 'failed' where id = %s",
            (issue_id,),
        )
        db.execute("delete from public.issues where id = %s", (issue_id,))
        db.commit()
