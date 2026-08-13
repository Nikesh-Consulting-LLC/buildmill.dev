/** Soft merge-gate test state (us-2.6): summarizes an issue's linked test
 * cases and their most recent recorded result, so review-actions can decide
 * whether approving needs an override. Pure — no Supabase import. */

export type TestCaseRow = { id: string; title: string };

export type TestRunResultRow = {
  test_case_id: string;
  result: string;
  recorded_at: string | null;
  test_run_id: string | null;
  /** us-5.19: evidence text an agent attached to the result. */
  note?: string | null;
  /** us-5.19: the owning run's attribution, when the query joins it. */
  test_runs?: { source: string; worker_name: string } | null;
};

export type TestCaseState = {
  id: string;
  title: string;
  /** Most recent result, or null if never run. */
  latestResult: string | null;
  /** Latest test run that recorded a result for this case, if any. */
  latestRunId: string | null;
  /** us-5.19: true when the latest result came from an agent-sourced run. */
  agentVerified: boolean;
  /** us-5.19: the reporting worker's name, when agent-verified. */
  workerName: string | null;
  /** us-5.19: evidence text on the latest result, if any. */
  note: string | null;
};

export type TestGateState = {
  cases: TestCaseState[];
  /** Cases whose latest recorded result is a real failure. */
  failing: TestCaseState[];
  /** us-11.4: cases a tester or agent explicitly could not run. Distinct
   * from `failing` — the code is not known to be broken — and distinct from
   * `unrun`, because someone did look and reported an obstacle. */
  blocked: TestCaseState[];
  /** Cases with no recorded outcome — never run, still pending, or skipped. */
  unrun: TestCaseState[];
  /** True when at least one linked test case is failing, blocked, or has no
   * recorded outcome — approving requires a confirmed merge-override in that
   * case. A blocked case is still an unverified gate. */
  needsOverride: boolean;
};

const PASS_RESULTS = new Set(["pass"]);
// 'pending' is the default a freshly started run writes for every selected case,
// and 'skipped' is a decision not to execute — neither is a recorded outcome, so
// both mean "not run", not "failed".
const UNRUN_RESULTS = new Set(["pending", "skipped"]);
// us-11.4: migration 063 added 'blocked' to test_run_results.result, and
// db.report_test_results maps a worker's `blocked` status onto it — but this
// file predated that value, so it fell through to the `failing` catch-all and
// an unperformed manual test was reported to the manager as a failure.
// (test_run_results.result is one of
// 'pending' | 'pass' | 'fail' | 'skipped' | 'blocked'.)
const BLOCKED_RESULTS = new Set(["blocked"]);

function bucketOf(
  result: string | null
): "passing" | "failing" | "blocked" | "unrun" {
  if (result === null) return "unrun";
  const r = result.toLowerCase();
  if (PASS_RESULTS.has(r)) return "passing";
  if (UNRUN_RESULTS.has(r)) return "unrun";
  if (BLOCKED_RESULTS.has(r)) return "blocked";
  return "failing";
}

export function computeTestGateState(
  testCases: TestCaseRow[],
  results: TestRunResultRow[]
): TestGateState {
  const latestByCase = new Map<string, TestRunResultRow>();
  for (const r of results) {
    const existing = latestByCase.get(r.test_case_id);
    if (
      !existing ||
      (r.recorded_at ?? "") > (existing.recorded_at ?? "")
    ) {
      latestByCase.set(r.test_case_id, r);
    }
  }

  const cases: TestCaseState[] = testCases.map((tc) => {
    const latest = latestByCase.get(tc.id);
    return {
      id: tc.id,
      title: tc.title,
      latestResult: latest?.result ?? null,
      latestRunId: latest?.test_run_id ?? null,
      agentVerified: latest?.test_runs?.source === "agent",
      workerName:
        latest?.test_runs?.source === "agent"
          ? latest?.test_runs?.worker_name || null
          : null,
      note: latest?.note ?? null,
    };
  });

  const failing = cases.filter((c) => bucketOf(c.latestResult) === "failing");
  const blocked = cases.filter((c) => bucketOf(c.latestResult) === "blocked");
  const unrun = cases.filter((c) => bucketOf(c.latestResult) === "unrun");

  return {
    cases,
    failing,
    blocked,
    unrun,
    needsOverride: failing.length > 0 || blocked.length > 0 || unrun.length > 0,
  };
}
