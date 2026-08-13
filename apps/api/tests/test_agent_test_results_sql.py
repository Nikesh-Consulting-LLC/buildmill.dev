"""US-5.19: live SQL coverage — agent-reported test results land as
agent-sourced test runs, upserted latest-wins, claim-guarded, org-scoped.

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
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    return {"project_id": project["id"], "org_id": project["org_id"]}


@pytest.fixture
def worker(db, ctx):
    token = f"sfw_test_{uuid.uuid4().hex}"
    row = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'results-test', 'autonomous', %s, %s) returning id
        """,
        (ctx["org_id"], hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    db.commit()
    yield {
        "id": row["id"],
        "org_id": ctx["org_id"],
        "name": "results-test",
        "type": "autonomous",
    }
    db.rollback()
    db.execute("delete from public.workers where id = %s", (row["id"],))
    db.commit()


@pytest.fixture
def scene(db, ctx, worker):
    """A running claimed run + two test cases on its issue; cleaned up."""
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    case_ids = [uuid.uuid4(), uuid.uuid4()]
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '["ok"]'::jsonb, 'queued')
        """,
        (issue_id, ctx["org_id"], ctx["project_id"], f"results-test {issue_id}"),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context, worker_id)
        values (%s, %s, %s, 'claude', 'running', 'code', '{}'::jsonb, %s)
        """,
        (run_id, ctx["org_id"], issue_id, worker["id"]),
    )
    for i, case_id in enumerate(case_ids):
        db.execute(
            """
            insert into public.test_cases
              (id, org_id, project_id, issue_id, title, steps, expected_result, source)
            values (%s, %s, %s, %s, %s, 'steps', 'expected', 'agent')
            """,
            (case_id, ctx["org_id"], ctx["project_id"], issue_id, f"case {i}"),
        )
    db.commit()
    yield {"issue_id": issue_id, "run_id": run_id, "case_ids": case_ids}
    db.rollback()
    db.execute(
        "delete from public.test_runs where run_id = %s", (run_id,)
    )
    db.execute("delete from public.test_cases where issue_id = %s", (issue_id,))
    db.execute("update public.issues set status = 'draft' where id = %s", (issue_id,))
    db.execute("delete from public.runs where issue_id = %s", (issue_id,))
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def _latest_results(db, case_ids):
    """The exact shape the us-2.6 gate consumes: latest result per case."""
    rows = db.execute(
        """
        select distinct on (test_case_id) test_case_id, result, note
        from public.test_run_results
        where test_case_id = any(%s)
        order by test_case_id, recorded_at desc
        """,
        (case_ids,),
    ).fetchall()
    return {str(r["test_case_id"]): r for r in rows}


def test_report_records_agent_run_with_attribution(db, settings, worker, scene):
    out = app_db.report_test_results(
        settings,
        str(scene["run_id"]),
        worker,
        [
            {
                "test_case_id": str(scene["case_ids"][0]),
                "status": "passed",
                "evidence": "pytest: 18 passed",
            },
            {
                "test_case_id": str(scene["case_ids"][1]),
                "status": "failed",
                "evidence": "assertion error in step 2",
            },
        ],
    )
    assert out["ok"] and out["recorded"] == 2

    db.rollback()
    run = db.execute(
        "select * from public.test_runs where id = %s", (out["test_run_id"],)
    ).fetchone()
    assert run["source"] == "agent"
    assert run["worker_id"] == worker["id"]
    assert str(run["run_id"]) == str(scene["run_id"])
    assert run["worker_name"] == "results-test"
    assert run["started_by"] is None

    latest = _latest_results(db, scene["case_ids"])
    assert latest[str(scene["case_ids"][0])]["result"] == "pass"
    assert latest[str(scene["case_ids"][1])]["result"] == "fail"
    assert "assertion error" in latest[str(scene["case_ids"][1])]["note"]


def test_rereport_replaces_previous_result(db, settings, worker, scene):
    case = str(scene["case_ids"][0])
    first = app_db.report_test_results(
        settings, str(scene["run_id"]), worker,
        [{"test_case_id": case, "status": "failed", "evidence": "flaky"}],
    )
    second = app_db.report_test_results(
        settings, str(scene["run_id"]), worker,
        [{"test_case_id": case, "status": "passed", "evidence": "fixed"}],
    )
    # Same agent run, one row per case, latest outcome wins — the gate
    # sees 'pass', so the case no longer counts as unrun/failing.
    assert first["test_run_id"] == second["test_run_id"]
    db.rollback()
    rows = db.execute(
        "select result, note from public.test_run_results where test_case_id = %s",
        (case,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["result"] == "pass"
    assert rows[0]["note"] == "fixed"


def test_report_requires_claim_holder(db, settings, worker, scene):
    stranger = {"id": uuid.uuid4(), "org_id": worker["org_id"], "name": "other"}
    out = app_db.report_test_results(
        settings, str(scene["run_id"]), stranger,
        [{"test_case_id": str(scene["case_ids"][0]), "status": "passed"}],
    )
    assert "claim holder" in out["error"]


def test_report_unknown_case_id_is_actionable(db, settings, worker, scene):
    bogus = str(uuid.uuid4())
    out = app_db.report_test_results(
        settings, str(scene["run_id"]), worker,
        [{"test_case_id": bogus, "status": "passed"}],
    )
    assert bogus in out["error"]
    assert out["unknown_ids"] == [bogus]


def test_report_cross_org_run_is_not_found(db, settings, scene):
    foreign = {"id": uuid.uuid4(), "org_id": uuid.uuid4(), "name": "foreign"}
    out = app_db.report_test_results(
        settings, str(scene["run_id"]), foreign,
        [{"test_case_id": str(scene["case_ids"][0]), "status": "passed"}],
    )
    assert out is None
