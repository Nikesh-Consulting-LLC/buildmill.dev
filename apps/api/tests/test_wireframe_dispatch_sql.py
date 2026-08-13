"""US-48.2: dispatch_wireframe, against the real database.

The guarantees here are the ones a unit test cannot make: that the issue's
status really does not move, that the refusals are refusals, that the hold
exemption reaches `list_worker_pool` and not just `run_hold_reason` in
isolation, and that the capability gate resolves to a grant a manager can
actually give.

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


def _story(db, project, status: str = "ready", parent=None):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria,
           status, parent_id)
        values (%(id)s, %(org_id)s, %(project_id)s, 'story', %(title)s,
                'the story body', %(ac)s::jsonb, %(status)s, %(parent)s)
        """,
        {
            "id": issue_id,
            "org_id": project["org_id"],
            "project_id": project["id"],
            "title": f"sql-test wireframe {issue_id}",
            "ac": json.dumps(["it works"]),
            "status": status,
            "parent": parent,
        },
    )
    db.commit()
    return issue_id


def _feature(db, project):
    issue_id = uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%(id)s, %(org_id)s, %(project_id)s, 'feature', %(title)s,
                'body', '[]'::jsonb, 'ready')
        """,
        {
            "id": issue_id,
            "org_id": project["org_id"],
            "project_id": project["id"],
            "title": f"sql-test wireframe feature {issue_id}",
        },
    )
    db.commit()
    return issue_id


def _cleanup(db, *issue_ids):
    db.rollback()
    for issue_id in issue_ids:
        db.execute("delete from public.runs where issue_id = %s", (issue_id,))
        db.execute("delete from public.issue_events where issue_id = %s", (issue_id,))
        db.execute("delete from public.artifacts where issue_id = %s", (issue_id,))
    for issue_id in issue_ids:
        db.execute("delete from public.issues where parent_id = %s", (issue_id,))
        db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_dispatch_queues_a_wireframe_run_and_never_moves_the_story(db, project):
    """The status guarantee is the whole reason this is not a plan run: a
    wireframe run must sit outside the issue lifecycle exactly as prd,
    breakdown and elaborate runs do."""
    issue_id = _story(db, project, status="planned")
    try:
        run_id = db.execute(
            "select public.dispatch_wireframe(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        run = db.execute(
            "select kind, status, prev_issue_status, input_context "
            "from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert run["kind"] == "wireframe"
        assert run["status"] == "queued"
        assert run["prev_issue_status"] == "planned"

        after = db.execute(
            "select status from public.issues where id = %s", (issue_id,)
        ).fetchone()
        assert after["status"] == "planned", "dispatch must not move the story"

        ctx = run["input_context"]
        assert ctx["run_kind"] == "wireframe"
        assert ctx["story"] == "the story body"
        assert ctx["acceptance_criteria"] == ["it works"]

        events = db.execute(
            "select type from public.issue_events where issue_id = %s", (issue_id,)
        ).fetchall()
        assert any(e["type"] == "wireframe-dispatched" for e in events)
    finally:
        _cleanup(db, issue_id)


def test_redo_carries_the_managers_comment(db, project):
    issue_id = _story(db, project)
    try:
        run_id = db.execute(
            "select public.dispatch_wireframe(%s, %s) as id",
            (issue_id, "the filter belongs in the header"),
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        assert ctx["feedback"] == "the filter belongs in the header"
    finally:
        _cleanup(db, issue_id)


def test_a_blank_comment_is_not_recorded_as_feedback(db, project):
    issue_id = _story(db, project)
    try:
        run_id = db.execute(
            "select public.dispatch_wireframe(%s, %s) as id", (issue_id, "   ")
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        assert "feedback" not in ctx
    finally:
        _cleanup(db, issue_id)


def test_a_second_dispatch_is_refused_while_one_is_in_flight(db, project):
    issue_id = _story(db, project)
    try:
        db.execute("select public.dispatch_wireframe(%s)", (issue_id,))
        db.commit()
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.dispatch_wireframe(%s)", (issue_id,))
        assert "already in flight" in str(excinfo.value)
    finally:
        _cleanup(db, issue_id)


def test_a_feature_is_refused_and_pointed_at_the_batch(db, project):
    feature_id = _feature(db, project)
    try:
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.dispatch_wireframe(%s)", (feature_id,))
        assert "drawing its stories" in str(excinfo.value)
    finally:
        _cleanup(db, feature_id)


def test_an_abandoned_story_is_refused(db, project):
    issue_id = _story(db, project)
    try:
        db.execute(
            "update public.issues set abandoned_at = now() where id = %s",
            (issue_id,),
        )
        db.commit()
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.dispatch_wireframe(%s)", (issue_id,))
        assert "abandoned" in str(excinfo.value)
    finally:
        _cleanup(db, issue_id)


def test_a_missing_issue_is_refused(db):
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException) as excinfo:
        db.execute("select public.dispatch_wireframe(%s)", (uuid.uuid4(),))
    assert "not found" in str(excinfo.value)
    db.rollback()


def test_the_previous_wireframe_reaches_the_redo(db, project):
    issue_id = _story(db, project)
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'wireframe', %s, 1, 'approved', 'agent')
            """,
            (
                project["org_id"],
                issue_id,
                json.dumps({"screens": [{"name": "Queue", "route": "/issues"}]}),
            ),
        )
        db.commit()
        run_id = db.execute(
            "select public.dispatch_wireframe(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        assert "previous_wireframe" in ctx
        assert "Queue" in ctx["previous_wireframe"]
        event = db.execute(
            "select payload from public.issue_events "
            "where issue_id = %s and type = 'wireframe-dispatched'",
            (issue_id,),
        ).fetchone()
        assert event["payload"]["redo"] is True
    finally:
        _cleanup(db, issue_id)


def test_sibling_screens_travel_but_sibling_bodies_do_not(db, project):
    """A feature's stories are slices of one surface, and two of them
    proposing two different filter bars is the failure this context exists to
    prevent. Only the names and routes travel — the whole declaration would be
    most of the context budget by the fifth story."""
    feature_id = _feature(db, project)
    sibling = _story(db, project, parent=feature_id)
    subject = _story(db, project, parent=feature_id)
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'wireframe', %s, 1, 'approved', 'agent')
            """,
            (
                project["org_id"],
                sibling,
                json.dumps(
                    {
                        "screens": [
                            {
                                "name": "Sibling screen",
                                "route": "/siblings",
                                "regions": [
                                    {"component": "card", "title": "secret detail"}
                                ],
                            }
                        ]
                    }
                ),
            ),
        )
        db.commit()
        run_id = db.execute(
            "select public.dispatch_wireframe(%s) as id", (subject,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        siblings = ctx.get("sibling_wireframes") or []
        assert siblings, "the drawn sibling should be in context"
        names = [s["name"] for entry in siblings for s in entry["screens"]]
        assert "Sibling screen" in names
        assert "secret detail" not in json.dumps(siblings)
    finally:
        _cleanup(db, subject, sibling, feature_id)


def test_an_undrawn_sibling_is_not_listed(db, project):
    feature_id = _feature(db, project)
    sibling = _story(db, project, parent=feature_id)
    subject = _story(db, project, parent=feature_id)
    try:
        run_id = db.execute(
            "select public.dispatch_wireframe(%s) as id", (subject,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        assert "sibling_wireframes" not in ctx
    finally:
        _cleanup(db, subject, sibling, feature_id)


# ---------------------------------------------------------------------------
# US-48.3: the feature fan-out
# ---------------------------------------------------------------------------


def _draw_batch(db, feature_id):
    row = db.execute(
        "select public.dispatch_wireframe_batch(%s) as result", (feature_id,)
    ).fetchone()["result"]
    db.commit()
    return row


def test_the_batch_queues_one_run_per_story_in_order(db, project):
    feature_id = _feature(db, project)
    first = _story(db, project, parent=feature_id)
    second = _story(db, project, parent=feature_id)
    try:
        result = _draw_batch(db, feature_id)
        assert result["dispatched_count"] == 2
        assert result["skipped_count"] == 0

        runs = db.execute(
            """
            select r.issue_id, r.kind, r.status
            from public.runs r
            where r.issue_id = any(%s) and r.kind = 'wireframe'
            """,
            ([first, second],),
        ).fetchall()
        assert len(runs) == 2
        assert all(r["status"] == "queued" for r in runs)

        # And no child's status moved.
        statuses = db.execute(
            "select status from public.issues where parent_id = %s", (feature_id,)
        ).fetchall()
        assert {s["status"] for s in statuses} == {"ready"}
    finally:
        _cleanup(db, first, second, feature_id)


def test_the_batch_skips_rather_than_fails(db, project):
    """us-20.5's shape: an abandoned child, one already in flight and one
    already drawn are reported with a reason, and the rest still dispatch."""
    feature_id = _feature(db, project)
    abandoned = _story(db, project, parent=feature_id)
    in_flight = _story(db, project, parent=feature_id)
    drawn = _story(db, project, parent=feature_id)
    fresh = _story(db, project, parent=feature_id)
    try:
        db.execute(
            "update public.issues set abandoned_at = now() where id = %s",
            (abandoned,),
        )
        db.execute("select public.dispatch_wireframe(%s)", (in_flight,))
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'wireframe', %s, 1, 'approved', 'agent')
            """,
            (project["org_id"], drawn, json.dumps({"screens": [{"name": "S"}]})),
        )
        db.commit()

        result = _draw_batch(db, feature_id)
        assert result["dispatched_count"] == 1
        reasons = {s["reason"] for s in result["skipped"]}
        assert reasons == {"abandoned", "already-in-flight", "already-drawn"}
        assert result["dispatched"][0]["issue_id"] == str(fresh)
    finally:
        _cleanup(db, abandoned, in_flight, drawn, fresh, feature_id)


def test_a_no_ui_verdict_counts_as_drawn(db, project):
    """The verdict is an answer, not a gap. A batch that redrew it would
    re-ask a question an agent already answered — and charge for it."""
    feature_id = _feature(db, project)
    verdict = _story(db, project, parent=feature_id)
    try:
        db.execute(
            """
            insert into public.artifacts
              (org_id, issue_id, kind, content, version, status, created_by)
            values (%s, %s, 'wireframe', %s, 1, 'approved', 'agent')
            """,
            (
                project["org_id"],
                verdict,
                json.dumps({"no_ui_surface": True, "reason": "a migration"}),
            ),
        )
        db.commit()
        result = _draw_batch(db, feature_id)
        assert result["dispatched_count"] == 0
        assert result["skipped"][0]["reason"] == "already-drawn"
    finally:
        _cleanup(db, verdict, feature_id)


def test_running_the_batch_twice_dispatches_nothing_the_second_time(db, project):
    feature_id = _feature(db, project)
    story = _story(db, project, parent=feature_id)
    try:
        assert _draw_batch(db, feature_id)["dispatched_count"] == 1
        second = _draw_batch(db, feature_id)
        assert second["dispatched_count"] == 0
        assert second["skipped"][0]["reason"] == "already-in-flight"
    finally:
        _cleanup(db, story, feature_id)


def test_a_childless_feature_is_a_no_op_not_an_error(db, project):
    feature_id = _feature(db, project)
    try:
        result = _draw_batch(db, feature_id)
        assert result["dispatched_count"] == 0
        assert result["skipped_count"] == 0
    finally:
        _cleanup(db, feature_id)


def test_the_batch_refuses_a_story(db, project):
    story = _story(db, project)
    try:
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.dispatch_wireframe_batch(%s)", (story,))
        assert "applies to a feature" in str(excinfo.value)
    finally:
        _cleanup(db, story)


def test_the_batch_refuses_an_abandoned_feature(db, project):
    feature_id = _feature(db, project)
    try:
        db.execute(
            "update public.issues set abandoned_at = now() where id = %s",
            (feature_id,),
        )
        db.commit()
        with pytest.raises(psycopg.errors.RaiseException) as excinfo:
            db.execute("select public.dispatch_wireframe_batch(%s)", (feature_id,))
        assert "abandoned" in str(excinfo.value)
    finally:
        _cleanup(db, feature_id)


def test_the_batch_does_not_need_feature_build_mode(db, project):
    """dispatch_feature_batch refuses outside feature/epic mode because it
    batches DELIVERY work, where the mode decides who owns the build. Drawing
    is not delivery — a story-mode project's feature has stories with screens
    like any other, and refusing there would make the fan-out unavailable to
    most projects for no reason anyone could act on."""
    db.rollback()
    mode = db.execute(
        "select build_mode from public.projects where id = %s", (project["id"],)
    ).fetchone()["build_mode"]
    feature_id = _feature(db, project)
    story = _story(db, project, parent=feature_id)
    try:
        db.execute(
            "update public.projects set build_mode = 'story' where id = %s",
            (project["id"],),
        )
        db.commit()
        assert _draw_batch(db, feature_id)["dispatched_count"] == 1
    finally:
        db.execute(
            "update public.projects set build_mode = %s where id = %s",
            (mode, project["id"]),
        )
        db.commit()
        _cleanup(db, story, feature_id)


# ---------------------------------------------------------------------------
# The gates
# ---------------------------------------------------------------------------


def test_the_capability_gate_takes_wireframe_first_class(db):
    """US-55.1 retired migration 178's kind→capability squeeze: a
    worker_capabilities row means project ACCESS, and `wireframe` gates on
    the agent's own enabled_kinds like every other kind — the mapping
    function is gone from the live schema."""
    db.rollback()
    gone = db.execute(
        "select count(*) as n from pg_proc p "
        "join pg_namespace ns on ns.oid = p.pronamespace "
        "where ns.nspname = 'public' and p.proname = 'run_kind_capability'"
    ).fetchone()
    assert gone["n"] == 0
    src = db.execute(
        "select prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = 'worker_has_grant'"
    ).fetchone()["prosrc"]
    assert "enabled_kinds" in src


def _prosrc(db, name):
    return db.execute(
        "select prosrc from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = %s",
        (name,),
    ).fetchone()["prosrc"]


def test_the_hold_exemption_is_in_the_live_function(db):
    db.rollback()
    # us-74.5 moved the rule bodies out of run_hold_reason (which now only
    # looks the run up) and into issue_hold_reason, so the same question can
    # be asked before a run exists. The exemptions must have travelled with
    # them: 185 extends 176's rule rather than rebuilding the function,
    # precisely so this cannot be dropped.
    src = _prosrc(db, "issue_hold_reason")
    assert "not in ('elaborate', 'wireframe')" in src
    assert "kind = 'guidelines'" in src
    # ...and the pool must still reach them: run_hold_reason is what the
    # claim path calls, so the delegation is the other half of the guarantee.
    assert "issue_hold_reason" in _prosrc(db, "run_hold_reason")


def test_a_wireframe_run_is_not_held_by_its_draft_siblings(db, project):
    """The us-15.3 rule holds any run whose sibling is still `draft` — which
    is EVERY story in a fresh breakdown set, and exactly the state a wireframe
    run is dispatched into. Without the exemption it would be held by the
    condition it exists to work inside.

    Asserted as a DIFFERENCE against a plan run on the same story, not as
    "not held at all": a wireframe is delivery work for its feature and stays
    subject to every ordering rule (us-20.5's one-in-flight, the earlier
    feature). Asserting `is None` would pass or fail on whatever else the
    database happens to have queued, which is what the first version of this
    test did."""
    feature_id = _feature(db, project)
    draft_sibling = _story(db, project, status="draft", parent=feature_id)
    subject = _story(db, project, status="draft", parent=feature_id)
    try:
        wireframe_run = db.execute(
            "select public.dispatch_wireframe(%s) as id", (subject,)
        ).fetchone()["id"]
        plan_run = db.execute(
            """
            insert into public.runs
              (org_id, project_id, issue_id, provider, status, kind,
               input_context)
            values (%s, %s, %s, 'claude', 'queued', 'plan', '{}'::jsonb)
            returning id
            """,
            (project["org_id"], project["id"], subject),
        ).fetchone()["id"]
        db.commit()

        held_wireframe = db.execute(
            "select public.run_hold_reason(%s) as reason", (wireframe_run,)
        ).fetchone()["reason"]
        held_plan = db.execute(
            "select public.run_hold_reason(%s) as reason", (plan_run,)
        ).fetchone()["reason"]

        assert held_plan and "still being curated" in held_plan, (
            "the us-15.3 draft-sibling rule should hold a plan run here — "
            f"got {held_plan!r}"
        )
        assert not (held_wireframe and "still being curated" in held_wireframe), (
            f"the draft-sibling rule reached the wireframe run: {held_wireframe!r}"
        )
    finally:
        _cleanup(db, subject, draft_sibling, feature_id)


def test_the_seeded_instruction_tells_the_agent_what_to_declare(db):
    db.rollback()
    text = db.execute(
        "select public.baked_worker_instruction('wireframe') as t"
    ).fetchone()["t"]
    assert text
    # The three things that make the output usable rather than decorative.
    assert "submit_wireframe" in text
    assert "no_ui_surface" in text
    assert "status-badge" in text and "empty-state" in text
    # It must not ask for HTML: the whole kit depends on a declaration.
    assert "no HTML" in text


def test_every_other_kinds_instruction_is_untouched(db):
    """185's surgery replaces one `else null` tail. If it ever replaces the
    function wholesale, this is what notices."""
    db.rollback()
    for kind in ("plan", "code", "prd", "breakdown", "elaborate", "guidelines"):
        text = db.execute(
            "select public.baked_worker_instruction(%s) as t", (kind,)
        ).fetchone()["t"]
        assert text, f"{kind} lost its instruction"
        assert "submit_wireframe" not in text, f"{kind} picked up wireframe text"
