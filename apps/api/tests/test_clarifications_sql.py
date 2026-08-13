"""US-5.4: live SQL coverage — ask/answer round-trip at the db layer.
A claim-holder's question is recorded with its audit event, the manager's
answer comes back through the same rows, and a retry run on the same work
item sees the whole exchange.

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

from app import db as dbmod
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
    if not url:
        pytest.skip("DATABASE_URL not set")
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


@pytest.fixture
def worker(db, ctx):
    worker_id = uuid.uuid4()
    token_hash = hashlib.sha256(f"clar-test-{worker_id}".encode()).hexdigest()
    row = db.execute(
        """
        insert into public.workers (id, org_id, name, type, token_hash, token_last4)
        values (%s, %s, %s, 'human', %s, '0000')
        returning id, org_id, name, type, status
        """,
        (worker_id, ctx["org_id"], f"clar-tester {worker_id}", token_hash),
    ).fetchone()
    db.commit()
    yield dict(row)
    db.rollback()
    db.execute("delete from public.workers where id = %s", (worker_id,))
    db.commit()


@pytest.fixture
def issue_and_run(db, ctx, worker):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'Body.', '[]'::jsonb, 'running')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"clar-test {issue_id}"),
    )
    run = db.execute(
        """
        insert into public.runs
          (org_id, issue_id, provider, status, kind, worker_id,
           input_context, claimed_at, claim_expires_at)
        values (%s, %s, 'claude', 'running', 'code', %s, '{}'::jsonb, now(),
                now() + interval '15 minutes')
        returning id, org_id, issue_id, kind
        """,
        (ctx["org_id"], issue_id, worker["id"]),
    ).fetchone()
    db.commit()
    yield issue_id, dict(run)
    db.rollback()
    db.execute(
        "update public.runs set status = 'failed' where issue_id = %s", (issue_id,)
    )
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_ask_answer_round_trip(db, settings, worker, issue_and_run):
    issue_id, run = issue_and_run

    row = dbmod.add_clarification(
        settings, run, worker, "Should exports include archived rows?"
    )
    assert row["id"] is not None

    # audit event on the issue timeline, naming the worker
    event = db.execute(
        "select payload from public.issue_events "
        "where issue_id = %s and type = 'clarification-asked'",
        (issue_id,),
    ).fetchone()
    assert event is not None
    assert event["payload"]["question"] == "Should exports include archived rows?"
    assert event["payload"]["worker"] == worker["name"]

    # pending until the manager answers
    listed = dbmod.list_run_clarifications(settings, run)
    assert len(listed) == 1
    assert listed[0]["answer"] is None

    db.execute(
        "update public.clarifications set answer = 'No — active only', "
        "answered_at = now() where id = %s",
        (row["id"],),
    )
    db.commit()

    listed = dbmod.list_run_clarifications(settings, run)
    assert listed[0]["answer"] == "No — active only"
    assert listed[0]["answered_at"] is not None


def test_retry_run_sees_prior_exchange(db, settings, worker, issue_and_run):
    issue_id, run = issue_and_run
    dbmod.add_clarification(settings, run, worker, "Which currency format?")

    retry = db.execute(
        """
        insert into public.runs
          (org_id, issue_id, provider, status, kind, worker_id,
           input_context, claimed_at, claim_expires_at)
        values (%s, %s, 'claude', 'running', 'code', %s, '{}'::jsonb, now(),
                now() + interval '15 minutes')
        returning id, org_id, issue_id, kind
        """,
        (run["org_id"], issue_id, worker["id"]),
    ).fetchone()
    db.commit()

    listed = dbmod.list_run_clarifications(settings, dict(retry))
    assert [c["question"] for c in listed] == ["Which currency format?"]


# --- US-14.9: questions that offer choices -------------------------------


def test_options_shape_is_constrained(db, ctx, worker):
    """A malformed option set is refused by the database, not rendered as
    a broken form. One option is not a choice; six is the ceiling."""
    issue = db.execute(
        """
        insert into public.issues (org_id, project_id, title, type, status)
        values (%s, %s, 'us-14.9 probe', 'chore', 'draft') returning id
        """,
        (ctx["org_id"], ctx["project_id"]),
    ).fetchone()
    run = db.execute(
        """
        insert into public.runs
          (org_id, project_id, issue_id, kind, status, input_context)
        values (%s, %s, %s, 'code', 'running', '{}'::jsonb) returning id
        """,
        (ctx["org_id"], ctx["project_id"], issue["id"]),
    ).fetchone()

    def insert(options_json):
        return db.execute(
            """
            insert into public.clarifications
              (org_id, issue_id, run_id, worker_id, question, options)
            values (%s, %s, %s, %s, 'which?', %s::jsonb) returning id
            """,
            (
                ctx["org_id"],
                issue["id"],
                run["id"],
                worker["id"],
                options_json,
            ),
        )

    with pytest.raises(psycopg.errors.CheckViolation):
        insert('[{"label": "only one"}]')
    db.rollback()


def test_a_question_without_options_is_unchanged(db, ctx, worker):
    """The additive promise: options null must behave exactly as before,
    so every stored question and every existing caller keeps working."""
    issue = db.execute(
        """
        insert into public.issues (org_id, project_id, title, type, status)
        values (%s, %s, 'us-14.9 legacy', 'chore', 'draft') returning id
        """,
        (ctx["org_id"], ctx["project_id"]),
    ).fetchone()
    run = db.execute(
        """
        insert into public.runs
          (org_id, project_id, issue_id, kind, status, input_context)
        values (%s, %s, %s, 'code', 'running', '{}'::jsonb) returning id
        """,
        (ctx["org_id"], ctx["project_id"], issue["id"]),
    ).fetchone()
    row = db.execute(
        """
        insert into public.clarifications
          (org_id, issue_id, run_id, worker_id, question)
        values (%s, %s, %s, %s, 'open question?')
        returning options, multi_select, selected_options
        """,
        (ctx["org_id"], issue["id"], run["id"], worker["id"]),
    ).fetchone()
    assert row["options"] is None
    assert row["multi_select"] is False
    assert row["selected_options"] is None
    db.rollback()
