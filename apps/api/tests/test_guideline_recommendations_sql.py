"""US-5.32: the guideline_recommendations migration's SQL mechanisms.

Same posture as test_content_audit.py: no live DB in the suite, so the
load-bearing clauses are pinned textually; behavior was verified against
the live project when the migration was applied.
"""

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2].parent
    / "infra"
    / "supabase"
    / "migrations"
    / "069_guideline_recommendations.sql"
).read_text(encoding="utf-8")


def test_severity_and_status_are_constrained():
    assert "severity in ('trivial', 'minor', 'major', 'severe')" in MIGRATION
    assert "status in ('pending', 'accepted', 'rejected')" in MIGRATION


def test_no_client_insert_policy():
    # select + update (for the decide RPC) only — submissions arrive
    # through the API's service connection.
    assert MIGRATION.count("create policy") == 2
    assert "for select" in MIGRATION
    assert "for update" in MIGRATION
    assert "for insert" not in MIGRATION


def test_decide_rpc_is_invoker_and_atomic():
    # SECURITY INVOKER: the caller's RLS decides who may decide, and the
    # applied guidelines change is attributed to the manager (us-5.33).
    assert "security invoker" in MIGRATION
    assert "for update;" in MIGRATION  # row locked before deciding
    assert "recommendation not found or already decided" in MIGRATION


def test_accept_applies_or_creates_the_section():
    assert "set content = rec.proposed_text" in MIGRATION
    assert "insert into public.project_guidelines" in MIGRATION
    assert "'Agent-recommended section'" in MIGRATION


def test_decision_is_stamped_with_actor_and_time():
    assert "decided_by = auth.uid()" in MIGRATION
    assert "decided_at = now()" in MIGRATION
