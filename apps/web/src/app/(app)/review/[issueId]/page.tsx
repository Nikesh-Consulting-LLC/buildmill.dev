import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import {
  ArrowLeft,
  Check,
  CheckSquare,
  CircleSlash,
  ExternalLink,
  FileDiff,
  GitBranch,
  Terminal,
} from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import { createClient } from "@/lib/supabase/server";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DiffView } from "@/components/diff-view";
import { formatChangeSummary } from "@/lib/change-metrics";
import { computeTestGateState } from "@/lib/test-state";
import { AgentNotes } from "./agent-notes";
import { ReviewActions } from "./review-actions";
import { SendForVerification } from "./send-for-verification";
import { ComplexityDetail } from "@/components/complexity-badge";
import { PlanReview, type PlanArtifact } from "./plan-review";
import { PlanReviewActions } from "./plan-review-actions";
import { PrdReviewActions } from "./prd-review-actions";
import { PrdReviewBody } from "./prd-review-body";
import {
  ElaborationReview,
  type ElaborationProposal,
} from "./elaboration-review";
import { TestStateStrip } from "./test-state-strip";
import { TestEvidenceStrip, type TestEvidence } from "./test-evidence-strip";
import { ReviewerPicker } from "@/components/reviewer-picker";
import { loadOrgCapabilities } from "@/lib/permissions";
import { ResetRunButton } from "@/app/(app)/issues/[id]/reset-run-button";

export default async function ReviewDetailPage({
  params,
}: {
  params: Promise<{ issueId: string }>;
}) {
  const { issueId } = await params;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: issue } = await supabase
    .from("issues")
    .select(
      "id, project_id, title, body, acceptance_criteria, status, complexity, touches_critical, data_model_impact, complexity_rationale, complexity_basis, complexity_model, breakdown_mode, breakdown_instructions, projects(name, repo_full_name)"
    )
    .eq("id", issueId)
    .maybeSingle();

  // US-12.2: PRD review joins plan and code review on this one surface —
  // reviewing a PRD, a plan, and a changeset are the same act, so they
  // happen in the same place with the same controls in the same position.
  if (!issue) notFound();

  // US-14.3: a gate that has already been decided is not a missing page.
  // Approving pushes to the work item, but a stale tab, browser Back, or
  // a bookmark can still land here after the fact — and rendering "this
  // page never made it off the line" tells the manager their work may
  // have been deleted when in truth they just finished it. Send them to
  // the work item, where the consequence of the decision is visible.
  if (!["in-review", "plan-review", "prd-review"].includes(issue.status)) {
    redirect(`/issues/${issue.id}?from=dashboard`);
  }

  const project = issue.projects as unknown as {
    name: string;
    repo_full_name: string;
  } | null;

  // US-13.3: the submitting run's hand-back notes, per gate — what the
  // agent flagged rides the submission and shows where the decision is
  // made.
  const latestHandbackNotes = async (kind: string) => {
    const { data } = await supabase
      .from("runs")
      .select("handback_notes")
      .eq("issue_id", issue.id)
      .eq("kind", kind)
      .eq("status", "succeeded")
      .order("created_at", { ascending: false })
      .limit(1);
    return data?.[0]?.handback_notes ?? null;
  };

  // US-44.1: the elaboration gate. It branches on the DRAFT, not on a
  // status — an elaborate run deliberately never moves the story, so there
  // is no `elaboration-review` status to key off and inventing one would put
  // a fourth box on the rail for a pass that is optional by design.
  const { data: elaborationRows } = await supabase
    .from("artifacts")
    .select("id, content, version")
    .eq("issue_id", issue.id)
    .eq("kind", "elaboration")
    .eq("status", "draft")
    .order("version", { ascending: false })
    .limit(1);
  const elaboration = elaborationRows?.[0];
  if (elaboration) {
    let proposal: ElaborationProposal | null = null;
    try {
      proposal = JSON.parse(elaboration.content) as ElaborationProposal;
    } catch {
      proposal = null;
    }
    if (proposal) {
      return (
        <div className="flex w-full flex-col gap-6">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0">
              <Link
                href={`/issues/${issue.id}`}
                className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-3" />
                Work item
              </Link>
              <h1 className="truncate text-2xl font-semibold tracking-tight">
                {issue.title}
              </h1>
              <p className="text-sm text-muted-foreground">
                {project?.name} · Elaboration review
              </p>
            </div>
          </div>
          <AgentNotes notes={await latestHandbackNotes("elaborate")} />
          <ElaborationReview
            issueId={issue.id}
            proposal={{
              story: proposal.story ?? "",
              acceptance_criteria: proposal.acceptance_criteria ?? [],
              open_questions: proposal.open_questions ?? [],
              proposes_change: !!proposal.proposes_change,
            }}
            currentBody={issue.body ?? ""}
            currentCriteria={
              (issue.acceptance_criteria as string[] | null) ?? []
            }
            isDraft={issue.status === "draft"}
          />
        </div>
      );
    }
  }

  // US-12.2: the PRD gate, on the shared review shell.
  if (issue.status === "prd-review") {
    const { data: prdRows } = await supabase
      .from("artifacts")
      .select("id, content, version")
      .eq("issue_id", issue.id)
      .eq("kind", "prd")
      .eq("status", "draft")
      .order("version", { ascending: false })
      .limit(1);
    const prd = prdRows?.[0];
    // A feature can sit in prd-review with its draft already superseded
    // (a send-back redraft in flight). Nothing to review yet — send the
    // manager back to the work item rather than showing an empty gate.
    if (!prd) redirect(`/issues/${issue.id}?panel=prd&from=dashboard`);

    return (
      <div className="flex w-full flex-col gap-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              href="/dashboard"
              className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3" />
              Things to Do
            </Link>
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {issue.title}
            </h1>
            <p className="text-sm text-muted-foreground">
              {project?.name} · PRD review
            </p>
          </div>
          <PrdReviewActions
            issueId={issue.id}
            projectId={issue.project_id}
            breakdownMode={issue.breakdown_mode ?? "automatic"}
            breakdownInstructions={issue.breakdown_instructions ?? ""}
          />
        </div>
        <AgentNotes notes={await latestHandbackNotes("prd")} />
        <PrdReviewBody
          issueId={issue.id}
          artifactId={prd.id}
          content={prd.content}
          version={prd.version}
        />
      </div>
    );
  }

  if (issue.status === "plan-review") {
    const { data: artifacts } = await supabase
      .from("artifacts")
      .select("id, kind, content, version")
      .eq("issue_id", issue.id)
      .eq("status", "draft")
      .in("kind", ["plan", "test_plan"])
      .order("version", { ascending: false });

    // us-5.21: structural findings the submit recorded — e.g. a test plan
    // that would materialize zero test cases at approval.
    const { data: findingEvents } = await supabase
      .from("issue_events")
      .select("payload, created_at")
      .eq("issue_id", issue.id)
      .eq("type", "submission-findings")
      .order("created_at", { ascending: false })
      .limit(1);
    const submissionFindings =
      ((findingEvents?.[0]?.payload as { findings?: string[] } | null)
        ?.findings as string[] | undefined) ?? [];

    return (
      <div className="flex w-full flex-col gap-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <Link
              href="/dashboard"
              className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-3" />
              Things to Do
            </Link>
            <h1 className="truncate text-2xl font-semibold tracking-tight">
              {issue.title}
            </h1>
            <p className="text-sm text-muted-foreground">
              {project?.name} · plan review
            </p>
          </div>
          <PlanReviewActions issueId={issue.id} />
        </div>
        <AgentNotes notes={await latestHandbackNotes("plan")} />
        {submissionFindings.length > 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
            <p className="font-medium">
              The submission reported structural findings:
            </p>
            <ul className="mt-1 list-disc pl-5">
              {submissionFindings.map((f) => (
                <li key={f}>{f}</li>
              ))}
            </ul>
          </div>
        )}
        {issue.complexity && (
          <ComplexityDetail
            estimate={{
              complexity: issue.complexity,
              touches_critical: issue.touches_critical,
              data_model_impact: issue.data_model_impact,
              complexity_rationale: issue.complexity_rationale,
              complexity_basis: issue.complexity_basis,
              complexity_model: issue.complexity_model,
            }}
          />
        )}
        <PlanReview artifacts={(artifacts ?? []) as PlanArtifact[]} />
      </div>
    );
  }

  // US-54.3: `kind = 'code'` because this is the CODE gate. Without it a
  // wireframe (or plan/test) run landing after the last code run is what
  // gets dressed in the Approve & merge dialog — no diff, no PR, and a
  // server that refuses `approve_run` for non-code runs (us-14.6: never
  // offer an action the factory will refuse).
  const { data: run } = await supabase
    .from("runs")
    .select(
      "id, org_id, reviewer_id, provider, status, stdout, diff, branch_ref, pr_url, started_at, finished_at, lines_added, lines_removed, files_changed, change_breakdown, tokens_in, tokens_out, cost_usd, handback_notes, merged_unapproved_at, test_evidence"
    )
    .eq("issue_id", issueId)
    .eq("kind", "code")
    .eq("status", "succeeded")
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  // US-22.9: a story built inside a feature run has no run of its own. The
  // review that decides its fate is the feature's — one commit, one decision
  // — so send the manager there rather than dead-ending on the work item.
  if (!run) {
    const { data: owningRun } = await supabase
      .from("run_items")
      .select("run_id, runs!inner(issue_id, status, kind)")
      .eq("issue_id", issue.id)
      .eq("runs.status", "succeeded")
      .eq("runs.kind", "code")
      .limit(1)
      .maybeSingle();
    const owner = owningRun
      ? ((Array.isArray(owningRun.runs) ? owningRun.runs[0] : owningRun.runs) as
          | { issue_id: string }
          | null)
      : null;
    if (owner?.issue_id) redirect(`/review/${owner.issue_id}`);
  }

  // US-14.3: same rule — at a code gate with no succeeded run to show,
  // the work item is the honest destination, not a 404.
  if (!run) redirect(`/issues/${issue.id}?from=dashboard`);

  // US-13.11: an in-flight verification run disables re-dispatch.
  const { data: activeTestRuns } = await supabase
    .from("runs")
    .select("id, created_at, worker_id, status")
    .eq("issue_id", issue.id)
    .eq("kind", "test")
    .in("status", ["queued", "running"])
    .limit(1);
  const activeTestRun = (activeTestRuns ?? [])[0] ?? null;
  const hasActiveTestRun = Boolean(activeTestRun);
  // A test run is a separate pool from code/plan workers — nothing claims
  // it if no worker in the project has kept the `test` kind enabled. 15
  // minutes unclaimed is long enough to surface a "this looks stuck" nudge
  // with a one-click reset, rather than leaving it silently queued forever.
  const STALE_TEST_RUN_MINUTES = 15;
  const staleTestRunMinutes =
    activeTestRun && activeTestRun.status === "queued" && !activeTestRun.worker_id
      ? Math.floor(
          (Date.now() - new Date(activeTestRun.created_at).getTime()) / 60000
        )
      : null;
  const isStaleTestRun =
    staleTestRunMinutes !== null && staleTestRunMinutes >= STALE_TEST_RUN_MINUTES;

  // US-9.10: only a review_work member can approve or send back.
  const { can } = await loadOrgCapabilities(supabase, run.org_id, user.id);
  const canReview = can("review_work");

  // Soft merge gate (us-2.6): linked test cases + their latest recorded
  // result decide whether Approve requires a confirmed override.
  const { data: testCases } = await supabase
    .from("test_cases")
    .select("id, title")
    .eq("issue_id", issue.id)
    .eq("status", "active");

  const testCaseIds = (testCases ?? []).map((tc) => tc.id);
  // us-5.19: the joined test_runs row tells the strip which results are
  // agent-verified (source + worker attribution) and carries the evidence.
  let testResults: {
    test_case_id: string;
    result: string;
    note: string | null;
    recorded_at: string | null;
    test_run_id: string | null;
    test_runs: { source: string; worker_name: string } | null;
  }[] = [];
  if (testCaseIds.length > 0) {
    const { data } = await supabase
      .from("test_run_results")
      .select(
        "test_case_id, result, note, recorded_at, test_run_id, test_runs(source, worker_name)"
      )
      .in("test_case_id", testCaseIds);
    testResults = (data ?? []).map((r) => ({
      ...r,
      test_runs: r.test_runs as unknown as {
        source: string;
        worker_name: string;
      } | null,
    }));
  }
  const testGateState = computeTestGateState(testCases ?? [], testResults);
  // A failed/blocked verification pass is an actionable fix task, not a
  // dead end — prefill Reject with the failure detail so "send back to
  // coding" is a one-click confirm instead of the manager retyping it.
  const failingOrBlocked = [...testGateState.failing, ...testGateState.blocked];
  const defaultRejectComment =
    failingOrBlocked.length > 0
      ? "Verification failed:\n" +
        failingOrBlocked
          .map(
            (c) =>
              `- ${c.title}: ${c.latestResult ?? "blocked"}` +
              (c.note ? ` — ${c.note}` : "")
          )
          .join("\n")
      : undefined;

  // US-22.9: a feature-level code run is judged against the acceptance
  // criteria of every story it built, not the feature's own body. Grouped by
  // story so the manager can tell which criterion belongs to what.
  const { data: memberRows } = await supabase
    .from("run_items")
    .select("position, issues!inner(id, title, acceptance_criteria)")
    .eq("run_id", run.id)
    .order("position");
  const members = (memberRows ?? []).map((m) => {
    const i = (Array.isArray(m.issues) ? m.issues[0] : m.issues) as {
      id: string;
      title: string;
      acceptance_criteria: unknown;
    };
    return {
      id: i.id,
      title: i.title,
      criteria: (i.acceptance_criteria as string[]) ?? [],
    };
  });
  const criteria = members.length
    ? members.flatMap((m) => m.criteria.map((c) => `${m.title} — ${c}`))
    : ((issue.acceptance_criteria as string[]) ?? []);
  // US-27.1: what this run actually landed, read from the commits rather than
  // from its status. Run 11c564b0 reported six stories built and had commits
  // for four — the manager approving that PR would have merged two empty
  // stories. Coverage is stated before the decision, not after it.
  const { data: coverageRows } = await supabase
    .from("run_item_commits")
    .select("issue_id, commit_sha")
    .eq("run_id", run.id);
  const landedIds = new Set((coverageRows ?? []).map((c) => c.issue_id));
  const coverage = members.map((m) => ({ ...m, landed: landedIds.has(m.id) }));
  const uncovered = coverage.filter((c) => !c.landed);
  const isSimulated = run.pr_url?.startsWith("simulated://") ?? true;
  // US-2.15: tokens/cost when the provider reported them, "—" when not.
  const fmtTokens = (n: number) =>
    n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
  const usageSummary =
    run.tokens_in !== null || run.tokens_out !== null || run.cost_usd !== null
      ? [
          run.tokens_in !== null || run.tokens_out !== null
            ? `${fmtTokens(run.tokens_in ?? 0)} in / ${fmtTokens(run.tokens_out ?? 0)} out`
            : null,
          run.cost_usd !== null ? `$${Number(run.cost_usd).toFixed(2)}` : null,
        ]
          .filter(Boolean)
          .join(" · ")
      : "—";
  const durationSeconds =
    run.started_at && run.finished_at
      ? Math.max(
          0,
          Math.round(
            (new Date(run.finished_at).getTime() -
              new Date(run.started_at).getTime()) /
              1000
          )
        )
      : null;

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/dashboard"
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            Things to Do
          </Link>
          <h1 className="truncate text-2xl font-semibold tracking-tight">
            {issue.title}
          </h1>
          <p className="text-sm text-muted-foreground">
            {project?.name} · {run.provider}
            {durationSeconds !== null && ` · ${durationSeconds}s`}
            {isSimulated && " · simulated run"}
            {` · ${formatChangeSummary(run)}`}
            {` · ${usageSummary}`}
          </p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <ReviewerPicker
            runId={run.id}
            orgId={run.org_id}
            reviewerId={run.reviewer_id ?? null}
          />
          <ReviewActions
            runId={run.id}
            issueId={issue.id}
            testState={testGateState}
            canReview={canReview}
            mergedUnapproved={Boolean(run.merged_unapproved_at)}
            defaultRejectComment={defaultRejectComment}
          />
          <SendForVerification
            issueId={issue.id}
            activeTestRun={hasActiveTestRun}
            hasTestCases={(testCases ?? []).length > 0}
            projectId={issue.project_id}
          />
          {isStaleTestRun && activeTestRun && (
            <div className="flex flex-col items-end gap-1 text-xs text-amber-700 dark:text-amber-400">
              <span>
                Unclaimed for {staleTestRunMinutes}+ min — no worker has
                picked it up.
              </span>
              <ResetRunButton runId={activeTestRun.id} runKind="test" />
            </div>
          )}
        </div>
      </div>

      <AgentNotes notes={run.handback_notes} />

      <TestStateStrip state={testGateState} projectId={issue.project_id} />

      <TestEvidenceStrip
        evidence={run.test_evidence as TestEvidence | null}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Story</CardTitle>
              <CardDescription>What was asked for.</CardDescription>
            </CardHeader>
            <CardContent>
              {issue.body ? (
                <MarkdownView>{issue.body}</MarkdownView>
              ) : (
                <p className="text-sm text-muted-foreground">No story text.</p>
              )}
            </CardContent>
          </Card>

          {members.length > 1 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {coverage.length - uncovered.length} of {coverage.length}{" "}
                  stories landed
                </CardTitle>
                <CardDescription>
                  {uncovered.length === 0
                    ? "Every story in this run has a commit behind it."
                    : `${uncovered.length} ${uncovered.length === 1 ? "story has" : "stories have"} no commit in this run and went back to the pool — approving this PR does not build ${uncovered.length === 1 ? "it" : "them"}.`}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="grid gap-2">
                  {coverage.map((c) => (
                    <li key={c.id} className="flex items-start gap-2 text-sm">
                      {c.landed ? (
                        <Check className="mt-0.5 size-4 shrink-0 text-emerald-600" />
                      ) : (
                        <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                      )}
                      <span
                        className={
                          c.landed ? undefined : "text-muted-foreground"
                        }
                      >
                        {c.title}
                        {!c.landed && " — no commit"}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <CheckSquare className="size-4 text-muted-foreground" />
                Acceptance criteria
              </CardTitle>
              <CardDescription>
                {members.length > 1
                  ? `Tick these off as you read the diff. One commit built all ${members.length} stories, so this is one decision — approving or sending back covers every one of them.`
                  : "Tick these off as you read the diff."}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="grid gap-2">
                {criteria.map((c, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="mt-0.5 size-4 shrink-0 accent-primary"
                      aria-label={`Criterion ${i + 1} reviewed`}
                    />
                    {c}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <FileDiff className="size-4 text-muted-foreground" />
                Changes
              </CardTitle>
              <CardDescription className="flex flex-wrap items-center gap-2">
                {run.branch_ref && (
                  <Badge variant="secondary" className="gap-1 font-normal">
                    <GitBranch className="size-3" />
                    {run.branch_ref}
                  </Badge>
                )}
                {run.pr_url &&
                  (isSimulated ? (
                    <span className="font-mono text-xs">{run.pr_url}</span>
                  ) : (
                    <a
                      href={run.pr_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-xs underline-offset-4 hover:underline"
                    >
                      {run.pr_url}
                      <ExternalLink className="size-3" />
                    </a>
                  ))}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {run.diff ? (
                <DiffView diff={run.diff} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  No diff recorded for this run.
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Terminal className="size-4 text-muted-foreground" />
                Run log
              </CardTitle>
            </CardHeader>
            <CardContent>
              <details>
                <summary className="cursor-pointer select-none text-sm text-muted-foreground">
                  Show provider output
                </summary>
                <div className="mt-2 overflow-x-auto rounded-md bg-muted/50 p-3">
                  <pre className="text-xs leading-5 whitespace-pre-wrap">
                    {run.stdout ?? "No output captured."}
                  </pre>
                </div>
              </details>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
