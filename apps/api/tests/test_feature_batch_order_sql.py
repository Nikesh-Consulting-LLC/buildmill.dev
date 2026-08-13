"""US-41.1: batch dispatch works in every build mode, in a total order.

Live SQL coverage against DATABASE_URL (apps/api/.env); skips if unreachable.
Every test rolls back — nothing here commits.

The ordering half exists because `created_at` is not a tiebreaker: a breakdown
inserts every sibling in ONE statement, so all of them share a timestamp to the
microsecond. Any story with a null `sub_no` used to sort arbitrarily, and the
queue drains serially, so dispatch order is execution order.
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


def _feature_with_stories(db, ctx, stories, status="draft"):
    """A feature and its children, all inserted in ONE statement so they share
    a `created_at` exactly as a real breakdown does. `stories` is a list of
    (title, sub_no) — sub_no may be None."""
    feature_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues (id, org_id, project_id, type, title, status)
        values (%s, %s, %s, 'feature', 'Batch order fixture', 'ready')
        """,
        (feature_id, ctx["org_id"], ctx["project_id"]),
    )
    ids = [uuid.uuid4() for _ in stories]
    # One statement, so every sibling shares a `created_at` — the condition
    # that makes it useless as a tiebreaker.
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, status, parent_id)
        select x.id, %s, %s, 'story', x.title, %s, %s
        from unnest(%s::uuid[], %s::text[]) as x(id, title)
        """,
        (
            ctx["org_id"],
            ctx["project_id"],
            status,
            feature_id,
            ids,
            [s[0] for s in stories],
        ),
    )
    # `sub_no` is trigger-assigned in insertion order, so the fixture sets the
    # numbers it wants afterwards — including NULL, which is what a story
    # added to a feature by hand can look like.
    db.execute(
        """
        update public.issues i
        set sub_no = x.sub_no
        from unnest(%s::uuid[], %s::int[]) as x(id, sub_no)
        where i.id = x.id
        """,
        (ids, [s[1] for s in stories]),
    )
    return feature_id, ids


def _order(db, feature_id):
    """The clause dispatch_feature_batch now uses, in both loops."""
    rows = db.execute(
        """
        select title from public.issues
        where parent_id = %s and abandoned_at is null
        order by sub_no nulls last, item_no nulls last, id
        """,
        (feature_id,),
    ).fetchall()
    return [r["title"] for r in rows]


def test_siblings_really_do_share_a_created_at(db, ctx):
    """The premise the ordering fix rests on."""
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2), ("c", 3)]
    )
    row = db.execute(
        "select count(distinct created_at) as n from public.issues where parent_id = %s",
        (feature_id,),
    ).fetchone()
    assert row["n"] == 1
    db.rollback()


def test_order_follows_sub_no(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("third", 3), ("first", 1), ("second", 2)]
    )
    assert _order(db, feature_id) == ["first", "second", "third"]
    db.rollback()


def test_a_story_without_a_sub_no_goes_last(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("added by hand", None), ("first", 1), ("second", 2)]
    )
    assert _order(db, feature_id) == ["first", "second", "added by hand"]
    db.rollback()


def test_order_is_stable_across_repeated_calls(db, ctx):
    """Two stories with no sub_no share every sort key but `id`. Without the
    id tiebreaker Postgres may return them in either order."""
    feature_id, _ = _feature_with_stories(
        db, ctx, [("x", None), ("y", None), ("first", 1)]
    )
    runs = [_order(db, feature_id) for _ in range(5)]
    assert all(r == runs[0] for r in runs), runs
    assert runs[0][0] == "first"
    db.rollback()


def test_phase_reports_same_stage_when_they_agree(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    payload = db.execute(
        "select public.feature_dispatch_phase(%s) as p", (feature_id,)
    ).fetchone()["p"]
    assert payload["same_stage"] is True
    assert payload["common_stage"] == "draft"
    assert payload["children"] == 2
    db.rollback()


def test_phase_reports_mixed_stages(db, ctx):
    feature_id, ids = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    db.execute(
        "update public.issues set status = 'ready' where id = %s", (ids[0],)
    )
    payload = db.execute(
        "select public.feature_dispatch_phase(%s) as p", (feature_id,)
    ).fetchone()["p"]
    assert payload["same_stage"] is False
    assert payload["common_stage"] is None
    db.rollback()


def test_phase_reports_the_build_mode(db, ctx):
    """The confirm labels itself from this rather than guessing."""
    feature_id, _ = _feature_with_stories(db, ctx, [("a", 1)])
    payload = db.execute(
        "select public.feature_dispatch_phase(%s) as p", (feature_id,)
    ).fetchone()["p"]
    assert payload["build_mode"] in ("story", "feature", "epic")
    db.rollback()


def test_story_mode_batch_is_no_longer_refused(db, ctx):
    """US-41.1's headline: batching used to raise for any non-feature/epic
    project, which left the default mode clicking one story at a time."""
    db.execute(
        "update public.projects set build_mode = 'story' where id = %s",
        (ctx["project_id"],),
    )
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="ready"
    )
    result = db.execute(
        "select public.dispatch_feature_batch(%s) as r", (feature_id,)
    ).fetchone()["r"]
    assert result["phase"] == "plan"
    assert result["story_count"] == 2
    db.rollback()


def test_story_mode_plan_batch_dispatches_in_sub_no_order(db, ctx):
    db.execute(
        "update public.projects set build_mode = 'story' where id = %s",
        (ctx["project_id"],),
    )
    feature_id, _ = _feature_with_stories(
        db, ctx, [("third", 3), ("first", 1), ("second", 2)], status="ready"
    )
    result = db.execute(
        "select public.dispatch_feature_batch(%s) as r", (feature_id,)
    ).fetchone()["r"]
    titles = [
        db.execute(
            "select title from public.issues where id = %s",
            (d["issue_id"],),
        ).fetchone()["title"]
        for d in result["dispatched"]
    ]
    assert titles == ["first", "second", "third"]
    db.rollback()


# --------------------------------------------------- US-41.2: bulk curation


def _statuses(db, feature_id):
    return sorted(
        r["status"]
        for r in db.execute(
            "select status from public.issues where parent_id = %s and abandoned_at is null",
            (feature_id,),
        ).fetchall()
    )


def test_curate_moves_every_draft_to_ready(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2), ("c", 3)], status="draft"
    )
    moved = db.execute(
        "select public.curate_feature_stories(%s) as n", (feature_id,)
    ).fetchone()["n"]
    assert moved == 3
    assert _statuses(db, feature_id) == ["ready", "ready", "ready"]
    db.rollback()


def test_curate_leaves_non_draft_statuses_alone(db, ctx):
    feature_id, ids = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2), ("c", 3)], status="draft"
    )
    db.execute(
        "update public.issues set status = 'planned' where id = %s", (ids[1],)
    )
    moved = db.execute(
        "select public.curate_feature_stories(%s) as n", (feature_id,)
    ).fetchone()["n"]
    assert moved == 2
    assert _statuses(db, feature_id) == ["planned", "ready", "ready"]
    db.rollback()


def test_curate_is_idempotent(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    db.execute("select public.curate_feature_stories(%s)", (feature_id,))
    again = db.execute(
        "select public.curate_feature_stories(%s) as n", (feature_id,)
    ).fetchone()["n"]
    assert again == 0
    db.rollback()


def test_curate_skips_an_abandoned_story(db, ctx):
    feature_id, ids = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    db.execute(
        "update public.issues set abandoned_at = now() where id = %s", (ids[0],)
    )
    moved = db.execute(
        "select public.curate_feature_stories(%s) as n", (feature_id,)
    ).fetchone()["n"]
    assert moved == 1
    still_draft = db.execute(
        "select status from public.issues where id = %s", (ids[0],)
    ).fetchone()["status"]
    assert still_draft == "draft"
    db.rollback()


def test_curate_records_an_event_per_story(db, ctx):
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    db.execute("select public.curate_feature_stories(%s)", (feature_id,))
    n = db.execute(
        """
        select count(*) as n from public.issue_events
        where type = 'curated'
          and issue_id in (select id from public.issues where parent_id = %s)
        """,
        (feature_id,),
    ).fetchone()["n"]
    assert n == 2
    db.rollback()


def test_curate_refuses_a_non_feature(db, ctx):
    feature_id, ids = _feature_with_stories(db, ctx, [("a", 1)], status="draft")
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select public.curate_feature_stories(%s)", (ids[0],))
    db.rollback()


def test_curated_feature_then_offers_a_plan_batch(db, ctx):
    """The two halves meet: curation unblocks us-41.1's bulk dispatch."""
    feature_id, _ = _feature_with_stories(
        db, ctx, [("a", 1), ("b", 2)], status="draft"
    )
    db.execute("select public.curate_feature_stories(%s)", (feature_id,))
    payload = db.execute(
        "select public.feature_dispatch_phase(%s) as p", (feature_id,)
    ).fetchone()["p"]
    assert payload["phase"] == "plan"
    assert payload["same_stage"] is True
    assert payload["common_stage"] == "ready"
    db.rollback()
