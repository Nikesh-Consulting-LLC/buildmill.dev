"""US-17.2 / US-86.1: run_hold_reason centralizes pool eligibility.

Migration 247 rebuilt the rules around two project switches and one law:

  * follow_build_order (switch 1) — items go in Epic → Feature → Story order;
    off frees the ORDER only. Gates the earlier-feature hold and the trouble
    pause.
  * route_feature_as_one (switch 2) — the feature is the routing unit. Gates
    the siblings-plan-approved-before-code hold, and widens the law's unit.
  * THE LAW (no checkbox): one unit in progress per project, start to merge.
    Any other unit sitting in planning/plan-review/planned/running/in-review/
    needs-fixes holds everything, softly. 'failed'/'ready'/'draft'/'queued'
    do not count as in progress.

build_mode and sequential_only survive only as trigger-maintained mirrors of
the switches; the epic-mode phase gates and the serial-drain rule (c) are gone.

Runs against DATABASE_URL (apps/api/.env). Skips if unreachable.
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
def scenario(db):
    """An isolated project (switch 1 on, switch 2 off) with epic E1, features
    F1(<F2), F1's stories S1(plan approved) and S2(no plan), F2's story S3,
    and a queued code run on S1 and S3. Stories start 'ready' — under the law
    'planned' counts as in progress and a 'planned' baseline would hold every
    other unit before a test arranged anything. Everything torn down after."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org")
    org_id = org["id"]
    ids = {k: uuid.uuid4() for k in ("proj", "f1", "f2", "s1", "s2", "s3", "r1", "r3")}
    db.execute(
        "insert into public.projects (id, org_id, name, repo_full_name, slug, "
        "follow_build_order, route_feature_as_one) "
        "values (%s,%s,'hr-test','x/y',%s,true,false)",
        (ids["proj"], org_id, f"hr-{uuid.uuid4().hex[:10]}"),
    )
    epic = db.execute(
        "select id from public.epics where project_id=%s order by number limit 1",
        (ids["proj"],),
    ).fetchone()
    epic_id = epic["id"]
    db.execute(
        "insert into public.issues (id,org_id,project_id,epic_id,type,title,body,"
        "acceptance_criteria,status,item_no) values "
        "(%s,%s,%s,%s,'feature','F1','b','[]'::jsonb,'ready',1),"
        "(%s,%s,%s,%s,'feature','F2','b','[]'::jsonb,'ready',2)",
        (ids["f1"], org_id, ids["proj"], epic_id, ids["f2"], org_id, ids["proj"], epic_id),
    )
    db.execute(
        "insert into public.issues (id,org_id,project_id,epic_id,parent_id,type,title,"
        "body,acceptance_criteria,status,item_no,sub_no) values "
        "(%s,%s,%s,%s,%s,'story','S1','b','[]'::jsonb,'ready',1,1),"
        "(%s,%s,%s,%s,%s,'story','S2','b','[]'::jsonb,'ready',1,2),"
        "(%s,%s,%s,%s,%s,'story','S3','b','[]'::jsonb,'ready',2,1)",
        (
            ids["s1"], org_id, ids["proj"], epic_id, ids["f1"],
            ids["s2"], org_id, ids["proj"], epic_id, ids["f1"],
            ids["s3"], org_id, ids["proj"], epic_id, ids["f2"],
        ),
    )
    for s in ("s1", "s3"):
        db.execute(
            "insert into public.artifacts (org_id,issue_id,kind,content,version,status,created_by) "
            "values (%s,%s,'plan','#p',1,'approved','agent')",
            (org_id, ids[s]),
        )  # S1 and S3 have approved plans; S2 does not
    db.execute(
        "insert into public.runs (id,org_id,issue_id,project_id,provider,status,kind,input_context) "
        "values (%s,%s,%s,%s,'claude','queued','code','{}'::jsonb),"
        "(%s,%s,%s,%s,'claude','queued','code','{}'::jsonb)",
        (ids["r1"], org_id, ids["s1"], ids["proj"], ids["r3"], org_id, ids["s3"], ids["proj"]),
    )
    db.commit()
    yield {"db": db, **ids}
    db.rollback()
    # guard_issue_removal refuses to delete a queued/running issue, and some
    # tests park one there deliberately — stand them back down first.
    db.execute("update public.issues set status='ready' where project_id=%s",
               (ids["proj"],))
    db.execute("delete from public.runs where project_id=%s", (ids["proj"],))
    db.execute("delete from public.artifacts where issue_id in "
               "(select id from public.issues where project_id=%s)", (ids["proj"],))
    db.execute("update public.issues set parent_id=null where project_id=%s", (ids["proj"],))
    db.execute("delete from public.issues where project_id=%s", (ids["proj"],))
    db.execute("delete from public.epics where project_id=%s", (ids["proj"],))
    db.execute("delete from public.projects where id=%s", (ids["proj"],))
    db.commit()


def _reason(db, run_id):
    return db.execute(
        "select public.run_hold_reason(%s) as r", (run_id,)
    ).fetchone()["r"]


def _featone_fully_planned(scenario):
    """switch 2 on with F1 fully plan-approved, so the sibling-plan rule is
    clear and only the law / the kept rules can hold anything."""
    db = scenario["db"]
    db.execute(
        "update public.projects set route_feature_as_one=true where id=%s",
        (scenario["proj"],),
    )
    db.execute(
        "insert into public.artifacts (org_id,issue_id,kind,content,version,status,created_by) "
        "select org_id,%s,'plan','#p',1,'approved','agent' from public.issues where id=%s",
        (scenario["s2"], scenario["s2"]),
    )
    db.commit()


def _run_for(scenario, issue_key, kind="code", status="queued"):
    db = scenario["db"]
    run_id = uuid.uuid4()
    db.execute(
        "insert into public.runs (id,org_id,issue_id,project_id,provider,status,kind,input_context) "
        "select %s,org_id,%s,project_id,'claude',%s,%s,'{}'::jsonb "
        "from public.issues where id=%s",
        (run_id, scenario[issue_key], status, kind, scenario[issue_key]),
    )
    db.commit()
    return run_id


def test_nothing_in_progress_holds_nothing(scenario):
    db = scenario["db"]
    assert _reason(db, scenario["r1"]) is None


def test_the_trigger_mirrors_the_switches_onto_the_legacy_columns(scenario):
    """build_mode/sequential_only are derived, never authored: a stale writer
    (an old UI radio setting 'epic', or re-enabling sequential_only) is
    silently corrected to what the switches say."""
    db = scenario["db"]
    db.execute(
        "update public.projects set build_mode='epic', sequential_only=true "
        "where id=%s", (scenario["proj"],))
    db.commit()
    row = db.execute(
        "select build_mode, sequential_only, follow_build_order, "
        "route_feature_as_one from public.projects where id=%s",
        (scenario["proj"],)).fetchone()
    assert row["route_feature_as_one"] is False
    assert row["build_mode"] == "story"      # derived from switch 2, not 'epic'
    assert row["sequential_only"] is False   # pinned

    db.execute(
        "update public.projects set route_feature_as_one=true where id=%s",
        (scenario["proj"],))
    db.commit()
    row = db.execute(
        "select build_mode, sequential_only from public.projects where id=%s",
        (scenario["proj"],)).fetchone()
    assert row["build_mode"] == "feature"
    assert row["sequential_only"] is False


def test_switch_2_holds_code_until_siblings_planned(scenario):
    db = scenario["db"]
    db.execute("update public.projects set route_feature_as_one=true where id=%s",
               (scenario["proj"],))
    db.commit()
    reason = _reason(db, scenario["r1"])
    assert reason is not None and "plan approval" in reason


def test_switch_1_holds_a_later_feature_until_the_earlier_is_done(scenario):
    db = scenario["db"]
    reason = _reason(db, scenario["r3"])  # S3 in F2, F1 not done
    assert reason is not None and "earlier feature" in reason
    assert "FEAT-" in reason


def test_switch_1_off_releases_the_feature_ordering_hold(scenario):
    db = scenario["db"]
    db.execute("update public.projects set follow_build_order=false where id=%s",
               (scenario["proj"],))
    db.commit()
    assert _reason(db, scenario["r3"]) is None


def test_switch_2_releases_when_every_sibling_planned(scenario):
    db = scenario["db"]
    db.execute("update public.projects set route_feature_as_one=true where id=%s",
               (scenario["proj"],))
    # give S2 a plan → F1 fully planned → S1 code run free (F1 is earliest)
    db.execute(
        "insert into public.artifacts (org_id,issue_id,kind,content,version,status,created_by) "
        "select org_id,%s,'plan','#p',1,'approved','agent' from public.issues where id=%s",
        (scenario["s2"], scenario["s2"]),
    )
    db.commit()
    assert _reason(db, scenario["r1"]) is None


# --- US-86.1: the law — one unit in progress, start to merge ---------------
# (Replaces the epic-mode phase gates: an epic is ordering now, not a routing
# unit, so what holds a run across features is the law itself.)


def test_the_law_holds_a_run_while_another_unit_is_being_built(scenario):
    db = scenario["db"]
    db.execute("update public.issues set status='running' where id=%s",
               (scenario["s3"],))
    db.commit()
    reason = _reason(db, scenario["r1"])
    assert reason is not None and "is being built" in reason


def test_the_law_releases_when_the_unit_merges(scenario):
    db = scenario["db"]
    db.execute("update public.issues set status='running' where id=%s",
               (scenario["s3"],))
    db.commit()
    assert _reason(db, scenario["r1"]) is not None
    db.execute("update public.issues set status='merged' where id=%s",
               (scenario["s3"],))
    db.commit()
    assert _reason(db, scenario["r1"]) is None


def test_the_law_ignores_a_same_unit_sibling_under_switch_2(scenario):
    """Under switch 2 S1 and S2 share F1 as their unit: S2 being built never
    holds S1's own run — while S3's run in F2 is held by it."""
    db = scenario["db"]
    _featone_fully_planned(scenario)
    db.execute("update public.issues set status='running' where id=%s",
               (scenario["s2"],))
    db.commit()
    assert _reason(db, scenario["r1"]) is None
    reason = _reason(db, scenario["r3"])
    assert reason is not None and "is being built" in reason


# --- kept rules: trouble pause (d) and their switch gates -------------------


def test_a_queued_sibling_run_alone_no_longer_holds(scenario):
    """Serial-drain rule (c) is gone, subsumed by the law: only an issue's
    STATUS makes a unit in-progress, and 'ready' with a queued run is not.
    Under 235, S1's queued code run held S2's run in this exact scene."""
    _featone_fully_planned(scenario)
    r2 = _run_for(scenario, "s2")
    assert _reason(scenario["db"], r2) is None


def test_trouble_pauses_every_other_story_in_the_feature(scenario):
    """Kept rule (d), gated on switch 1. Under switch 2 the troubled story
    shares its siblings' unit, so the law is quiet and the pause is what
    holds them."""
    _featone_fully_planned(scenario)
    db = scenario["db"]
    r2 = _run_for(scenario, "s2")
    db.execute(
        "update public.issues set status='needs-fixes' where id=%s", (scenario["s1"],)
    )
    db.commit()
    reason = _reason(db, r2)
    assert reason is not None and reason.startswith("paused: story ")


def test_the_trouble_pause_is_gated_on_switch_1(scenario):
    """'failed' is trouble but NOT in-progress under the law: with switch 1 on
    the sibling is paused by rule (d); with switch 1 off nothing holds it at
    all — the law does not count a failed unit."""
    db = scenario["db"]
    r2 = _run_for(scenario, "s2")
    db.execute(
        "update public.issues set status='failed' where id=%s", (scenario["s1"],)
    )
    db.commit()
    reason = _reason(db, r2)
    assert reason is not None and reason.startswith("paused: story ")
    db.execute("update public.projects set follow_build_order=false where id=%s",
               (scenario["proj"],))
    db.commit()
    assert _reason(db, r2) is None


def test_a_troubled_story_can_always_be_re_dispatched(scenario):
    """The exemption that stops the pause rule deadlocking: two troubled
    stories in one unit hold neither each other nor themselves — the law
    excludes the same unit, and rule (d) exempts a troubled story's own run."""
    _featone_fully_planned(scenario)
    db = scenario["db"]
    db.execute(
        "update public.issues set status='needs-fixes' where id in (%s,%s)",
        (scenario["s1"], scenario["s2"]),
    )
    db.commit()
    r2 = _run_for(scenario, "s2")
    assert _reason(db, scenario["r1"]) is None
    assert _reason(db, r2) is None


def test_a_sent_back_plan_counts_as_trouble(scenario):
    """send_back_plan restores the pre-dispatch status, so the gate decision
    is the only durable record that the story went backwards."""
    db = scenario["db"]
    db.execute(
        "delete from public.artifacts where issue_id=%s and kind='plan'",
        (scenario["s1"],),
    )
    db.execute(
        "insert into public.approvals (org_id,issue_id,gate,decision,actor,comment) "
        "select org_id,%s,'plan','sent-back',null,'redo' from public.issues where id=%s",
        (scenario["s1"], scenario["s1"]),
    )
    db.execute("update public.issues set status='ready' where id=%s", (scenario["s1"],))
    db.commit()
    assert db.execute(
        "select public.issue_in_trouble(%s) as t", (scenario["s1"],)
    ).fetchone()["t"] is True


def test_batch_dispatch_works_with_switch_2_off(scenario):
    """US-41.1, kept: batching is a convenience, not a governance setting.
    The legacy reader dispatch_feature_batch sees build_mode='story' through
    the mirror trigger and must not refuse on it.
    """
    db = scenario["db"]
    try:
        db.execute("select public.dispatch_feature_batch(%s)", (scenario["f1"],))
    except psycopg.errors.RaiseException as e:
        # It may still refuse for a PHASE reason — this fixture leaves one
        # story unplanned, and us-27.11's "not ready to build" guard is
        # deliberately untouched. What it may never say again is "build mode".
        assert "build mode" not in str(e), str(e)
    db.rollback()


def test_batch_dispatch_skips_a_non_dispatchable_child(scenario):
    """US-22.9, kept: under switch 2 the code phase is ONE run attached to
    the FEATURE (the mirror shows the legacy reader build_mode='feature'),
    carrying every buildable story. A child parked somewhere undispatchable
    is reported in `skipped`, not fatal, and is not in the membership."""
    _featone_fully_planned(scenario)
    db = scenario["db"]
    db.execute("delete from public.runs where project_id=%s", (scenario["proj"],))
    # S1 in a buildable status; S2 parked on merged.
    db.execute("update public.issues set status='planned' where id=%s", (scenario["s1"],))
    db.execute("update public.issues set status='merged' where id=%s", (scenario["s2"],))
    db.commit()
    result = db.execute(
        "select public.dispatch_feature_batch(%s) as r", (scenario["f1"],)
    ).fetchone()["r"]
    db.commit()
    # One run, on the feature — not one per story.
    assert [d["issue_id"] for d in result["dispatched"]] == [str(scenario["f1"])]
    assert result["dispatched"][0]["kind"] == "code"
    assert result["story_count"] == 1
    assert [s["issue_id"] for s in result["skipped"]] == [str(scenario["s2"])]
    assert "merged" in result["skipped"][0]["reason"]
    # The membership names exactly the story that was built.
    members = [
        str(r["issue_id"])
        for r in db.execute(
            "select issue_id from public.run_items where run_id=%s order by position",
            (result["run_id"],),
        ).fetchall()
    ]
    assert members == [str(scenario["s1"])]
    # And run_issue_ids resolves it the same way for every caller.
    resolved = [
        str(r["issue_id"])
        for r in db.execute(
            "select issue_id from public.run_issue_ids(%s)", (result["run_id"],)
        ).fetchall()
    ]
    assert resolved == [str(scenario["s1"])]
    # dispatch_issue left the children `queued`, and guard_issue_removal
    # refuses to delete a queued issue — so unwind it here or the fixture's
    # teardown aborts the connection for every test after this one.
    db.execute(
        "update public.issues set status='ready' "
        "where project_id=%s and status in ('queued','running')",
        (scenario["proj"],),
    )
    db.commit()
