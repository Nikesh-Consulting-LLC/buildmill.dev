"""US-91.11 / US-91.14: the agent-effort ledger's rules, against a live DB.

Seconds are what the database stores; hours are a rendering. These pin the
things a reader of the Team page would be misled by if they broke:

  * a terminal run gets `work_seconds`, measured from the CLAIM, and never
    null — a silent null becomes a silent zero in every sum downstream;
  * a run that died holding its claim is timed to its last heartbeat, not to
    "now" and not to zero;
  * the daily rollup equals the sum of the runs it summarises;
  * a work item counts as completed ONCE, for the agent whose code run
    produced the merge;
  * an item's cost includes every attempt against it, failed ones included;
  * `agent_effort_daily` is org-scoped and does not leak across workspaces.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every case
rolls back, so nothing is left behind.
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
        conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        conn.execute("select 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    yield conn
    conn.close()


@pytest.fixture()
def tx(db):
    """One rolled-back transaction per test."""
    with db.transaction(force_rollback=True):
        yield db


def _workspace(tx, name: str) -> dict:
    """An org, a project, an epic, an issue and a worker — the minimum a run
    needs to exist."""
    org = tx.execute(
        "insert into organizations (name) values (%s) returning id",
        (f"effort-{name}-{uuid.uuid4().hex[:8]}",),
    ).fetchone()["id"]
    project = tx.execute(
        "insert into projects (org_id, name, repo_full_name) "
        "values (%s, %s, %s) returning id",
        (org, f"proj-{name}", "acme/repo"),
    ).fetchone()["id"]
    epic = tx.execute(
        "insert into epics (org_id, project_id, number, title) "
        "values (%s, %s, 1, 'E') returning id",
        (org, project),
    ).fetchone()["id"]
    issue = tx.execute(
        "insert into issues (org_id, project_id, epic_id, title, type, status) "
        "values (%s, %s, %s, 'An item', 'story', 'queued') returning id",
        (org, project, epic),
    ).fetchone()["id"]
    worker = tx.execute(
        "insert into workers (org_id, name, type, status, token_hash, token_last4) "
        "values (%s, 'agent-1', 'autonomous', 'active', %s, '1234') returning id",
        (org, uuid.uuid4().hex),
    ).fetchone()["id"]
    return {"org": org, "project": project, "issue": issue, "worker": worker}


def _run(tx, w: dict, *, kind: str = "code", claimed_ago: str = "1 hour") -> str:
    return tx.execute(
        """
        insert into runs (org_id, project_id, issue_id, kind, provider, status,
                          worker_id, input_context, billing, claimed_at)
        values (%s, %s, %s, %s, 'claude', 'running', %s, '{}'::jsonb, 'metered',
                now() - %s::interval)
        returning id
        """,
        (w["org"], w["project"], w["issue"], kind, w["worker"], claimed_ago),
    ).fetchone()["id"]


def _effort(tx, w: dict) -> dict:
    row = tx.execute(
        """
        select coalesce(sum(work_seconds), 0) as secs,
               coalesce(sum(runs_finished), 0) as runs,
               coalesce(sum(issues_completed), 0) as done,
               coalesce(sum(cost_usd), 0) as cost
          from agent_effort_daily where org_id = %s and worker_id = %s
        """,
        (w["org"], w["worker"]),
    ).fetchone()
    return row


def test_a_finished_run_is_timed_from_its_claim(tx):
    w = _workspace(tx, "timed")
    run = _run(tx, w, claimed_ago="90 minutes")
    tx.execute(
        "update runs set status='succeeded', finished_at=now() where id=%s", (run,)
    )
    secs = tx.execute(
        "select work_seconds from runs where id=%s", (run,)
    ).fetchone()["work_seconds"]
    # ~5400s, allowing for the clock moving during the test.
    assert 5300 <= secs <= 5500, secs


def test_a_run_that_died_holding_its_claim_is_timed_to_its_last_heartbeat(tx):
    w = _workspace(tx, "lease")
    run = _run(tx, w, claimed_ago="3 hours")
    # No finished_at — the reaper marks it failed; the last proof of life is
    # the heartbeat, two hours before now.
    tx.execute(
        "update runs set last_heartbeat_at = now() - interval '2 hours' where id=%s",
        (run,),
    )
    tx.execute("update runs set status='failed' where id=%s", (run,))
    secs = tx.execute(
        "select work_seconds from runs where id=%s", (run,)
    ).fetchone()["work_seconds"]
    assert 3500 <= secs <= 3700, secs  # one hour of demonstrable life


def test_every_terminal_run_gets_a_number_never_null(tx):
    w = _workspace(tx, "never-null")
    for status in ("succeeded", "failed", "cancelled", "abandoned", "stopped"):
        run = _run(tx, w)
        tx.execute("update runs set status=%s where id=%s", (status, run))
        secs = tx.execute(
            "select work_seconds from runs where id=%s", (run,)
        ).fetchone()["work_seconds"]
        assert secs is not None, status


def test_the_rollup_equals_the_sum_of_its_runs(tx):
    w = _workspace(tx, "rollup")
    total = 0
    for ago in ("10 minutes", "20 minutes", "30 minutes"):
        run = _run(tx, w, claimed_ago=ago)
        tx.execute(
            "update runs set status='succeeded', finished_at=now(), cost_usd=1.50, "
            "lines_added=10, lines_removed=2, tokens_in=100, tokens_out=50 where id=%s",
            (run,),
        )
        total += tx.execute(
            "select work_seconds from runs where id=%s", (run,)
        ).fetchone()["work_seconds"]

    rolled = _effort(tx, w)
    assert rolled["secs"] == total
    assert rolled["runs"] == 3
    assert float(rolled["cost"]) == pytest.approx(4.50)


def test_a_re_run_item_counts_once_for_the_agent_that_merged_it(tx):
    w = _workspace(tx, "once")
    # Three attempts: two failed, one succeeded.
    for status in ("failed", "failed", "succeeded"):
        run = _run(tx, w, kind="code")
        tx.execute(
            "update runs set status=%s, finished_at=now() where id=%s", (status, run)
        )
    tx.execute("update issues set status='merged' where id=%s", (w["issue"],))
    assert _effort(tx, w)["done"] == 1

    # Merging is one transition; re-running the update must not double-count.
    tx.execute("update issues set status='merged' where id=%s", (w["issue"],))
    assert _effort(tx, w)["done"] == 1


def test_a_plan_run_never_completes_an_item(tx):
    w = _workspace(tx, "plan-only")
    run = _run(tx, w, kind="plan")
    tx.execute(
        "update runs set status='succeeded', finished_at=now() where id=%s", (run,)
    )
    tx.execute("update issues set status='merged' where id=%s", (w["issue"],))
    # No code run produced the merge, so nobody is credited with it.
    assert _effort(tx, w)["done"] == 0


def test_an_items_cost_includes_every_attempt(tx):
    w = _workspace(tx, "cost")
    for status, cost in (("failed", 4.00), ("failed", 2.50), ("succeeded", 1.25)):
        run = _run(tx, w)
        tx.execute(
            "update runs set status=%s, finished_at=now(), cost_usd=%s where id=%s",
            (status, cost, run),
        )
    cost = tx.execute(
        "select cost_usd from issues where id=%s", (w["issue"],)
    ).fetchone()["cost_usd"]
    # A story that took three tries cost what three tries cost.
    assert float(cost) == pytest.approx(7.75)


def test_agent_effort_daily_is_org_scoped(tx):
    a = _workspace(tx, "org-a")
    b = _workspace(tx, "org-b")
    for w in (a, b):
        run = _run(tx, w)
        tx.execute(
            "update runs set status='succeeded', finished_at=now() where id=%s", (run,)
        )

    rows = tx.execute(
        "select org_id from agent_effort_daily where org_id in (%s, %s)",
        (a["org"], b["org"]),
    ).fetchall()
    assert {r["org_id"] for r in rows} == {a["org"], b["org"]}

    # RLS: the policy is is_org_member, and neither org has members here, so a
    # non-service caller sees nothing. Pinned as the policy's shape rather than
    # by impersonating a JWT, which this fixture has no way to mint.
    policy = tx.execute(
        "select qual from pg_policies where tablename='agent_effort_daily' "
        "and policyname='agent_effort_daily_select'"
    ).fetchone()
    assert policy is not None
    assert "is_org_member" in policy["qual"]


def test_a_run_still_in_flight_has_no_measurement(tx):
    w = _workspace(tx, "in-flight")
    run = _run(tx, w)
    tx.execute("update runs set last_heartbeat_at = now() where id=%s", (run,))
    secs = tx.execute(
        "select work_seconds from runs where id=%s", (run,)
    ).fetchone()["work_seconds"]
    assert secs is None
    assert _effort(tx, w)["runs"] == 0
