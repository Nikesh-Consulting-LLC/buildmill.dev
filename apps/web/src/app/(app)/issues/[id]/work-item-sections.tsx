import Link from "next/link";
import { ElaborateButton } from "./elaborate-button";
import { Bot, CheckSquare, FileDiff, GitPullRequest } from "lucide-react";
import { MarkdownView } from "@/components/markdown-view";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatChangeSummary, sumMetrics } from "@/lib/change-metrics";
import { money } from "@/lib/budget";
import { formatWorkSeconds } from "@/lib/work-seconds";
import { ComplexityDetail } from "@/components/complexity-badge";
import { DocumentsPanel } from "@/components/documents-panel";
import type { DocumentRow } from "@/lib/documents";
import type { ApprovalRow } from "@/lib/approvals";
import type { IssueType } from "@/components/type-badge";
import { LiveActivity } from "./live-activity";
import { CommentsPanel, type CommentRow } from "./comments-panel";
import { InstructionSetPanel } from "./instruction-set-panel";
import { PrdPanel, type PrdArtifact } from "./prd-panel";
import { StoriesPanel, type ChildIssue } from "./stories-panel";
import { PlanPanel, type PlanArtifactRow } from "./plan-panel";
import { WireframePanel } from "./wireframe-panel";
import { DecisionsTimeline } from "./decisions-timeline";
import { IssueDeploymentsPanel } from "./issue-deployments-panel";
import type { EpicOption } from "../issue-dialog";

/** US-15.20: everything the four per-type views need, loaded once by the
 * route. The views own layout; the loader owns data. */
export type WorkItemViewData = {
  issue: {
    id: string;
    org_id: string;
    project_id: string;
    title: string;
    body: string | null;
    status: string;
    epic_id: string | null;
    instruction_set: string | null;
    breakdown_mode: string | null;
    breakdown_instructions: string | null;
    complexity: string | null;
    touches_critical: boolean | null;
    data_model_impact: string | null;
    complexity_rationale: string | null;
    complexity_basis: string | null;
    complexity_model: string | null;
  };
  type: IssueType;
  /** US-44.1: an elaborate run queued or running on this item. */
  hasActiveElaborateRun?: boolean;
  /** US-48.2: the screen an agent drew, before this story was planned. */
  wireframe?: import("./wireframe-panel").WireframeState;
  /** US-44.1: a proposal waiting on the manager at /review. */
  hasElaborationDraft?: boolean;
  criteria: string[];
  showCriteria: boolean;
  bugBody: { repro: string | null; expected: string | null } | null;
  parent: { id: string; title: string; type: string } | null;
  parentApprovedPrd: string | null;
  prdArtifacts: PrdArtifact[];
  prdDocs: DocumentRow[];
  planArtifacts: PlanArtifactRow[];
  workItemDocs: DocumentRow[];
  children: ChildIssue[];
  /** US-20.6: null in story mode, or when the feature has no stories. */
  childRollup?: import("@/lib/stage-tracker").ChildRollup | null;
  /** The project's build mode — decides whether one story may be built alone. */
  buildMode: import("@/lib/stage-tracker").BuildMode;
  /** Sequential build mode: the earliest other non-terminal issue in this
   * project, if any — holds every issue's dispatch (plan and code) but its
   * own until it reaches merged. */
  blockingIssue?: { id: string; label: string } | null;
  /** This item's own display id (`FEAT-1.1`), null when it has no numbering. */
  displayId: string | null;
  epics: EpicOption[];
  hasActivePrdRun: boolean;
  hasActiveBreakdownRun: boolean;
  runs: RunLike[];
  /** US-27.2: the feature run that built this story, when the story owns no
   * code run of its own. `covered` is null on a run that predates US-27.1's
   * per-story commit record — unknown, not "nothing". */
  featureRun?: {
    runId: string;
    issueId: string;
    issueTitle: string;
    status: string;
    createdAt: string;
    linesAdded: number | null;
    linesRemoved: number | null;
    filesChanged: number | null;
    prUrl: string | null;
    covered: boolean | null;
  } | null;
  runActivity: { run_id: string; tool: string; at: string }[];
  events: { id: string; type: string; payload: unknown; created_at: string }[];
  approvalRows: ApprovalRow[];
  actorNames: Record<string, string>;
  workerNames: Record<string, string>;
  comments: CommentRow[];
};

type RunLike = {
  id: string;
  created_at: string;
  status: string;
  kind: string;
  claimed_at: string | null;
  last_heartbeat_at: string | null;
  worker_id: string | null;
  pushed_at: string | null;
  pushed_head_sha: string | null;
  /** US-27.10: why a cancelled run was retired. */
  cancel_reason?: string | null;
  /** US-91.11: seconds the agent actually held this run. Read, never
   *  recomputed from timestamps — so this page and Team cannot disagree. */
  work_seconds?: number | null;
  /** US-91.14: what this run cost. */
  cost_usd?: number | null;
  workers?: { name: string } | { name: string }[] | null;
};

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The readable substance of a timeline event. Unknown types and missing keys
 * fall through to no detail, so a new event kind degrades to the old behaviour
 * rather than breaking the timeline. */
export function eventDetail(type: string, payload: unknown): string | null {
  const p = (payload ?? {}) as Record<string, unknown>;
  const str = (k: string) => {
    const v = p[k];
    return typeof v === "string" && v.trim() ? v.trim() : null;
  };
  switch (type) {
    case "progress-note":
      return str("note");
    case "merge-override":
      return str("reason");
    case "created":
      return str("title");
    case "merged":
      return str("pr_url");
    case "changeset-submitted": {
      const files = Array.isArray(p.files) ? p.files.length : null;
      const sha = str("commit_sha")?.slice(0, 7);
      const branch = str("branch_ref");
      return (
        [
          files === null ? null : `${files} file${files === 1 ? "" : "s"}`,
          sha ? `commit ${sha}` : null,
          branch,
        ]
          .filter(Boolean)
          .join(" · ") || null
      );
    }
    case "plan-approved": {
      const n = p.materialized_test_cases;
      return typeof n === "number"
        ? `${n} test case${n === 1 ? "" : "s"} materialized`
        : null;
    }
    case "stories-created": {
      const n = Array.isArray(p.story_ids) ? p.story_ids.length : null;
      return n === null ? null : `${n} stor${n === 1 ? "y" : "ies"} created`;
    }
    case "prd-approved": {
      const v = p.version;
      return typeof v === "number" ? `v${v}` : null;
    }
    case "docs-written": {
      const sha = str("commit_sha")?.slice(0, 7);
      return sha ? `Approved docs written to the repo · commit ${sha}` : null;
    }
    case "docs-write-failed":
      return str("error");
    default:
      return null;
  }
}

/** The claimed run, if any — the one every live section keys off. */
export function activeRunOf(runs: RunLike[]) {
  return runs.find((r) => r.status === "running" && r.worker_id) ?? null;
}

/** US-7.1: the full advisory estimate. The header carries only the grade. */
export function ComplexitySection({ data }: { data: WorkItemViewData }) {
  const { issue } = data;
  if (!issue.complexity) return null;
  return (
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
  );
}

/** The parent feature's approved PRD, read-only, on a child that has one. */
export function ParentPrdSection({ data }: { data: WorkItemViewData }) {
  if (!data.parentApprovedPrd) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          PRD context from {data.parent?.title}
        </CardTitle>
        <CardDescription>Read-only — edit it on the feature.</CardDescription>
      </CardHeader>
      <CardContent>
        <details>
          <summary className="cursor-pointer select-none text-sm text-muted-foreground">
            Show approved PRD
          </summary>
          <MarkdownView className="mt-3">{data.parentApprovedPrd}</MarkdownView>
        </details>
      </CardContent>
    </Card>
  );
}

/** The item's own prose. Bugs lead with Repro/Expected instead (US-15.20).
 *
 * US-49.3: the heading is the caller's, because the block is not the same
 * thing on every type — a feature's prose is the requirement a PRD run is
 * dispatched against, a chore's is a description. The empty state travels
 * with it, or a feature says "No description." under a heading that says
 * requirement. */
export function DescriptionSection({
  data,
  title,
  empty = "No description.",
}: {
  data: WorkItemViewData;
  title: string;
  empty?: string;
}) {
  const { issue } = data;
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base">{title}</CardTitle>
          {/* US-25.3: the item's TLDR, not this block's. A manager opening an
              item they have not read in a week is asking what the whole thing
              is, and summarizing only the prose leaves out the plan they are
              about to approve. */}
          <div className="flex items-center gap-2">
            {/* US-44.1: it rewrites this block, so it belongs on this block. */}
            {data.type === "story" ? (
              <ElaborateButton
                issueId={issue.id}
                status={issue.status}
                inFlight={!!data.hasActiveElaborateRun}
                hasDraft={!!data.hasElaborationDraft}
              />
            ) : null}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {issue.body ? (
          <MarkdownView>{issue.body}</MarkdownView>
        ) : (
          <p className="text-sm text-muted-foreground">{empty}</p>
        )}
      </CardContent>
    </Card>
  );
}

export function BugReportSection({ data }: { data: WorkItemViewData }) {
  const { bugBody } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Report</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4">
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              Repro
            </p>
            {bugBody?.repro ? (
              <MarkdownView>{bugBody.repro}</MarkdownView>
            ) : (
              <p className="text-sm text-muted-foreground">Not provided.</p>
            )}
          </div>
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">
              Expected
            </p>
            {bugBody?.expected ? (
              <MarkdownView>{bugBody.expected}</MarkdownView>
            ) : (
              <p className="text-sm text-muted-foreground">Not provided.</p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export function AcceptanceCriteriaSection({ data }: { data: WorkItemViewData }) {
  if (!data.showCriteria) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <CheckSquare className="size-4 text-muted-foreground" />
          Acceptance criteria
        </CardTitle>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2">
          {data.criteria.map((c, i) => (
            <li key={i} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border text-[10px] text-muted-foreground">
                {i + 1}
              </span>
              {c}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

export function StoriesSection({ data }: { data: WorkItemViewData }) {
  const { issue } = data;
  return (
    <StoriesPanel
      orgId={issue.org_id}
      projectId={issue.project_id}
      featureId={issue.id}
      featureStatus={issue.status}
      epicId={issue.epic_id}
      epics={data.epics}
      childIssues={data.children}
      breakdownMode={issue.breakdown_mode ?? "automatic"}
      breakdownInstructions={issue.breakdown_instructions ?? ""}
      breakdownPending={data.hasActiveBreakdownRun}
      rollup={data.childRollup ?? null}
      buildMode={data.buildMode}
      featureLabel={data.displayId ?? issue.title}
      featureTitle={issue.title}
      blockingIssue={data.blockingIssue ?? null}
    />
  );
}

export function PrdSection({ data }: { data: WorkItemViewData }) {
  const { issue } = data;
  return (
    <PrdPanel
      issueId={issue.id}
      orgId={issue.org_id}
      projectId={issue.project_id}
      status={issue.status}
      artifacts={data.prdArtifacts}
      documents={data.prdDocs}
      actorNames={data.actorNames}
      hasActivePrdRun={data.hasActivePrdRun}
      currentInstructionSet={issue.instruction_set}
    />
  );
}

export function PlanSection({ data }: { data: WorkItemViewData }) {
  return (
    <PlanPanel
      issueId={data.issue.id}
      status={data.issue.status}
      artifacts={data.planArtifacts}
      isBug={data.type === "bug"}
    />
  );
}

export function DocumentsSection({ data }: { data: WorkItemViewData }) {
  const { issue } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Documents</CardTitle>
        <CardDescription>
          {data.workItemDocs.length
            ? `${data.workItemDocs.length} attached`
            : "No documents yet"}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <DocumentsPanel
          orgId={issue.org_id}
          projectId={issue.project_id}
          target={{ attachedTo: "work-item", issueId: issue.id }}
          initialDocs={data.workItemDocs}
          actorNames={data.actorNames}
        />
      </CardContent>
    </Card>
  );
}

/** Live account of the claimed run + the pushed-but-unsubmitted warning +
 * change metrics. The header chip owns the realtime channel, so the panel
 * here opts out rather than opening a second one on the same topic. */
export function RunsSection({ data }: { data: WorkItemViewData }) {
  const { runs, issue } = data;
  const active = activeRunOf(runs);
  const act = active
    ? data.runActivity.find((a) => a.run_id === active.id)
    : undefined;
  const noteEvent = active
    ? data.events.find(
        (e) =>
          e.type === "progress-note" &&
          (e.payload as { run_id?: string } | null)?.run_id === active.id
      )
    : undefined;
  const w = active?.workers;
  const issueTotal = sumMetrics(runs as never[]);
  // US-91.12 AC5 / US-91.14: what the item cost in time and money, summed over
  // EVERY run against it — failed and superseded attempts included, because
  // that is what it cost.
  const totalSeconds = runs.reduce((n, r) => n + (r.work_seconds ?? 0), 0);
  const totalCost = runs.reduce((n, r) => n + (r.cost_usd ?? 0), 0);

  // US-3.4: a claimed code run whose branch has factory-remote pushes.
  const latest = runs[0];
  const pushed =
    latest &&
    latest.kind === "code" &&
    latest.status === "running" &&
    latest.pushed_at
      ? latest
      : null;
  const ago = pushed
    ? Math.max(
        0,
        Math.round((Date.now() - new Date(pushed.pushed_at!).getTime()) / 60000)
      )
    : 0;

  return (
    <div className="flex flex-col gap-6">
      {active && (
        <LiveActivity
          subscribe={false}
          issueId={issue.id}
          orgId={issue.org_id}
          runId={active.id}
          workerName={(Array.isArray(w) ? w[0]?.name : w?.name) ?? null}
          claimedAt={active.claimed_at}
          lastHeartbeatAt={active.last_heartbeat_at}
          note={(noteEvent?.payload as { note?: string } | null)?.note ?? null}
          noteAt={noteEvent?.created_at ?? null}
          lastTool={act?.tool ?? null}
          lastToolAt={act?.at ?? null}
        />
      )}

      {/* US-49.6: the brief lives beside the run it redirects. Expanded while
          an agent is working — that is when changing it does something no
          other surface can — and folded away when nothing is in flight. */}
      <InstructionSetPanel
        issueId={issue.id}
        orgId={issue.org_id}
        instructionSet={issue.instruction_set}
        collapsible={!active}
      />

      {pushed && (
        <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
          <GitPullRequest className="size-4 shrink-0 text-amber-600" />
          <span>
            Pushed{" "}
            <code className="font-mono text-xs">
              {(pushed.pushed_head_sha ?? "").slice(0, 7)}
            </code>{" "}
            to the factory remote{" "}
            {ago < 60 ? `${ago} min ago` : `${Math.round(ago / 60)} h ago`} ·
            awaiting submit — if the claim expires, the factory submits it
            automatically.
          </span>
        </div>
      )}

      {/* US-27.2: a story built inside its feature's run owns no code run, so
          this card was fed the story's plan run alone and read "No metrics
          computed yet" beside a status of `in-review` — the page saying
          "ready for your review" and "nothing was built" at once. The code
          was real; it belongs to the feature. Say whose it is, and say
          plainly when a story genuinely got none. */}
      {data.featureRun && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileDiff className="size-4 text-muted-foreground" />
              Built with{" "}
              <Link
                href={`/issues/${data.featureRun.issueId}?from=${encodeURIComponent(`/issues/${issue.id}`)}&fromLabel=${encodeURIComponent(issue.title)}`}
                className="underline underline-offset-2"
              >
                {data.featureRun.issueTitle}
              </Link>
            </CardTitle>
            <CardDescription>
              {data.featureRun.covered === false
                ? "This story's feature was built as one change — but no commit in that run covers this story. Nothing was written for it."
                : data.featureRun.status === "succeeded"
                  ? `One change, one review, one decision for every story in it${
                      data.featureRun.covered === null
                        ? " (this run predates per-story commit tracking)"
                        : ""
                    }.`
                  : "Its feature's code run is in flight — this story is being built inside it."}
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3 text-sm">
            {data.featureRun.covered === false ? (
              <span className="text-muted-foreground">
                Send the feature back to have it built.
              </span>
            ) : (
              <span className="font-mono text-xs">
                {formatChangeSummary(data.featureRun as never)}
              </span>
            )}
            <Link
              href={`/issues/${data.featureRun.issueId}?from=${encodeURIComponent(`/issues/${issue.id}`)}&fromLabel=${encodeURIComponent(issue.title)}`}
              className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
            >
              see the feature&rsquo;s code run
            </Link>
            {data.featureRun.status === "succeeded" && (
              <Link
                href={`/review/${data.featureRun.issueId}`}
                className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
              >
                review the diff
              </Link>
            )}
          </CardContent>
        </Card>
      )}

      {runs.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <FileDiff className="size-4 text-muted-foreground" />
              Change metrics
            </CardTitle>
            <CardDescription>
              {issueTotal
                ? `Work item total: +${issueTotal.lines_added} −${issueTotal.lines_removed} lines · ${issueTotal.files_changed} files across ${runs.length} run${runs.length === 1 ? "" : "s"}${
                    totalSeconds ? ` · ${formatWorkSeconds(totalSeconds)}` : ""
                  }${totalCost ? ` · ${money(totalCost)}` : ""}`
                : data.featureRun
                  ? "This story's own runs produced no code — its code came from the feature's run above."
                  : "No metrics computed yet."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-2">
              {runs.map((r, i) => (
                <li
                  key={r.id}
                  className="flex items-center justify-between gap-2 text-sm"
                >
                  <span className="text-muted-foreground">
                    Run {runs.length - i} · {formatWhen(r.created_at)}
                  </span>
                  {/* US-27.10: a cancelled run stays here with its reason —
                      deleting it would make a mis-dispatch unexplainable, and
                      calling it a failure would be a lie. */}
                  {r.status === "cancelled" ? (
                    <span className="text-xs text-muted-foreground">
                      Cancelled
                      {r.cancel_reason ? ` — ${r.cancel_reason}` : ""}
                    </span>
                  ) : (
                    <span className="font-mono text-xs">
                      {formatChangeSummary(r as never)}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : (
        <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          No runs yet.
        </p>
      )}
    </div>
  );
}

export function DiscussionSection({ data }: { data: WorkItemViewData }) {
  const { issue } = data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Comments</CardTitle>
      </CardHeader>
      <CardContent>
        <CommentsPanel
          issueId={issue.id}
          orgId={issue.org_id}
          comments={data.comments}
          actorNames={data.actorNames}
          workerNames={data.workerNames}
        />
      </CardContent>
    </Card>
  );
}

/** US-49.5: runs and events answer one question — what has happened to this
 * item — and answering it from two tabs meant a dispatch appeared as an event
 * on one and a row on the other. The runs sit ABOVE the timeline rather than
 * interleaved into it: a run is a unit of work with a worker, a diff and a
 * cost; an event is a fact with a timestamp, and merging them would bury the
 * run that is running right now among `status-changed` rows. */
export function HistorySection({ data }: { data: WorkItemViewData }) {
  const { events } = data;
  return (
    <div className="flex flex-col gap-6">
      <RunsSection data={data} />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Timeline</CardTitle>
          <CardDescription>
            {`${events.length} event${events.length === 1 ? "" : "s"} on this issue`}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {events.length > 0 ? (
            <ol className="relative grid gap-4 border-l pl-4">
              {events.map((e) => {
                const detail = eventDetail(e.type, e.payload);
                const worker = (e.payload as { worker?: string } | null)?.worker;
                return (
                  <li key={e.id} className="grid gap-0.5 text-sm">
                    <span className="absolute -left-[5px] mt-1.5 size-2.5 rounded-full border bg-background" />
                    <span className="flex flex-wrap items-center gap-x-2">
                      <span className="font-medium capitalize">
                        {e.type.replaceAll("-", " ")}
                      </span>
                      {worker && (
                        <span className="inline-flex items-center gap-1 text-xs text-violet-600 dark:text-violet-400">
                          <Bot className="size-3" />
                          {worker}
                        </span>
                      )}
                      <span className="text-xs text-muted-foreground">
                        {formatWhen(e.created_at)}
                      </span>
                    </span>
                    {detail && (
                      <span className="whitespace-pre-wrap text-muted-foreground">
                        {detail}
                      </span>
                    )}
                  </li>
                );
              })}
            </ol>
          ) : (
            <p className="text-sm text-muted-foreground">No events yet.</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Decisions</CardTitle>
        </CardHeader>
        <CardContent>
          <DecisionsTimeline
            issueId={data.issue.id}
            approvals={data.approvalRows}
            actorNames={data.actorNames}
          />
        </CardContent>
      </Card>
    </div>
  );
}

/** US-48.2: a story's screen. Offered on every type that gets planned —
 * story, bug and chore — because any of them can change what a user sees. A
 * feature has no wireframe of its own; its screens are its stories'. */
export function WireframeSection({ data }: { data: WorkItemViewData }) {
  return (
    <WireframePanel
      issueId={data.issue.id}
      state={
        data.wireframe ?? {
          version: null,
          noUiSurface: false,
          reason: null,
          summary: null,
          screens: [],
          inFlight: false,
          repoPath: null,
          repoUrl: null,
        }
      }
    />
  );
}

export function commonSlots(data: WorkItemViewData) {
  return {
    wireframe: <WireframeSection data={data} />,
    documents: <DocumentsSection data={data} />,
    discussion: <DiscussionSection data={data} />,
    history: <HistorySection data={data} />,
  };
}
