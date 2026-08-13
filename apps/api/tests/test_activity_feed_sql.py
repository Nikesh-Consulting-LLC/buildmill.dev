"""US-5.34: the activity_feed read model's SQL mechanisms.

Same posture as test_content_audit.py: the suite carries no live DB, so
the view's load-bearing properties are pinned textually; row-level
behavior was verified against the live project when the migration was
applied (every kind contributed rows; the one real failed deploy carried
failure detail).
"""

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2].parent
    / "infra"
    / "supabase"
    / "migrations"
    / "070_activity_feed.sql"
).read_text(encoding="utf-8")


def test_every_event_source_contributes_a_union_arm():
    for source in (
        "from public.approvals",
        "from public.issue_events",
        "from public.runs",
        "from public.deployment_runs",
        "from public.test_runs",
        "from public.learning_submissions",
        "from public.guideline_recommendations",
        "from public.content_audit",
    ):
        assert source in MIGRATION, source


def test_rls_applies_through_the_view():
    # security_invoker: each member sees exactly what the underlying
    # tables' RLS grants — the view adds no privilege.
    assert "security_invoker = true" in MIGRATION


def test_failures_carry_their_story():
    # Run failures: the error and a stdout tail.
    assert "'error', r.error" in MIGRATION
    assert "right(r.stdout, 1500)" in MIGRATION
    # Deploy failures: the last recorded event and a log tail.
    assert "'log_tail', right(dr.log, 1500)" in MIGRATION
    assert "deployment_run_events" in MIGRATION


def test_outcome_mapping_marks_failures_and_pending():
    assert (
        "case when r.status = 'failed' then 'failure' else 'success' end"
        in MIGRATION
    )
    assert (
        "case when dr.status = 'failed' then 'failure' else 'success' end"
        in MIGRATION
    )
    assert (
        "case when ls.status = 'pending' then 'pending' else 'success' end"
        in MIGRATION
    )


def test_lease_minutiae_stay_out():
    # progress-note heartbeats are excluded; run-failed events defer to
    # the richer runs-derived failure row (no double reporting).
    assert "not in ('progress-note', 'run-failed')" in MIGRATION


def test_no_blind_spots_for_the_named_steps():
    # dispatch / claim / submit / gate / PR open / merge / test reports /
    # deploy start+finish all have a producing arm or event.
    assert "run dispatched" in MIGRATION
    assert "run submitted" in MIGRATION
    assert "PR opened" in MIGRATION
    assert "a.gate || ' ' || a.decision" in MIGRATION  # gate decisions
    assert "deployment started" in MIGRATION
    assert "'deployment ' || dr.status" in MIGRATION
    assert "test results reported" in MIGRATION
    # claims and merges arrive via issue_events (run-claimed / merged),
    # which the events arm carries unfiltered.
    assert "from public.issue_events" in MIGRATION
