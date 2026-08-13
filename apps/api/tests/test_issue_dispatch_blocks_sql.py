"""US-74.5 / US-86.1: the Things-to-Do hub asks whether a work item can be
dispatched right now, and gets the answer from the code the factory enforces.

Two questions, one row:
  * `hard` — dispatch_issue would refuse this outright (the button errors).
  * not `hard` — the dispatch is accepted and the run parked by the pool.

US-86.1 rewrote the model underneath (migration 247): routing is two project
switches — follow_build_order (switch 1, the ORDER) and route_feature_as_one
(switch 2, the UNIT) — and execution is governed by one law with no checkbox:
a project works ONE unit at a time, start to merge. The law is always a SOFT
hold; sequential_only's dispatch-time refusal is deleted. build_mode and
sequential_only survive only as trigger-maintained mirrors of the switches.

The point of these tests is the EQUIVALENCE. It isn't enough that the helper
returns some plausible sentence; it has to return the sentence dispatch_issue
raises, and hold exactly what run_hold_reason holds. Anything looser and the
hub drifts away from the pool it is describing.

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
def scene(db):
    """An isolated project (switch 1 on, switch 2 off) with epic E1, features
    F1(item 1) and F2(item 2), F1's stories S1/S2 and F2's story S3. S1 and S3
    hold approved plans; S2 does not. Every story starts 'ready' — under the
    law 'planned' counts as in-progress, so a 'planned' baseline would hold
    everything before a test arranged anything. Everything torn down after."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org")
    org_id = org["id"]
    ids = {k: uuid.uuid4() for k in ("proj", "f1", "f2", "s1", "s2", "s3")}
    db.execute(
        "insert into public.projects (id, org_id, name, repo_full_name, slug, "
        "follow_build_order, route_feature_as_one) "
        "values (%s,%s,'db-test','x/y',%s,true,false)",
        (ids["proj"], org_id, f"db-{uuid.uuid4().hex[:10]}"),
    )
    epic_id = db.execute(
        "select id from public.epics where project_id=%s order by number limit 1",
        (ids["proj"],),
    ).fetchone()["id"]
    db.execute(
        "insert into public.issues (id,org_id,project_id,epic_id,type,title,body,"
        "acceptance_criteria,status,item_no) values "
        "(%s,%s,%s,%s,'feature','F1','b','[]'::jsonb,'ready',1),"
        "(%s,%s,%s,%s,'feature','F2','b','[]'::jsonb,'ready',2)",
        (ids["f1"], org_id, ids["proj"], epic_id,
         ids["f2"], org_id, ids["proj"], epic_id),
    )
    db.execute(
        "insert into public.issues (id,org_id,project_id,epic_id,parent_id,type,title,"
        "body,acceptance_criteria,status,item_no,sub_no) values "
        "(%s,%s,%s,%s,%s,'story','S1','b','[]'::jsonb,'ready',1,1),"
        "(%s,%s,%s,%s,%s,'story','S2','b','[]'::jsonb,'ready',1,2),"
        "(%s,%s,%s,%s,%s,'story','S3','b','[]'::jsonb,'ready',2,1)",
        (ids["s1"], org_id, ids["proj"], epic_id, ids["f1"],
         ids["s2"], org_id, ids["proj"], epic_id, ids["f1"],
         ids["s3"], org_id, ids["proj"], epic_id, ids["f2"]),
    )
    for s in ("s1", "s3"):
        db.execute(
            "insert into public.artifacts (org_id,issue_id,kind,content,version,"
            "status,created_by) values (%s,%s,'plan','#p',1,'approved','agent')",
            (org_id, ids[s]),
        )
    db.commit()
    yield {"db": db, "org": org_id, **ids}
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


def _block(db, issue):
    row = db.execute(
        "select reason, hard from public.issue_dispatch_block(%s, null)", (issue,)
    ).fetchone()
    return (row["reason"], row["hard"]) if row else (None, None)


def _hold(db, issue, kind="plan"):
    return db.execute(
        "select public.issue_hold_reason(%s, %s) as r", (issue, kind)
    ).fetchone()["r"]


def _featone(scene, on: bool):
    """Flip switch 2 (route_feature_as_one). The legacy build_mode column is a
    trigger-maintained mirror of this switch — never written directly."""
    scene["db"].execute(
        "update public.projects set route_feature_as_one=%s where id=%s",
        (on, scene["proj"]),
    )
    scene["db"].commit()


# --- nothing in the way ----------------------------------------------------

def test_a_free_story_reports_no_block(scene):
    assert _block(scene["db"], scene["s1"]) == (None, None)


def test_an_undispatchable_status_is_not_a_block(scene):
    """`merged` isn't blocked, it's finished. The hub must not paint an
    hourglass on work that was never up for dispatch."""
    db = scene["db"]
    db.execute("update public.issues set status='merged' where id=%s", (scene["s1"],))
    db.commit()
    assert _block(db, scene["s1"]) == (None, None)


# --- hard refusals: the text must be what dispatch_issue raises ------------

def test_feature_owns_the_build_matches_the_dispatch_error(scene):
    db = scene["db"]
    _featone(scene, True)
    # 'planned' so the inferred kind is 'code' — the refusal only guards the
    # code kind, and a 'ready' story would infer a plan run instead.
    db.execute("update public.issues set status='planned' where id=%s", (scene["s1"],))
    db.commit()
    reason, hard = _block(db, scene["s1"])
    assert hard is True
    assert "owns the build" in reason

    # The equivalence: dispatch the same story and compare the raised text.
    db.rollback()
    with pytest.raises(psycopg.errors.RaiseException) as err:
        db.execute("select public.dispatch_issue(%s, 'code')", (scene["s1"],))
    db.rollback()
    assert str(err.value).strip().splitlines()[0].strip() == reason


# --- the law: one unit in progress per project, start to merge -------------

def test_the_law_is_a_soft_hold_and_dispatch_succeeds(scene):
    """US-86.1: S2 is being built, so S1 waits — but the dispatch is ACCEPTED
    and the run parked. sequential_only's dispatch-time refusal ("must reach
    merged before you can dispatch a new one") is deleted outright."""
    db = scene["db"]
    db.execute("update public.issues set status='running' where id=%s", (scene["s2"],))
    # S1 needs a code-dispatchable status for the code-run dispatch below.
    db.execute("update public.issues set status='planned' where id=%s", (scene["s1"],))
    db.commit()

    reason, hard = _block(db, scene["s1"])
    assert hard is False
    assert "is being built" in reason

    run_id = db.execute(
        "select public.dispatch_issue(%s, 'code') as r", (scene["s1"],)
    ).fetchone()["r"]
    assert run_id is not None
    db.rollback()


@pytest.mark.parametrize(
    ("status", "phrase"),
    [
        ("plan-review", "is awaiting your plan approval"),
        ("planned", "holds an approved plan awaiting build"),
        ("in-review", "PR is not merged yet"),
    ],
)
def test_the_hold_names_the_blockers_stage(scene, status, phrase):
    """Start to merge means every stage holds — including an approved plan
    parked awaiting the build, which is the manager's own gate to clear."""
    db = scene["db"]
    db.execute("update public.issues set status=%s where id=%s", (status, scene["s2"]))
    db.commit()
    reason = _hold(db, scene["s1"])
    assert reason is not None and reason.startswith("waiting: ")
    assert phrase in reason


def test_a_failed_unit_is_not_in_progress(scene):
    """'failed' ended its journey until the manager redispatches it — the law
    does not hold on it. What DOES fire for a failed sibling is the kept
    switch-1 trouble rule (a pause, not the law); with switch 1 off, nothing
    holds at all."""
    db = scene["db"]
    db.execute("update public.issues set status='failed' where id=%s", (scene["s2"],))
    db.commit()

    reason, hard = _block(db, scene["s1"])
    assert hard is False and reason.startswith("paused: story ")
    assert "is being built" not in reason

    db.execute("update public.projects set follow_build_order=false where id=%s",
               (scene["proj"],))
    db.commit()
    assert _block(db, scene["s1"]) == (None, None)


def test_switch_2_makes_the_feature_one_unit(scene):
    """Under switch 2 the routing unit is the feature: S2 being built never
    holds its own sibling S1 (same unit), but still holds S3 in F2."""
    db = scene["db"]
    _featone(scene, True)
    db.execute("update public.issues set status='running' where id=%s", (scene["s2"],))
    db.commit()
    assert _hold(db, scene["s1"]) is None
    reason = _hold(db, scene["s3"])
    assert reason is not None and "is being built" in reason


# --- the queue: exactly one unit is offerable ------------------------------
# A queued issue is not dispatchable, so issue_dispatch_block answers nothing
# for it — the queue position is the POOL's question, asked of
# issue_hold_reason (what run_hold_reason delegates to for a parked run).

def test_switch_1_orders_the_queue_by_build_order(scene):
    """S2 (US-x.1.2) and S3 (US-x.2.1) both queued: build order says S2 goes
    first, and S3 is told what it is behind."""
    db = scene["db"]
    db.execute("update public.issues set status='queued' where id in (%s,%s)",
               (scene["s2"], scene["s3"]))
    db.commit()
    assert _hold(db, scene["s2"]) is None
    reason = _hold(db, scene["s3"])
    assert reason is not None and "ahead in the queue" in reason


def test_switch_1_off_orders_the_queue_by_dispatch_time(scene):
    """follow_build_order off frees the ORDER only: the unit with the earliest
    queued run goes first, even against story numbering — S3's run was created
    before S2's, so S2 waits."""
    db = scene["db"]
    db.execute("update public.projects set follow_build_order=false where id=%s",
               (scene["proj"],))
    db.execute("update public.issues set status='queued' where id in (%s,%s)",
               (scene["s2"], scene["s3"]))
    r2, r3 = uuid.uuid4(), uuid.uuid4()
    db.execute(
        "insert into public.runs (id,org_id,issue_id,project_id,provider,status,"
        "kind,input_context,created_at) values "
        "(%s,%s,%s,%s,'claude','queued','plan','{}'::jsonb,'2026-01-01T00:00:00Z'),"
        "(%s,%s,%s,%s,'claude','queued','plan','{}'::jsonb,'2026-01-01T00:01:00Z')",
        (r3, scene["org"], scene["s3"], scene["proj"],
         r2, scene["org"], scene["s2"], scene["proj"]),
    )
    db.commit()
    try:
        reason = _hold(db, scene["s2"])
        assert reason is not None and "ahead in the queue" in reason
        assert _hold(db, scene["s3"]) is None
    finally:
        db.rollback()
        db.execute("delete from public.runs where id in (%s,%s)", (r2, r3))
        db.commit()


# --- soft waits: the text must be what the pool would hold on -------------

def test_an_earlier_feature_is_a_soft_wait_and_names_the_blocker(scene):
    """S3 is in F2; F1 is not done. The dispatch is allowed — the pool parks
    the run — so this is a wait, not a refusal, and it says which feature.
    (Under switch 2 a story's code dispatch is refused outright, so ask the
    hold directly — it is what the pool would consult.)"""
    db = scene["db"]
    _featone(scene, True)
    reason = _hold(db, scene["s3"], "code")
    assert reason is not None
    assert "earlier feature to finish" in reason
    assert "FEAT-" in reason and "F1" in reason, (
        f"the blocking feature should be named — got {reason!r}"
    )


def test_sibling_plan_approval_is_a_soft_wait(scene):
    db = scene["db"]
    _featone(scene, True)
    # S1's own feature is the earliest, so the earlier-feature rule is quiet;
    # S2 has no approved plan, so the code phase waits on it (switch 2).
    reason = _hold(db, scene["s1"], "code")
    assert reason is not None and "plan approval" in reason


def test_hold_reason_agrees_with_run_hold_reason_for_a_real_run(scene):
    """The wrapper must be a pure delegation: the same issue and kind, asked
    either way, answer identically."""
    db = scene["db"]
    _featone(scene, True)
    run_id = uuid.uuid4()
    db.execute(
        "insert into public.runs (id,org_id,issue_id,project_id,provider,status,"
        "kind,input_context) values (%s,%s,%s,%s,'claude','queued','code','{}'::jsonb)",
        (run_id, scene["org"], scene["s1"], scene["proj"]),
    )
    db.commit()
    try:
        via_run = db.execute(
            "select public.run_hold_reason(%s) as r", (run_id,)
        ).fetchone()["r"]
        via_issue = db.execute(
            "select public.issue_hold_reason(%s, 'code') as r", (scene["s1"],)
        ).fetchone()["r"]
        assert via_run == via_issue
    finally:
        db.rollback()
        db.execute("delete from public.runs where id=%s", (run_id,))
        db.commit()


# --- the org sweep ---------------------------------------------------------

def test_unblocked_work_yields_no_row_at_all(scene):
    """The sweep's shape: issue_dispatch_block returns ZERO rows when nothing
    is in the way, so the lateral join drops the item entirely. The hub paints
    an hourglass on every row it gets back, so a null-reason row would be a
    false hourglass."""
    db = scene["db"]
    rows = db.execute(
        "select * from public.issue_dispatch_block(%s, null)", (scene["s1"],)
    ).fetchall()
    assert rows == []


def test_the_org_sweep_is_membership_gated(scene):
    """security definer + is_org_member inside: this connection carries no
    JWT, so it is nobody's org member and must see nothing — even though the
    same items ARE blocked when asked directly."""
    db = scene["db"]
    _featone(scene, True)
    db.execute("update public.issues set status='planned' where id=%s", (scene["s1"],))
    db.commit()
    direct, hard = _block(db, scene["s1"])
    assert direct is not None and hard is True, "precondition: S1 is blocked"

    rows = db.execute(
        "select issue_id, reason, hard from public.org_issue_dispatch_blocks(%s)",
        (scene["org"],),
    ).fetchall()
    assert rows == []


def test_the_org_sweep_matches_the_reference_grants(db):
    """org_queue_hold_reasons is the established pattern for an org-wide,
    definer-gated read. The new sweep must be locked down the same way —
    signed-in callers only, never anon."""
    db.rollback()
    grants = {
        r["proname"]: (r["anon"], r["auth"])
        for r in db.execute(
            "select p.proname, "
            "  has_function_privilege('anon', p.oid, 'execute') as anon, "
            "  has_function_privilege('authenticated', p.oid, 'execute') as auth "
            "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
            "where n.nspname='public' and p.proname in "
            "  ('org_queue_hold_reasons','org_issue_dispatch_blocks')"
        ).fetchall()
    }
    assert grants["org_issue_dispatch_blocks"] == grants["org_queue_hold_reasons"]
    assert grants["org_issue_dispatch_blocks"] == (False, True)
