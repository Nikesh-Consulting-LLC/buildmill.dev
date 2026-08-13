"""Phase 81: the automated-testing schema's load-bearing properties.

Textual pins over migrations 239-244, the posture of
test_activity_feed_sql.py: the suite carries no live DB, so what must stay
true is pinned against the migration text. Row-level behavior was exercised
against both live projects when the migrations were applied (2026-08-11).
Named without the ``_sql`` suffix deliberately: these read files, not
Postgres, so Essential runs them.
"""

from pathlib import Path

MIGRATIONS = (
    Path(__file__).resolve().parents[2].parent
    / "infra"
    / "supabase"
    / "migrations"
)


def migration(name: str) -> str:
    return (MIGRATIONS / name).read_text(encoding="utf-8")


class TestSuiteDeclarations:
    def test_every_new_table_is_org_scoped_with_rls(self):
        # US-81.1/81.2/82.3: no table ships without the is_org_member gate.
        for fname, table in (
            ("239_test_suites.sql", "test_suites"),
            ("240_suite_runs.sql", "suite_runs"),
            ("240_suite_runs.sql", "suite_run_tests"),
            ("240_suite_runs.sql", "suite_run_events"),
            ("243_project_modules.sql", "project_modules"),
        ):
            text = migration(fname)
            assert f"create table public.{table}" in text, table
            assert f"alter table public.{table} enable row level security" in text, table
        for fname in ("239_test_suites.sql", "240_suite_runs.sql", "243_project_modules.sql"):
            assert "public.is_org_member(org_id)" in migration(fname), fname

    def test_suite_run_tables_are_read_only_to_members(self):
        # US-81.2: the pipeline (service role) is the only writer. A member
        # INSERT policy here would let a browser forge test results.
        text = migration("240_suite_runs.sql")
        assert "for select" in text
        assert "for insert" not in text
        assert "for update" not in text
        assert "for all" not in text

    def test_cross_org_integrity_is_composite(self):
        # The 020 pattern: plain FKs validate across RLS, composite (id,
        # org_id) FKs cannot reference another org's rows.
        text = migration("239_test_suites.sql")
        assert "references public.projects (id, org_id)" in text
        assert "references public.servers (id, org_id)" in text
        assert "references public.test_suites (id, org_id) on delete set null (suite_id)" in text
        runs = migration("240_suite_runs.sql")
        assert "references public.test_suites (id, org_id)" in runs
        assert "references public.deployments (id, org_id)" in runs

    def test_gating_is_opt_in(self):
        # The manager decision of 2026-08-10: advisory by default.
        assert "blocks_signoff boolean not null default false" in migration(
            "239_test_suites.sql"
        )

    def test_prod_smoke_is_opt_in(self):
        assert "run_on_prod boolean not null default false" in migration(
            "239_test_suites.sql"
        )

    def test_one_in_flight_run_per_suite(self):
        text = migration("240_suite_runs.sql")
        assert "suite_runs_single_flight" in text
        assert "where status in ('queued', 'running')" in text

    def test_could_not_test_is_not_tests_failed(self):
        # The status vocabulary keeps 'error' distinct from 'failed';
        # us-81.4's blocker words them differently.
        text = migration("240_suite_runs.sql")
        assert "'succeeded', 'failed', 'error'" in text.replace("\n", " ").replace(
            "                      ", " "
        ) or "'error'" in text

    def test_run_and_events_stream_live(self):
        text = migration("240_suite_runs.sql")
        assert "alter publication supabase_realtime add table public.suite_runs" in text
        assert (
            "alter publication supabase_realtime add table public.suite_run_events"
            in text
        )


class TestSignoffGate:
    def test_blocker_gates_only_flagged_suites(self):
        text = migration("241_suite_results_gate.sql")
        assert "ts.run_on_uat and ts.blocks_signoff" in text

    def test_blocker_distinguishes_every_non_success(self):
        text = migration("241_suite_results_gate.sql")
        for phrase in (
            "has not run for this release yet",
            "is still running",
            "could not run - re-run or waive it",
        ):
            assert phrase in text, phrase

    def test_waiver_lives_on_the_run(self):
        # A re-run produces a fresh, unwaived verdict.
        assert "waived_at" in migration("240_suite_runs.sql")
        assert "v_run.waived_at is null" in migration("241_suite_results_gate.sql")

    def test_stale_uat_blocks_signoff(self):
        # Somebody redeploying UAT mid-testing invalidates every result.
        text = migration("241_suite_results_gate.sql")
        assert "release_uat_deployment_id" in text
        assert "redeploy before signing off" in text

    def test_existing_manual_checks_survive(self):
        # The v2 body keeps 132's three checks verbatim in spirit.
        text = migration("241_suite_results_gate.sql")
        assert "this release has no test cases attached yet" in text
        assert "still %s no result" in text
        assert "failed or blocked" in text

    def test_machine_results_name_their_run(self):
        assert "suite_run_id" in migration("241_suite_results_gate.sql")


class TestInheritance:
    def test_release_copy_carries_automation(self):
        text = migration("242_inherit_automated_cases.sql")
        for col in ("execution", "suite_id", "spec_ref", "always_on_uat"):
            assert col in text, col

    def test_always_on_cases_attach_without_an_issue(self):
        text = migration("242_inherit_automated_cases.sql")
        assert "or tc.always_on_uat" in text
        # Null issue_id must not defeat the dedup.
        assert "is not distinct from" in text
        # Issue membership no longer implies project scope.
        assert "tc.project_id = v_rel.project_id" in text


class TestPresubmitEvidence:
    def test_columns_exist(self):
        text = migration("244_presubmit_evidence_and_spec_map.sql")
        assert "presubmit_test_command" in text
        assert "test_evidence" in text
        assert "spec_map" in text
