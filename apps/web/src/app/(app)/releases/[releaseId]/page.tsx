import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, GitBranch, GitCommitHorizontal, Tag } from "lucide-react";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { MarkdownView } from "@/components/markdown-view";
import { fetchActorNames } from "@/lib/approvals";
import { ReleaseActions } from "./release-actions";
import { ReleaseTestCases, type ReleaseCase } from "./release-test-cases";
import { ReleaseSuites, type ReleaseSuiteRun } from "./release-suites";
import {
  ReleaseModuleSuggestions,
  type SuggestedCase,
} from "./release-module-suggestions";

type IncludedItem = {
  issue_id: string;
  title: string;
  type: string;
  display_id: string | null;
};

/** US-21.6: one release — what is in it, what the agent said about it, what
 * has been tested, and everything that has happened to it. */
export default async function ReleaseDetailPage({
  params,
}: {
  params: Promise<{ releaseId: string }>;
}) {
  const { releaseId } = await params;
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: release } = await supabase
    .from("releases")
    .select("*")
    .eq("id", releaseId)
    .maybeSingle();
  if (!release) notFound();

  const { data: project } = await supabase
    .from("projects")
    .select("id, name, repo_full_name")
    .eq("id", release.project_id)
    .maybeSingle();

  // US-50.4: cutting creates release/<version> at the pinned commit — the
  // branch an external environment is deployed from, and the compare a human
  // opens. Shown beside the tag; a cut that could not create it says nothing
  // here, because the release row is the record and the branch is on top.
  const releaseBranch = `release/${release.version}`;
  const repoFullName = project?.repo_full_name ?? "";

  const { data: caseRows } = await supabase
    .from("test_cases")
    .select("id, title, steps, expected_result, source, issue_id, status")
    .eq("release_id", releaseId)
    .eq("status", "active")
    .order("issue_id", { ascending: true, nullsFirst: false })
    .order("title", { ascending: true });

  const { data: resultRows } = await supabase
    .from("release_test_results")
    .select("test_case_id, result, suite_run_id")
    .eq("release_id", releaseId);
  const resultByCase = new Map(
    (resultRows ?? []).map((r) => [r.test_case_id, r.result])
  );
  // US-81.4: a result with a suite_run_id is a machine's verdict.
  const machineCases = new Set(
    (resultRows ?? []).filter((r) => r.suite_run_id).map((r) => r.test_case_id)
  );

  // US-81.3: the project's automated suites and their latest run for this
  // release. Latest-per-suite is derived client-side from a small ordered set.
  const { data: suiteRows } = await supabase
    .from("test_suites")
    .select("id, name, layer, run_on_uat, run_on_prod, blocks_signoff, status")
    .eq("project_id", release.project_id)
    .eq("status", "active")
    .order("name", { ascending: true });
  const { data: suiteRunRows } = await supabase
    .from("suite_runs")
    .select(
      "id, suite_id, trigger, status, tests_total, tests_passed, tests_failed, waived_at, waive_reason, started_at, finished_at, created_at"
    )
    .eq("release_id", releaseId)
    .order("created_at", { ascending: false });

  const included = (release.included_items ?? []) as unknown as IncludedItem[];
  const displayByIssue = new Map(
    included.map((i) => [i.issue_id, i.display_id])
  );

  const cases: ReleaseCase[] = (caseRows ?? []).map((c) => ({
    id: c.id,
    title: c.title,
    steps: c.steps,
    expected_result: c.expected_result,
    source: c.source,
    issue_id: c.issue_id,
    origin_display_id: c.issue_id
      ? displayByIssue.get(c.issue_id) ?? null
      : null,
    result: (resultByCase.get(c.id) ?? null) as ReleaseCase["result"],
    machine: machineCases.has(c.id),
  }));

  // US-82.3: manual regression cases tagged with a touched module, not yet
  // in this release's set — suggestions while the release sits on UAT.
  const touchedModules = ((release.touched_modules ?? []) as string[]).filter(
    Boolean
  );
  let suggestions: SuggestedCase[] = [];
  if (release.status === "uat-deployed" && touchedModules.length) {
    const { data: mods } = await supabase
      .from("project_modules")
      .select("id, name")
      .eq("project_id", release.project_id)
      .in("name", touchedModules);
    const moduleName = new Map((mods ?? []).map((m) => [m.id, m.name]));
    if (moduleName.size) {
      const { data: candidates } = await supabase
        .from("test_cases")
        .select(
          "id, org_id, project_id, issue_id, title, steps, expected_result, source, test_types, environments, module_id"
        )
        .eq("project_id", release.project_id)
        .is("release_id", null)
        .eq("status", "active")
        .eq("execution", "manual")
        .in("module_id", [...moduleName.keys()]);
      const attachedKey = new Set(
        (caseRows ?? []).map((c) => `${c.issue_id ?? ""}|${c.title}`)
      );
      suggestions = (candidates ?? [])
        .filter((c) => !attachedKey.has(`${c.issue_id ?? ""}|${c.title}`))
        .map((c) => ({
          ...c,
          test_types: (c.test_types as string[]) ?? [],
          environments: (c.environments as string[]) ?? [],
          module_name: moduleName.get(c.module_id as string) ?? "module",
        }));
    }
  }

  // US-81.4: the sign-off gate is asked, not re-derived — the same SQL
  // function the endpoint consults, so the page can never say "ready" when
  // the API would refuse (and the suite/stale-UAT checks come for free).
  let signoffBlocker: string | null = null;
  if (release.status === "uat-deployed") {
    const { data: blocker } = await supabase.rpc("release_signoff_blocker", {
      p_release: releaseId,
    });
    signoffBlocker = (blocker as string | null) ?? null;
  }

  // US-90.1: every attempt at describing or deploying this build, in order.
  // The rows persist across retries, so a release that failed three times
  // reads as exactly that — never as a clean first try.
  const { data: prepAttemptRows } = await supabase
    .from("release_prep_runs")
    .select("id, status, error, created_at, finished_at, requested_by")
    .eq("release_id", releaseId)
    .order("created_at", { ascending: true });
  const { data: deployAttemptRows } = await supabase
    .from("deployment_runs")
    .select("id, status, created_at, finished_at, started_by_email")
    .eq("release_id", releaseId)
    .order("created_at", { ascending: true });
  const prepAttempts = prepAttemptRows ?? [];
  const deployAttempts = deployAttemptRows ?? [];
  const hasFailedAttempt =
    prepAttempts.some((a) => a.status === "failed") ||
    deployAttempts.some((a) => a.status === "failed");

  const actorNames = await fetchActorNames(supabase, [
    release.created_by,
    release.signed_off_by,
    release.promoted_by,
    ...prepAttempts.map((a) => a.requested_by),
  ]);
  const who = (uid: string | null) =>
    uid ? actorNames[uid] ?? "a member" : "a member";
  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString() : null;

  const attempts = [
    ...prepAttempts.map((a) => ({
      id: a.id,
      kind: "Notes prep",
      status: a.status,
      reason: a.error,
      by: a.requested_by
        ? `retried by ${who(a.requested_by)}`
        : "queued by the cut",
      at: a.created_at,
    })),
    ...deployAttempts.map((a) => ({
      id: a.id,
      kind: "UAT deploy",
      status: a.status,
      reason: null as string | null,
      by: a.started_by_email ? `by ${a.started_by_email}` : null,
      at: a.created_at,
    })),
  ].sort((x, y) => x.at.localeCompare(y.at));

  const timeline = [
    { label: `Cut by ${who(release.created_by)}`, at: release.created_at },
    { label: "Notes written", at: release.notes_written_at },
    { label: "Deployed to UAT", at: release.uat_deployed_at },
    { label: "Test cases attached", at: release.cases_attached_at },
    {
      label: `Signed off by ${who(release.signed_off_by)}`,
      at: release.signed_off_at,
    },
    {
      label: `Promoted by ${who(release.promoted_by)}`,
      at: release.promoted_at,
    },
    { label: "Live in production", at: release.released_at },
    { label: "Rolled back", at: release.rolled_back_at },
    { label: "Rejected", at: release.rejected_at },
  ].filter((e) => e.at);

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <Link
          href="/releases"
          className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" />
          Releases
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="font-mono text-2xl font-semibold tracking-tight">
              {release.version}
            </h1>
            <StatusBadge status={release.status as IssueStatus} />
          </div>
          <ReleaseActions
            releaseId={release.id}
            version={release.version}
            status={release.status}
            signoffBlocker={signoffBlocker}
          />
        </div>
        <p className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span className="inline-flex items-center gap-1.5">
            <GitCommitHorizontal className="size-3.5" />
            <span className="font-mono">
              {(release.commit_sha ?? "").slice(0, 10)}
            </span>
          </span>
          {release.git_tag && (
            <span className="inline-flex items-center gap-1.5">
              <Tag className="size-3.5" />
              <span className="font-mono">{release.git_tag}</span>
            </span>
          )}
          {repoFullName && (
            <a
              href={`https://github.com/${repoFullName}/tree/${releaseBranch}`}
              target="_blank"
              rel="noopener"
              className="inline-flex items-center gap-1.5 underline-offset-4 hover:text-foreground hover:underline"
              title="The branch cut at the pinned commit — what an external deployment merges"
            >
              <GitBranch className="size-3.5" />
              <span className="font-mono">{releaseBranch}</span>
            </a>
          )}
          <span>{included.length} work items</span>
        </p>
        {release.rejected_reason && (
          <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {release.rejected_reason}
          </p>
        )}
        {release.failure_reason && (
          <p className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {release.failure_reason}
          </p>
        )}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Release notes</CardTitle>
          <CardDescription>
            Written by the agent from the actual commit range — not inferred
            from the current tree.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {release.notes_summary || release.notes_detail ? (
            <>
              {release.notes_summary && (
                <MarkdownView>{release.notes_summary}</MarkdownView>
              )}
              {release.notes_detail && (
                <div className="border-t pt-4">
                  <MarkdownView>{release.notes_detail}</MarkdownView>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              Not written yet — the release run produces them.
            </p>
          )}
        </CardContent>
      </Card>

      <ReleaseSuites
        releaseId={release.id}
        environment="uat"
        releaseStatus={release.status}
        suites={(suiteRows ?? []).filter((s) => s.run_on_uat)}
        runs={(suiteRunRows ?? []) as ReleaseSuiteRun[]}
      />

      {release.status === "released" &&
        (suiteRows ?? []).some((s) => s.run_on_prod) && (
          <ReleaseSuites
            releaseId={release.id}
            environment="production"
            releaseStatus={release.status}
            suites={(suiteRows ?? []).filter((s) => s.run_on_prod)}
            runs={(suiteRunRows ?? []) as ReleaseSuiteRun[]}
          />
        )}

      <ReleaseModuleSuggestions
        releaseId={release.id}
        touchedModules={touchedModules}
        suggestions={suggestions}
      />

      <ReleaseTestCases
        releaseId={release.id}
        version={release.version}
        cases={cases}
        editable={release.status === "uat-deployed"}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Included work items</CardTitle>
            <CardDescription>
              Merged between the last released version and this one.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {included.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No work item in this range had a recorded merge commit.
              </p>
            ) : (
              <ul className="grid gap-1">
                {included.map((i) => (
                  <li
                    key={i.issue_id}
                    className="flex items-baseline gap-2 rounded-md border px-2 py-1 text-sm"
                  >
                    {i.display_id && (
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                        {i.display_id}
                      </span>
                    )}
                    <Link
                      href={`/issues/${i.issue_id}?from=${encodeURIComponent(`/releases/${releaseId}`)}&fromLabel=${encodeURIComponent(release.version)}`}
                      className="min-w-0 truncate underline-offset-4 hover:underline"
                    >
                      {i.title}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Timeline</CardTitle>
            <CardDescription>Everything that happened to it.</CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="grid gap-2">
              {timeline.map((e) => (
                <li
                  key={e.label}
                  className="flex flex-wrap items-baseline justify-between gap-2 border-b pb-2 text-sm last:border-0 last:pb-0"
                >
                  <span>{e.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {when(e.at as string)}
                  </span>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>

        {/* US-90.1: shown once anything failed — the same build, every try.
            The rows persist across retries, so three failures read as three
            failures, never as a clean first attempt. */}
        {hasFailedAttempt && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Attempts</CardTitle>
              <CardDescription>
                Every prep and deploy of this build — the pin never moved.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ol className="grid gap-2">
                {attempts.map((a, i) => (
                  <li
                    key={a.id}
                    className="flex flex-col gap-1 border-b pb-2 text-sm last:border-0 last:pb-0"
                  >
                    <span className="flex flex-wrap items-center justify-between gap-2">
                      <span className="flex items-center gap-2">
                        <span>
                          {a.kind} #{i + 1}
                        </span>
                        <StatusBadge status={a.status as IssueStatus} />
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {a.by ? `${a.by} · ` : ""}
                        {when(a.at)}
                      </span>
                    </span>
                    {a.status === "failed" && a.reason && (
                      <span className="text-xs text-destructive">
                        {a.reason}
                      </span>
                    )}
                  </li>
                ))}
              </ol>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
