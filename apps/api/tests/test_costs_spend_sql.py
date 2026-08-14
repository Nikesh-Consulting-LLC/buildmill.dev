"""Phase 95 (us-95.3 AC4/AC5, us-95.2 AC6): the reconciliation rules, against
a live database.

The seeded fixture is the one the story names: a normal run attributed to a
work item, a batch run (a run with no single item), and a session call (usage
with no run at all). The claims pinned here:

  * every grouping — infrastructure or work-shaped — sums to the same total;
    the work-shaped ones may not show less money than the project view;
  * the unattributable money is ONE named bucket holding exactly the session
    and batch dollars, never dropped and never pro-rated;
  * the work-item grain agrees with the us-91.14 `issues.cost_usd` rollup for
    a fully-metered item (they read different ledgers; where they differ it
    must be explained by the named exclusions, and here there are none);
  * the trend's series sums to the breakdown's total for the same window;
  * none of it leaks across orgs.

These run the REAL `db.spend_breakdown` / `db.spend_trend` query text —
`db._connect` is pointed at the test transaction — so what is pinned is the
shipped SQL, not a re-implementation of it.

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

from app import db as app_db


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


class _TxConn:
    """Hands db.spend_* the test transaction while swallowing the context
    manager exit — the real `_connect` would commit/close, which would break
    the rollback that keeps these tests traceless."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, q, p=None):
        return self._conn.execute(q, p)

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False


@pytest.fixture()
def tx(db, monkeypatch):
    with db.transaction(force_rollback=True):
        monkeypatch.setattr(app_db, "_connect", lambda s: _TxConn(db))
        yield db


def _fixture(tx) -> dict:
    """The us-95.3 AC4 cast: a story with a normal run and two metered calls
    ($1.00 + $0.50), a batch run with one call ($2.00) that no single item can
    claim, and a session call with no run at all ($4.00). Total: $7.50, of
    which $6.00 is honestly unattributable."""
    org = tx.execute(
        "insert into organizations (name) values (%s) returning id",
        (f"costs-{uuid.uuid4().hex[:8]}",),
    ).fetchone()["id"]
    project = tx.execute(
        "insert into projects (org_id, name, repo_full_name) "
        "values (%s, 'Alpha', 'acme/repo') returning id",
        (org,),
    ).fetchone()["id"]
    epic = tx.execute(
        "insert into epics (org_id, project_id, number, title) "
        "values (%s, %s, 4, 'The costs epic') returning id",
        (org, project),
    ).fetchone()["id"]
    story = tx.execute(
        "insert into issues (org_id, project_id, epic_id, title, type, status, item_no) "
        "values (%s, %s, %s, 'The story', 'story', 'queued', 2) returning id",
        (org, project, epic),
    ).fetchone()["id"]
    worker = tx.execute(
        "insert into workers (org_id, name, type, status, token_hash, token_last4) "
        "values (%s, 'agent-1', 'autonomous', 'active', %s, '1234') returning id",
        (org, uuid.uuid4().hex),
    ).fetchone()["id"]
    run_story = tx.execute(
        """
        insert into runs (org_id, project_id, issue_id, kind, provider, status,
                          worker_id, input_context, billing, claimed_at)
        values (%s, %s, %s, 'code', 'claude', 'running', %s, '{}'::jsonb,
                'metered', now() - interval '1 hour')
        returning id
        """,
        (org, project, story, worker),
    ).fetchone()["id"]
    run_batch = tx.execute(
        """
        insert into runs (org_id, project_id, issue_id, kind, provider, status,
                          worker_id, input_context, billing, claimed_at)
        values (%s, %s, null, 'code', 'claude', 'running', %s, '{}'::jsonb,
                'metered', now() - interval '1 hour')
        returning id
        """,
        (org, project, worker),
    ).fetchone()["id"]

    def usage(run_id, cost, tin, tout):
        tx.execute(
            """
            insert into llm_usage (org_id, project_id, run_id, worker_id,
                                   model, tokens_in, tokens_out, cost_usd, parsed)
            values (%s, %s, %s, %s, 'test-model', %s, %s, %s, true)
            """,
            (org, project, run_id, worker, tin, tout, cost),
        )

    usage(run_story, 1.00, 1000, 100)
    usage(run_story, 0.50, 500, 50)
    usage(run_batch, 2.00, 2000, 200)
    usage(None, 4.00, 4000, 400)  # the session call — no run behind it

    return {
        "org": str(org),
        "project": str(project),
        "epic": str(epic),
        "story": str(story),
        "run_story": str(run_story),
    }


TOTAL = 7.50
UNATTRIBUTABLE = 6.00


@pytest.mark.parametrize("dimension", ["project", "agent", "type", "epic", "item"])
def test_every_grouping_sums_to_the_same_total(tx, dimension):
    """us-95.3 AC4: if grouping by type shows less money than grouping by
    project, rows were dropped somewhere."""
    f = _fixture(tx)
    out = app_db.spend_breakdown(object(), f["org"], group_by=dimension)
    assert out["totals"]["cost_usd"] == pytest.approx(TOTAL), dimension


@pytest.mark.parametrize("dimension", ["type", "epic", "item"])
def test_the_unattributable_bucket_holds_exactly_the_unwalkable_money(tx, dimension):
    f = _fixture(tx)
    out = app_db.spend_breakdown(object(), f["org"], group_by=dimension)
    bucket = [r for r in out["rows"] if r["key"] is None]
    assert len(bucket) == 1, "one named bucket, not several and not zero"
    assert bucket[0]["label"] == "Not attributable to a work item"
    assert bucket[0]["cost_usd"] == pytest.approx(UNATTRIBUTABLE)
    assert bucket[0]["calls"] == 2  # the batch call and the session call


def test_epic_and_item_labels_read_as_work(tx):
    f = _fixture(tx)
    by_epic = app_db.spend_breakdown(object(), f["org"], group_by="epic")
    labels = {r["key"]: r["label"] for r in by_epic["rows"]}
    assert labels[f["epic"]] == "E4 — The costs epic · Alpha"
    by_item = app_db.spend_breakdown(object(), f["org"], group_by="item")
    labels = {r["key"]: r["label"] for r in by_item["rows"]}
    assert labels[f["story"]] == "US-4.2 — The story"


def test_the_item_type_filter_narrows_to_attributed_money_only(tx):
    """us-95.4 AC2 by way of us-95.3: a bug filter cannot vouch for money it
    cannot attribute, so the unattributable rows drop out of a filtered view
    — deliberately, and the totals say so."""
    f = _fixture(tx)
    out = app_db.spend_breakdown(object(), f["org"], item_type="story")
    assert out["totals"]["cost_usd"] == pytest.approx(1.50)
    out = app_db.spend_breakdown(object(), f["org"], item_type="bug")
    assert out["totals"]["cost_usd"] is None  # no bug spent anything


def test_the_item_grain_agrees_with_the_issue_rollup(tx):
    """us-95.3 AC5: the breakdown reads `llm_usage`; the Work Items hub reads
    the us-91.14 `issues.cost_usd` rollup, maintained from `runs.cost_usd` at
    run-terminal. For a fully-metered item the two figures MUST agree — here
    the run's rollup is written the way migration 159's recompute would, the
    run closes, and both ledgers answer $1.50."""
    f = _fixture(tx)
    tx.execute(
        """
        update runs set cost_usd = (
            select sum(cost_usd) from llm_usage where run_id = runs.id
        ), tokens_in = 1500, tokens_out = 150
        where id = %s
        """,
        (f["run_story"],),
    )
    tx.execute(
        "update runs set status='succeeded', finished_at=now() where id=%s",
        (f["run_story"],),
    )
    rolled = tx.execute(
        "select cost_usd from issues where id=%s", (f["story"],)
    ).fetchone()["cost_usd"]
    out = app_db.spend_breakdown(object(), f["org"], group_by="item")
    breakdown_cost = {r["key"]: r["cost_usd"] for r in out["rows"]}[f["story"]]
    assert float(rolled) == pytest.approx(breakdown_cost) == pytest.approx(1.50)


def test_the_trend_sums_to_the_breakdowns_total(tx):
    """us-95.2 AC6: two dollar figures for the same window that disagree is
    worse than none."""
    f = _fixture(tx)
    breakdown = app_db.spend_breakdown(object(), f["org"], days=30)
    trend = app_db.spend_trend(object(), f["org"], days=30)
    assert trend["total_cost_usd"] == pytest.approx(breakdown["totals"]["cost_usd"])
    assert sum(p["cost_usd"] for p in trend["series"]) == pytest.approx(TOTAL)
    # Nothing was metered before today's fixture rows, so the previous window
    # must say "nothing to compare against", not zero.
    assert trend["previous_cost_usd"] is None


def test_none_of_it_leaks_across_orgs(tx):
    f = _fixture(tx)
    other = tx.execute(
        "insert into organizations (name) values (%s) returning id",
        (f"costs-other-{uuid.uuid4().hex[:8]}",),
    ).fetchone()["id"]
    out = app_db.spend_breakdown(object(), str(other), group_by="type")
    assert out["rows"] == []
    trend = app_db.spend_trend(object(), str(other), days=30)
    assert trend["total_cost_usd"] is None
    assert f["org"] != str(other)
