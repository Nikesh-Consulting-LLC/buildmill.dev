"""US-44.1: the elaborate run kind — its migration's mechanisms.

No live DB in the suite, so the load-bearing clauses are pinned textually.
The hold exemption's *behaviour* was exercised against a live project in a
rolled-back transaction when the migration was applied: with every sibling
still `draft`, a plan run came back held ("waiting: 2 sibling stories still
being curated") while the elaborate run on the same story was claimable, and
in feature mode the elaborate run was still serialised behind its earlier
sibling ("waiting: story US-1.1.1 ahead of this one is still running").
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2].parent
M176 = (
    ROOT / "infra" / "supabase" / "migrations" / "176_elaborate_a_story.sql"
).read_text(encoding="utf-8")


# ------------------------------------------------------------------ vocabulary


def test_the_four_constraints_widen():
    assert "'guidelines', 'elaborate'" in M176  # runs.kind
    assert "worker_instructions_run_kind_check" in M176
    assert "'prd', 'plan', 'test_plan', 'elaboration'" in M176
    assert "'promotion', 'elaboration'" in M176  # approvals.gate


def test_artifacts_kind_keeps_every_existing_value():
    # Its first widening since 031 — dropping one of these would orphan every
    # plan and PRD in the app.
    block = M176.split("artifacts_kind_check")[2]
    for kind in ("'prd'", "'plan'", "'test_plan'"):
        assert kind in block


def test_approvals_gate_keeps_every_existing_value():
    block = M176.split("approvals_gate_check")[2]
    for gate in (
        "'prd'",
        "'plan'",
        "'code-review'",
        "'qa-signoff'",
        "'merge-override'",
        "'promotion'",
    ):
        assert gate in block


# ------------------------------------------------------- the narrow exemption


def test_the_exemption_is_narrow_not_a_blanket_return():
    """An elaboration IS delivery work for its feature: us-20.5's
    one-in-flight ordering is correct for it and it takes no queue_rank
    privilege. Only the us-15.3 draft-sibling rule — the one it exists to
    resolve — is skipped. A blanket `return null` like the guidelines kind's
    would silently buy it the rest."""
    assert "v_run.kind <> ''elaborate''" in M176
    # NOT the guidelines shape.
    assert "if v_run.kind = 'elaborate' then\n    return null" not in M176
    assert "kind in ('guidelines', 'elaborate')" not in M176


def test_it_refuses_to_run_if_the_guidelines_exemption_is_missing():
    # Rebuilding run_hold_reason from an older body is the 095/105/106
    # failure; this raises rather than quietly dropping us-43.5's rule.
    assert "the us-43.5 guidelines exemption is missing" in M176
    assert "raise exception" in M176


def test_the_hold_edit_is_surgery_over_the_live_body():
    assert "prosrc" in M176
    assert "run_hold_reason has drifted" in M176


# ------------------------------------------------------------------- dispatch


def test_dispatch_refuses_a_feature():
    assert "a feature is elaborated by its PRD" in M176


def test_dispatch_refuses_an_abandoned_item_and_a_duplicate_run():
    assert "issue is abandoned" in M176
    assert "already in flight" in M176


def test_dispatch_leaves_the_issue_status_alone():
    body = M176.split("create or replace function public.dispatch_elaboration")[1]
    dispatch = body.split("$$;")[0]
    # No status write anywhere in the RPC — prd and breakdown dispatch behave
    # the same way, and a fourth rail stage is explicitly not wanted.
    assert "update public.issues" not in dispatch
    assert "prev_issue_status" in dispatch
    assert "'elaboration-dispatched'" in dispatch
    assert "'from_status'" in dispatch


def test_dispatch_carries_the_siblings_and_the_parent_prd():
    body = M176.split("create or replace function public.dispatch_elaboration")[1]
    dispatch = body.split("$$;")[0]
    # The split is a contract: a proposal must be able to see the seams so it
    # does not annex the story next to it.
    assert "sibling_stories" in dispatch
    assert "feature_prd" in dispatch
    assert "sib.id <> p_issue" in dispatch
    assert "sib.abandoned_at is null" in dispatch


def test_dispatch_carries_send_back_feedback():
    assert "decision = 'sent-back'" in M176
    assert "'feedback'" in M176


# ---------------------------------------------------------------- instruction


def test_the_instruction_is_surgery_and_names_its_boundaries():
    assert "when 'elaborate' then" in M176
    assert "STAY INSIDE THIS STORY" in M176
    assert "submit_elaboration" in M176
    # Proposing nothing has to be stated, or the agent invents a rewrite.
    assert "propose nothing" in M176
    # Apostrophes doubled ONCE inside the dollar-quoted branch.
    assert "''''" not in M176


def test_the_instruction_insert_raises_on_drift():
    assert "the else-null tail is not where 176 expects it" in M176
