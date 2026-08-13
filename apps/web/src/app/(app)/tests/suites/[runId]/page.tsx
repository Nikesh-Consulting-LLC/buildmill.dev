import { notFound, redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { PageHeader } from "@/components/page-header";
import { SuiteRunView, type SuiteRunRow, type SuiteRunTest } from "./suite-run-view";

export default async function SuiteRunPage({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: run } = await supabase
    .from("suite_runs")
    .select(
      "id, org_id, project_id, suite_id, deployment_id, release_id, trigger, commit_sha, base_url, status, tests_total, tests_passed, tests_failed, tests_skipped, log, error, waived_at, waive_reason, started_at, finished_at, created_at"
    )
    .eq("id", runId)
    .maybeSingle();
  if (!run) notFound();

  const [{ data: suite }, { data: tests }] = await Promise.all([
    supabase
      .from("test_suites")
      .select("id, name, layer, results_path")
      .eq("id", run.suite_id)
      .maybeSingle(),
    supabase
      .from("suite_run_tests")
      .select("id, spec_ref, status, duration_ms, message, test_case_id")
      .eq("suite_run_id", runId)
      .order("spec_ref", { ascending: true }),
  ]);

  return (
    <div className="flex w-full flex-col gap-6">
      <PageHeader
        title={`Suite run — ${suite?.name ?? "suite"}`}
        description="A deterministic run of this suite against a deployed instance. JUnit is truth: the verdict comes from the report, not the exit code."
      />
      <SuiteRunView
        run={run as SuiteRunRow}
        suiteName={suite?.name ?? "suite"}
        initialTests={(tests ?? []) as SuiteRunTest[]}
      />
    </div>
  );
}
