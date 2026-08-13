"""US-27.11: a failed build goes back to building, not planning.

`dispatch_feature_batch` recognised the code phase only for children in
`planned` or `needs-fixes`. A feature whose code run had just failed left its
stories `failed`, matching neither — so re-dispatching it silently queued six
PLAN runs against stories that each already held an approved plan and an
approved test plan (2026-07-26).

The rule is: the approved plan decides the phase, not the status. These tests
walk all four combinations of status x approved-plan, plus the refusal that
makes the incident's exact shape impossible to hit silently.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every test
runs inside a transaction that is rolled back — nothing is left behind, which
matters because that URL may point at a live project.
"""

from __future__ import annotations

import os
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
def feature(db):
    """A throwaway feature with no children, rolled back after each test."""
    db.rollback()
    project = db.execute(
        "select id as project_id, org_id from public.projects limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project to hang a test feature off")
    row = db.execute(
        "insert into public.issues (org_id, project_id, type, title, status)"
        " values (%s, %s, 'feature', 'phase-inference-test', 'ready')"
        " returning id",
        (project["org_id"], project["project_id"]),
    ).fetchone()
    yield {"id": row["id"], **project}
    db.rollback()


def _story(db, feature, status: str, planned: bool):
    row = db.execute(
        "insert into public.issues"
        " (org_id, project_id, type, title, status, parent_id)"
        " values (%s, %s, 'story', %s, %s, %s) returning id",
        (
            feature["org_id"],
            feature["project_id"],
            f"story-{status}-{planned}",
            status,
            feature["id"],
        ),
    ).fetchone()
    if planned:
        db.execute(
            "insert into public.artifacts"
            " (org_id, issue_id, kind, content, version, status, created_by)"
            " values (%s, %s, 'plan', '# plan', 1, 'approved', 'agent')",
            (feature["org_id"], row["id"]),
        )
    return row["id"]


def _phase(db, feature):
    return db.execute(
        "select public.feature_dispatch_phase(%s) as p", (feature["id"],)
    ).fetchone()["p"]


def test_failed_with_an_approved_plan_builds(db, feature):
    """The 2026-07-26 case: this used to route to planning."""
    _story(db, feature, "failed", planned=True)
    assert _phase(db, feature)["phase"] == "code"


def test_failed_with_no_plan_still_plans(db, feature):
    """A story that failed BEFORE it was ever planned has no approved plan —
    the guard decides, not the status."""
    _story(db, feature, "failed", planned=False)
    assert _phase(db, feature)["phase"] == "plan"


def test_planned_with_an_approved_plan_builds(db, feature):
    _story(db, feature, "planned", planned=True)
    assert _phase(db, feature)["phase"] == "code"


def test_a_mixed_feature_builds_every_story_that_can_be_built(db, feature):
    _story(db, feature, "planned", planned=True)
    _story(db, feature, "failed", planned=True)
    out = _phase(db, feature)
    assert out["phase"] == "code"
    assert out["buildable"] == 2


def test_one_unplanned_story_blocks_the_build_rather_than_replanning(
    db, feature
):
    """Dispatching a subset would leave a run held forever by a story that is
    not in it — us-15.3's deadlock. The manager plans the straggler."""
    _story(db, feature, "failed", planned=True)
    _story(db, feature, "draft", planned=False)
    assert _phase(db, feature)["phase"] == "blocked"


def test_dispatch_refuses_to_plan_stories_that_all_hold_approved_plans(
    db, feature
):
    """Belt and braces. This combination has no legitimate reading — it is the
    signature of the bug, and it must be impossible to hit silently even if the
    inference is wrong again."""
    for _ in range(2):
        sid = _story(db, feature, "planned", planned=True)
        db.execute(
            "update public.issues set status = 'merged' where id = %s", (sid,)
        )
    assert _phase(db, feature)["phase"] == "blocked"
    with pytest.raises(psycopg.errors.RaiseException) as e:
        db.execute(
            "select public.dispatch_feature_batch(%s)", (feature["id"],)
        )
    assert "refusing to plan" in str(e.value)
