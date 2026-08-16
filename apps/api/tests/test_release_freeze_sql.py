"""US-103.5: a release in flight freezes dispatch, proven against Postgres.

A release is pinned to one commit; everything downstream reads that SHA. Work
merged while it is in flight is not in the build being tested but IS on the
default branch, so it silently belongs to the next release while the manager
watches it merge during this one.

The rule lives in `issue_dispatch_refusal` — migration 235's choke point —
which is what makes these assertions meaningful: `dispatch_issue` raises
exactly what this function returns, and `org_issue_dispatch_blocks` hands the
identical string to the Workbench and the issue page. There is no second
implementation that can drift.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every test
rolls back — nothing is left behind.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

# Migration 215's releases_one_in_flight_per_project set, in full.
IN_FLIGHT = [
    "queued",
    "running",
    "notes-ready",
    "deploying",
    "uat-deployed",
    "uat-deploy-failed",
    "uat-signed-off",
    "promoting",
]
# The three ways the freeze ends.
SETTLED = ["released", "cancelled", "rejected"]


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


@pytest.fixture
def ctx(db):
    """Two projects in one org, each with a standalone story — so "scoped to
    the project" is provable rather than assumed."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    org_id = org["id"]

    projects = []
    for n in (1, 2):
        pid = str(uuid.uuid4())
        db.execute(
            "insert into public.projects (id, org_id, name) values (%s, %s, %s)",
            (pid, org_id, f"freeze-test-{n}-{uuid.uuid4().hex[:6]}"),
        )
        iid = str(uuid.uuid4())
        db.execute(
            """
            insert into public.issues (id, org_id, project_id, title, type, status)
            values (%s, %s, %s, 'a standalone story', 'story', 'ready')
            """,
            (iid, org_id, pid),
        )
        projects.append({"project_id": pid, "issue_id": iid})

    yield {"org_id": org_id, "a": projects[0], "b": projects[1]}
    db.rollback()


def _release(db, ctx, project_id, status):
    db.execute(
        """
        insert into public.releases (org_id, project_id, version, status, commit_sha)
        values (%s, %s, %s, %s, %s)
        """,
        (ctx["org_id"], project_id, f"9996.01.01.{uuid.uuid4().hex[:6]}", status, "0" * 40),
    )


def _refusal(db, issue_id, kind):
    return db.execute(
        "select public.issue_dispatch_refusal(%s, %s) as r", (issue_id, kind)
    ).fetchone()["r"]


@pytest.mark.parametrize("status", IN_FLIGHT)
@pytest.mark.parametrize("kind", ["plan", "code"])
def test_every_in_flight_state_freezes_routing(db, ctx, status, kind):
    _release(db, ctx, ctx["a"]["project_id"], status)

    refusal = _refusal(db, ctx["a"]["issue_id"], kind)

    assert refusal is not None, f"{kind} was not frozen while a release is {status}"
    # AC2: the version, where it is, and the three ways out.
    assert "is in flight" in refusal
    assert "written but not" in refusal and "dispatched" in refusal
    assert "released, stopped or rejected" in refusal


@pytest.mark.parametrize("kind", ["breakdown", "elaborate", "draw", "guidelines"])
def test_authoring_kinds_stay_open(db, ctx, kind):
    """The manager's own line: writing stories, bugs and chores is not routing
    them. These kinds never touch the repository."""
    _release(db, ctx, ctx["a"]["project_id"], "running")

    assert _refusal(db, ctx["a"]["issue_id"], kind) is None


@pytest.mark.parametrize("status", SETTLED)
def test_the_freeze_lifts_by_itself(db, ctx, status):
    """AC6: released, stopped or rejected — no manual step, no stale block."""
    _release(db, ctx, ctx["a"]["project_id"], status)

    assert _refusal(db, ctx["a"]["issue_id"], "code") is None


def test_the_freeze_is_scoped_to_the_project(db, ctx):
    """AC7: a release on project A never blocks project B."""
    _release(db, ctx, ctx["a"]["project_id"], "running")

    assert _refusal(db, ctx["a"]["issue_id"], "code") is not None
    assert _refusal(db, ctx["b"]["issue_id"], "code") is None


def test_no_release_at_all_refuses_nothing(db, ctx):
    assert _refusal(db, ctx["a"]["issue_id"], "code") is None
    assert _refusal(db, ctx["a"]["issue_id"], "plan") is None


def test_the_refusal_names_where_the_release_is(db, ctx):
    """The lifecycle phrase tells the manager whether they are waiting on
    themselves — 'on UAT' means go run the test cases."""
    _release(db, ctx, ctx["a"]["project_id"], "uat-deployed")

    assert "on UAT" in _refusal(db, ctx["a"]["issue_id"], "code")


def test_dispatch_issue_raises_the_same_words_the_ui_reads(db, ctx):
    """Migration 235's whole discipline: what the manager reads before
    clicking is what the RPC would have said. A UI-side re-derivation is how
    a button comes to offer what the factory refuses."""
    _release(db, ctx, ctx["a"]["project_id"], "running")
    expected = _refusal(db, ctx["a"]["issue_id"], "code")

    with pytest.raises(psycopg.errors.RaiseException) as e:
        db.execute(
            "select public.dispatch_issue(%s, 'code')", (ctx["a"]["issue_id"],)
        )
    assert expected in str(e.value)
    db.rollback()


def test_the_block_is_reported_as_hard_not_a_pool_hold(db, ctx):
    """A soft hold would create the run and park it — which holds a claim slot
    and reads as work in progress. The point is that nothing is in progress on
    this project until the release lands."""
    _release(db, ctx, ctx["a"]["project_id"], "running")

    row = db.execute(
        "select reason, hard from public.issue_dispatch_block(%s, 'code')",
        (ctx["a"]["issue_id"],),
    ).fetchone()
    assert row is not None
    assert row["hard"] is True
    assert "is in flight" in row["reason"]


def test_the_org_sweep_surfaces_it_for_the_workbench(db, ctx):
    """The Workbench loads every block in one call; if the freeze were absent
    here the page would offer a button the factory refuses."""
    _release(db, ctx, ctx["a"]["project_id"], "running")

    rows = db.execute(
        "select issue_id, reason, hard from public.org_issue_dispatch_blocks(%s)",
        (ctx["org_id"],),
    ).fetchall()
    mine = [r for r in rows if str(r["issue_id"]) == ctx["a"]["issue_id"]]
    assert mine and "is in flight" in mine[0]["reason"] and mine[0]["hard"] is True
