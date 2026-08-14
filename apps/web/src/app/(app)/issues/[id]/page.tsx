import Link from "next/link";
import { notFound, redirect } from "next/navigation";
import { ArrowLeft, ChevronRight, GitPullRequest } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createClient } from "@/lib/supabase/server";
import { buildWireframeState, parseDeclaration } from "@/lib/wireframe";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { Badge } from "@/components/ui/badge";
import { parseBugBody } from "@/lib/issue-body";
import { epicLabel, workItemDisplayId } from "@/lib/work-items";
import { fetchActorNames, type ApprovalRow } from "@/lib/approvals";
import { StageTrackerCard } from "@/components/stage-tracker";
import type {
  BuildMode,
  ChildRollup,
  ParentFeature,
} from "@/lib/stage-tracker";
import { LiveActivity } from "./live-activity";
import { AttemptsBlockedBanner } from "./attempts-blocked";
import type { DocumentRow } from "@/lib/documents";
import { IssueDialog, type EpicOption } from "../issue-dialog";
import { RevertButton } from "./revert-button";
import { ResetRunButton } from "./reset-run-button";
import { StopWorkButton } from "./stop-work-button";
import { ResetStageButton } from "./reset-stage-button";
import { IssueActions } from "../issue-actions";
import { type CommentRow } from "./comments-panel";
import { type PrdArtifact } from "./prd-panel";
import { type ChildIssue } from "./stories-panel";
import { type PlanArtifactRow } from "./plan-panel";
import { resolveDefaultTab, tabsForType } from "./work-item-tab-config";
import type { WorkItemViewData } from "./work-item-sections";
import { FeatureView } from "./feature-view";
import { StoryView } from "./story-view";
import { BugView } from "./bug-view";
import { ChoreView } from "./chore-view";
import { AssigneePicker } from "@/components/assignee-picker";



export default async function IssueDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{
    from?: string;
    fromLabel?: string;
    tab?: string;
    panel?: string;
  }>;
}) {
  const { id } = await params;
  const { from, fromLabel, tab, panel } = await searchParams;
  const supabase = await createClient();

  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: issue } = await supabase
    .from("issues")
    .select(
      "id, org_id, project_id, title, body, type, acceptance_criteria, status, parent_id, epic_id, item_no, sub_no, complexity, touches_critical, data_model_impact, complexity_rationale, complexity_basis, complexity_model, abandoned_at, created_at, updated_at, github_issue_number, github_issue_url, instruction_set, breakdown_mode, breakdown_instructions, assignee_id, attempts_blocked_at, projects(name, build_mode, sequential_only, repo_full_name, default_branch), epics(number, title, status)"
    )
    .eq("id", id)
    .maybeSingle();

  if (!issue) notFound();

  const type = issue.type as IssueType;
  // US-15.8: defense in depth. acceptance_criteria is normally a jsonb array
  // of strings, but a malformed breakdown submission can store a bare string;
  // the cast alone let `.map` throw and take the whole page down through the
  // route error boundary. Guard at runtime — a non-array renders as empty.
  const criteria = Array.isArray(issue.acceptance_criteria)
    ? (issue.acceptance_criteria as unknown[]).filter(
        (c): c is string => typeof c === "string"
      )
    : [];
  const projectName =
    (issue.projects as unknown as { name: string } | null)?.name ?? "Project";
  // US-20.6: feature mode extends a feature's rail into Plan and Build.
  // (US-86.1: build_mode is the trigger-maintained mirror of the
  // route-feature-as-one switch — 'feature' or 'story', never 'epic'.)
  const buildMode = ((issue.projects as unknown as
    { build_mode?: string } | null)?.build_mode ?? "feature") as BuildMode;
  // US-86.1: the sequential_only dispatch refusal is gone — dispatch is
  // always legal, and waiting is the serial law's claim-time hold, which
  // reaches every surface through issue_dispatch_block as an hourglass
  // reason. The client-side mirror of the old refusal predicate is retired.
  const blockingIssue = null;
  // US-15.16: the breadcrumb goes back to where you came from. Callers that
  // know the entry point tag the link with ?from=; anything untagged (bookmarks,
  // Activity/notification deep links, and the ~9 other call sites) falls back to
  // the project page so nothing that works today breaks.
  // US-7.10: the epic behind the derived work-item id + header badge.
  const issueEpic = (issue.epics as unknown as
    | { number: number; title: string; status: string }
    | { number: number; title: string; status: string }[]
    | null) instanceof Array
    ? (issue.epics as unknown as { number: number; title: string; status: string }[])[0] ?? null
    : (issue.epics as unknown as { number: number; title: string; status: string } | null);

  const { data: events } = await supabase
    .from("issue_events")
    .select("id, type, payload, created_at")
    .eq("issue_id", issue.id)
    .order("created_at", { ascending: false });

  const { data: runs } = await supabase
    .from("runs")
    .select(
      "id, created_at, status, kind, error, lines_added, lines_removed, files_changed, change_breakdown, pushed_head_sha, pushed_at, claimed_at, last_heartbeat_at, worker_id, stop_requested_at, cancel_reason, work_seconds, cost_usd, workers(name)"
    )
    .eq("issue_id", issue.id)
    .order("created_at", { ascending: false });

  // US-14.8: the newest tool call per run in flight — the factory's own
  // account of what the agent is doing, independent of whether the agent
  // chose to narrate. One row per running run is all the panel needs.
  const runningRunIds = (runs ?? [])
    .filter((r) => r.status === "running" && r.worker_id)
    .map((r) => r.id);
  const { data: runActivity } = runningRunIds.length
    ? await supabase
        .from("run_activity")
        .select("run_id, tool, at, id")
        .in("run_id", runningRunIds)
        // Tie-break on the identity column: several calls can land inside
        // the same clock tick, and "at" alone leaves which one is newest to
        // chance — which showed as "picked up the work" while the run had
        // moved on to reading files.
        .order("at", { ascending: false })
        .order("id", { ascending: false })
    : { data: null };

  // US-31.5: attempts spent against this item, and who spent them. Read
  // here (server component, under RLS) so a blocked item can explain itself
  // without a client round-trip.
  const { data: attemptRows } = issue.attempts_blocked_at
    ? await supabase
        .from("run_attempts")
        .select("reason, worker_id, created_at")
        .eq("issue_id", issue.id)
    : { data: null };
  const { data: orgRow } = issue.attempts_blocked_at
    ? await supabase
        .from("organizations")
        .select("max_item_attempts")
        .eq("id", issue.org_id)
        .single()
    : { data: null };
  const attemptWorkerNames = new Map<string, string>();
  if (attemptRows?.length) {
    const ids = [
      ...new Set(attemptRows.map((a) => a.worker_id).filter(Boolean)),
    ] as string[];
    if (ids.length) {
      const { data: ws } = await supabase
        .from("workers")
        .select("id, name, principals(display_name)")
        .in("id", ids);
      for (const w of ws ?? []) {
        // Supabase types an embedded to-one as an array; normalize.
        const prin = Array.isArray(w.principals)
          ? w.principals[0]
          : (w.principals as { display_name: string | null } | null);
        attemptWorkerNames.set(w.id, prin?.display_name || w.name || "an agent");
      }
    }
  }
  const attemptsByWorker = new Map<string, number>();
  for (const a of attemptRows ?? []) {
    const name = a.worker_id
      ? attemptWorkerNames.get(a.worker_id) ?? "an agent"
      : "an agent";
    attemptsByWorker.set(name, (attemptsByWorker.get(name) ?? 0) + 1);
  }
  // Newest failure's own words — runs are already ordered newest-first.
  const lastAttemptError =
    (runs ?? []).find((r) => r.status === "failed" && r.error)?.error ?? null;

  const { data: artifacts } = await supabase
    .from("artifacts")
    // US-49.2: instruction_set is the brief each version was written from.
    .select("id, kind, content, version, status, instruction_set")
    .eq("issue_id", issue.id)
    .order("version", { ascending: false });

  const prdArtifacts = (artifacts ?? []).filter(
    (a) => a.kind === "prd"
  ) as PrdArtifact[];
  const planArtifacts = (artifacts ?? []).filter(
    (a) => a.kind === "plan" || a.kind === "test_plan"
  ) as PlanArtifactRow[];
  const hasApprovedPlan = (artifacts ?? []).some(
    (a) => a.kind === "plan" && a.status === "approved"
  );
  const hasApprovedPrd = prdArtifacts.some((a) => a.status === "approved");
  // US-44.1: the cheap pass in front of the plan run. The action reports the
  // run in flight instead of offering itself twice, and a draft proposal
  // points at the review rather than queueing a second one.
  const hasActiveElaborateRun = (runs ?? []).some(
    (r) => r.kind === "elaborate" && ["queued", "running"].includes(r.status)
  );
  const elaborationDraft = (artifacts ?? []).find(
    (a) => a.kind === "elaboration" && a.status === "draft"
  );

  // US-48.2: the story's live wireframe. The artifact holds the DECLARATION
  // the kit renders, not the rendered page — which is what lets a kit upgrade
  // restyle every wireframe without re-running an agent, and what lets this
  // summarise the screens without parsing HTML.
  const wireframeArtifact = (artifacts ?? []).find(
    (a) => a.kind === "wireframe" && a.status === "approved"
  );
  const hasActiveWireframeRun = (runs ?? []).some(
    (r) => r.kind === "wireframe" && ["queued", "running"].includes(r.status)
  );
  const wireframe = buildWireframeState({
    artifact: wireframeArtifact ?? null,
    inFlight: hasActiveWireframeRun,
    displayId: workItemDisplayId({
      type: issue.type,
      epicNumber: issueEpic?.number ?? null,
      itemNo: issue.item_no,
      subNo: issue.sub_no,
    }),
    repoFullName:
      (issue.projects as unknown as { repo_full_name: string | null } | null)
        ?.repo_full_name ?? null,
    defaultBranch:
      (issue.projects as unknown as { default_branch: string | null } | null)
        ?.default_branch ?? null,
  });
  const hasActivePrdRun = (runs ?? []).some(
    (r) => r.kind === "prd" && ["queued", "running"].includes(r.status)
  );
  // US-12.1: dispatch_breakdown leaves the issue status alone, so the
  // stage tracker cannot infer an in-flight split from status.
  const hasActiveBreakdownRun = (runs ?? []).some(
    (r) => r.kind === "breakdown" && ["queued", "running"].includes(r.status)
  );
  // US-15.14: the one active run (if any) the manager can reset — queued or
  // running, any kind. runs are ordered newest-first, so the first match is
  // the current attempt.
  const activeRun = (runs ?? []).find((r) =>
    ["queued", "running"].includes(r.status)
  );
  // US-15.15: an actively-claimed run can be asked to stop cooperatively.
  const runningClaim = (runs ?? []).find(
    (r) => r.status === "running" && r.worker_id
  );

  const { data: epics } = await supabase
    .from("epics")
    // US-14.4: `active` drives the new-item epic default.
    // US-71.1: `number` labels/sorts the picker — latest first.
    .select("id, title, active, status, number")
    .eq("project_id", issue.project_id)
    .order("number", { ascending: false });

  let children: ChildIssue[] = [];
  // US-20.6: the roll-up the feature rail is derived from. Null in story
  // mode, and for anything that is not a feature with stories.
  let childRollup: ChildRollup | null = null;
  if (type === "feature") {
    const { data } = await supabase
      .from("issues")
      .select(
        "id, title, status, updated_at, item_no, sub_no, complexity, abandoned_at"
      )
      .eq("parent_id", issue.id)
      // Story order — `sub_no` IS the story number, and the list is read as a
      // sequence ("story 3 is holding the batch"), never as a recency feed.
      .order("item_no", { ascending: true, nullsFirst: false })
      .order("sub_no", { ascending: true, nullsFirst: false });
    const live = (data ?? []).filter((c) => !c.abandoned_at);

    // Which stories hold an approved plan — the single fact that decides
    // whether "Code it" is even legal for a row. Read for every feature now,
    // not only in feature/epic mode, because the per-story dispatch menu needs
    // it in story mode too.
    const childIds = live.map((c) => c.id);
    const { data: approvedChildPlans } = childIds.length
      ? await supabase
          .from("artifacts")
          .select("issue_id")
          .in("issue_id", childIds)
          .eq("kind", "plan")
          .eq("status", "approved")
      : { data: [] as { issue_id: string }[] };
    const withApprovedPlan = new Set(
      (approvedChildPlans ?? []).map((a) => a.issue_id)
    );

    // US-48.3: where each story stands on being drawn. The `no UI surface`
    // verdict is read from the declaration rather than inferred from an empty
    // one — it is an answer an agent gave, and it reads differently from a
    // story nobody has drawn.
    const { data: childWireframes } = childIds.length
      ? await supabase
          .from("artifacts")
          .select("issue_id, content")
          .in("issue_id", childIds)
          .eq("kind", "wireframe")
          .eq("status", "approved")
      : { data: [] as { issue_id: string; content: unknown }[] };
    const drawnByIssue = new Map(
      (childWireframes ?? []).map((a) => [
        a.issue_id,
        parseDeclaration(a.content).no_ui_surface === true
          ? ("no-ui" as const)
          : ("drawn" as const),
      ])
    );
    const { data: childWireframeRuns } = childIds.length
      ? await supabase
          .from("runs")
          .select("issue_id")
          .in("issue_id", childIds)
          .eq("kind", "wireframe")
          .in("status", ["queued", "running"])
      : { data: [] as { issue_id: string }[] };
    const drawingNow = new Set(
      (childWireframeRuns ?? []).map((r) => r.issue_id)
    );

    children = live.map((c) => ({
      ...c,
      displayId: workItemDisplayId({
        type: "story",
        epicNumber: issueEpic?.number ?? null,
        itemNo: c.item_no,
        subNo: c.sub_no,
      }),
      hasApprovedPlan: withApprovedPlan.has(c.id),
      wireframe: drawingNow.has(c.id)
        ? ("in-flight" as const)
        : (drawnByIssue.get(c.id) ?? ("none" as const)),
    })) as ChildIssue[];

    if (buildMode !== "story" && live.length > 0) {
      const { data: childRuns } = await supabase
        .from("runs")
        .select("issue_id, kind, status")
        .in("issue_id", childIds)
        .in("status", ["queued", "running"]);
      const planned = withApprovedPlan;
      const activeByIssue = new Map(
        (childRuns ?? []).map((r) => [r.issue_id, r.kind])
      );
      // us-20.5 rule (d): the story that pauses the batch. `failed` and
      // `needs-fixes` are the statuses; a sent-back plan is only visible to
      // the SQL predicate, so the rail names what it can see and the pool's
      // hold reason remains the authority.
      const troubledRow = live.find((c) =>
        ["failed", "needs-fixes"].includes(c.status)
      );
      const inFlightIndex = live.findIndex((c) => activeByIssue.has(c.id));
      childRollup = {
        total: live.length,
        curated: live.filter((c) => c.status !== "draft").length,
        planApproved: live.filter((c) => planned.has(c.id)).length,
        inPlanReview: live.filter((c) => c.status === "plan-review").length,
        inCodeReview: live.filter((c) => c.status === "in-review").length,
        merged: live.filter((c) => ["merged", "done"].includes(c.status)).length,
        planRunActive: [...activeByIssue.values()].includes("plan"),
        codeRunActive: [...activeByIssue.values()].includes("code"),
        inFlightPosition: inFlightIndex >= 0 ? inFlightIndex + 1 : null,
        troubled: troubledRow
          ? {
              id: troubledRow.id,
              label:
                workItemDisplayId({
                  type: "story",
                  epicNumber: issueEpic?.number,
                  itemNo: troubledRow.item_no,
                  subNo: troubledRow.sub_no,
                }) ?? troubledRow.title,
            }
          : null,
      };
    }
  }

  let parent: { id: string; title: string; type: string } | null = null;
  let parentApprovedPrd: string | null = null;
  // US-22.10: the feature that owns this story's build, for the deferred
  // dispatch action. Null unless the parent really is a feature with
  // children — a story with no parent is unaffected in every mode.
  let parentFeature: ParentFeature | null = null;
  if (issue.parent_id) {
    const { data: parentRow } = await supabase
      .from("issues")
      .select("id, title, type, item_no, epics(number)")
      .eq("id", issue.parent_id)
      .maybeSingle();
    parent = parentRow ?? null;
    if (parentRow) {
      const { count: siblingCount } = await supabase
        .from("issues")
        .select("id", { count: "exact", head: true })
        .eq("parent_id", parentRow.id)
        .is("abandoned_at", null);
      const epic = Array.isArray(parentRow.epics)
        ? parentRow.epics[0]
        : parentRow.epics;
      const epicNumber = (epic as { number: number } | null)?.number ?? null;
      if (parentRow.type === "feature" && (siblingCount ?? 0) > 0) {
        parentFeature = {
          id: parentRow.id,
          label:
            epicNumber !== null && parentRow.item_no !== null
              ? `FEAT-${epicNumber}.${parentRow.item_no}`
              : parentRow.title,
          storyCount: siblingCount ?? 0,
        };
      }
      const { data: parentPrd } = await supabase
        .from("artifacts")
        .select("content")
        .eq("issue_id", parentRow.id)
        .eq("kind", "prd")
        .eq("status", "approved")
        .order("version", { ascending: false })
        .limit(1)
        .maybeSingle();
      parentApprovedPrd = parentPrd?.content ?? null;
    }
  }

  // US-22.9: a story built inside a feature run has no run of its own. Every
  // surface that reads `runs` for this issue would say nothing is in flight
  // while an agent is actively building it, so resolve the owning run here.
  const { data: owningRunRow } = await supabase
    .from("run_items")
    .select(
      "run_id, position, runs!inner(id, issue_id, kind, status, created_at, lines_added, lines_removed, files_changed, pr_url)"
    )
    .eq("issue_id", issue.id)
    .in("runs.status", ["queued", "running", "succeeded"])
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle();
  const owningRun = owningRunRow
    ? ((Array.isArray(owningRunRow.runs)
        ? owningRunRow.runs[0]
        : owningRunRow.runs) as {
        id: string;
        issue_id: string;
        kind: string;
        status: string;
        created_at: string;
        lines_added: number | null;
        lines_removed: number | null;
        files_changed: number | null;
        pr_url: string | null;
      } | null)
    : null;
  // US-27.2: the story owns no code run — the run hangs off its feature — so
  // the Change metrics card was fed an empty list and said "No metrics
  // computed yet" beside a status of `in-review`. Resolve the feature's own
  // title and (US-27.1) whether a commit in that run actually covered THIS
  // story, so "built with the feature" and "nothing was built" can be told
  // apart instead of looking alike.
  let featureRun: {
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
  } | null = null;
  if (owningRun && owningRun.issue_id !== issue.id) {
    const [{ data: ownerIssue }, { data: coverageRows }] = await Promise.all([
      supabase
        .from("issues")
        .select("title")
        .eq("id", owningRun.issue_id)
        .maybeSingle(),
      supabase
        .from("run_item_commits")
        .select("commit_sha")
        .eq("run_id", owningRun.id)
        .eq("issue_id", issue.id),
    ]);
    // `null` (not `false`) when the run predates US-27.1's record — absence of
    // evidence is not evidence that nothing was built, and the card says so.
    const hasRecord = await supabase
      .from("run_item_commits")
      .select("commit_sha")
      .eq("run_id", owningRun.id)
      .limit(1);
    featureRun = {
      runId: owningRun.id,
      issueId: owningRun.issue_id,
      issueTitle: ownerIssue?.title ?? "its feature",
      status: owningRun.status,
      createdAt: owningRun.created_at,
      linesAdded: owningRun.lines_added,
      linesRemoved: owningRun.lines_removed,
      filesChanged: owningRun.files_changed,
      prUrl: owningRun.pr_url,
      covered: (hasRecord.data ?? []).length
        ? (coverageRows ?? []).length > 0
        : null,
    };
  }

  // US-25.5: back goes where you CAME FROM, not where the item lives. The
  // origin rides in the URL (`?from=`) rather than document.referrer, so it
  // survives a reload and a shared link. `from` is either a known shortcut
  // ("work-items", "dashboard") or an arbitrary path — any caller can send
  // the user back to the exact page they left, paired with `?fromLabel=` for
  // the link text, instead of every new caller needing a code change here.
  // A story opened cold (no `from`) falls back to its parent feature — the
  // most useful place to land — and only an item with no parent falls back
  // to the project.
  const breadcrumb =
    from === "work-items"
      ? { href: "/issues", label: "Work Items" }
      : from === "dashboard"
        ? { href: "/dashboard", label: "Things to Do" }
        : from && from.startsWith("/")
          ? { href: from, label: fromLabel || "Back" }
          : parent
            ? {
                href: `/issues/${parent.id}`,
                label: parentFeature?.label
                  ? `${parentFeature.label} · ${parent.title}`
                  : parent.title,
              }
            : { href: `/projects/${issue.project_id}`, label: projectName };

  const hasChildren = children.length > 0;

  const { data: approvals } = await supabase
    .from("approvals")
    .select(
      "id, gate, decision, subject_type, subject_id, comment, actor, auto_approved, created_at"
    )
    .eq("issue_id", issue.id)
    .order("created_at", { ascending: true });
  const approvalRows = (approvals ?? []) as ApprovalRow[];

  // US-2.21/2.22: attached documents. Work-item docs on every type;
  // PRD-linked docs render inside the PRD panel on features.
  const { data: docRows } = await supabase
    .from("documents")
    .select("*")
    .eq("issue_id", issue.id)
    .order("created_at", { ascending: true });
  const workItemDocs = ((docRows ?? []) as DocumentRow[]).filter(
    (d) => d.attached_to === "work-item"
  );
  const prdDocs = ((docRows ?? []) as DocumentRow[]).filter(
    (d) => d.attached_to === "prd"
  );

  // US-7.2: the public Website of each classified environment's deployment,
  // so the release panel links straight through to UAT / Production.
  const { data: envDeploys } = await supabase
    .from("deployments")
    .select("environment, website_url")
    .eq("project_id", issue.project_id)
    .not("website_url", "is", null);
  const envWebsites: Record<string, string> = {};
  for (const d of envDeploys ?? []) {
    if (d.environment && d.website_url && !envWebsites[d.environment]) {
      envWebsites[d.environment] = d.website_url;
    }
  }

  // US-5.12: the work item's comment thread + author name lookups.
  const { data: commentRows } = await supabase
    .from("issue_comments")
    .select("id, author_kind, author_user, author_worker, body, created_at")
    .eq("issue_id", issue.id)
    .order("created_at", { ascending: true });
  const comments = (commentRows ?? []) as CommentRow[];
  const commentWorkerIds = Array.from(
    new Set(
      comments
        .map((c) => c.author_worker)
        .filter((id): id is string => Boolean(id))
    )
  );
  let workerNames: Record<string, string> = {};
  if (commentWorkerIds.length > 0) {
    const { data: workerRows } = await supabase
      .from("workers")
      .select("id, name")
      .in("id", commentWorkerIds);
    workerNames = Object.fromEntries(
      (workerRows ?? []).map((w) => [w.id, w.name])
    );
  }

  const actorNames = await fetchActorNames(supabase, [
    ...approvalRows.map((a) => a.actor),
    ...(docRows ?? []).map((d) => d.created_by),
    ...comments.map((c) => c.author_user),
  ]);

  const bugBody = type === "bug" ? parseBugBody(issue.body) : null;
  const showCriteria = type !== "chore" && criteria.length > 0;

  // US-2.23: "Where it sits" derives from state already on this page plus
  // which environments the project's deployments are classified into.
  const { data: envRows } = await supabase
    .from("deployments")
    .select("environment")
    .eq("project_id", issue.project_id)
    .not("environment", "is", null);
  const envSet = new Set((envRows ?? []).map((r) => r.environment));
  const trackerInput = {
    issueId: issue.id,
    orgId: issue.org_id,
    type,
    status: issue.status,
    latestRunKind: (runs?.[0]?.kind ?? null) as "plan" | "code" | null,
    hasApprovedPlan,
    hasApprovedPrd,
    hasPrd: prdArtifacts.length > 0,
    hasChildren,
    buildMode,
    children: childRollup ?? undefined,
    parent: parentFeature,
    activePrdRun: hasActivePrdRun,
    activeBreakdownRun: hasActiveBreakdownRun,
    sequentialBlockedBy: blockingIssue,
  };

  // US-15.19: the work item's sections live in tabs now; resolve which one
  // opens from the URL — ?tab=, or a legacy ?panel= from an existing deep link
  // (dashboard / the review page) — defaulting to Overview, or Release for a
  // shipped item that has one.
  // US-21.7: a work item has no release tab — releases are their own
  // surface, and they know what shipped.
  const hasRelease = false;
  const defaultTab = resolveDefaultTab({
    tab,
    panel,
    available: tabsForType(type, { hasRelease }),
    status: issue.status,
    hasRelease,
  });

  // US-15.20: one loader, four views. Everything the per-type views need,
  // gathered once here so no query is duplicated across them.
  const viewData: WorkItemViewData = {
    issue: {
      id: issue.id,
      org_id: issue.org_id,
      project_id: issue.project_id,
      title: issue.title,
      body: issue.body,
      status: issue.status,
      epic_id: issue.epic_id,
      instruction_set: issue.instruction_set,
      breakdown_mode: issue.breakdown_mode,
      breakdown_instructions: issue.breakdown_instructions,
      complexity: issue.complexity,
      touches_critical: issue.touches_critical,
      data_model_impact: issue.data_model_impact,
      complexity_rationale: issue.complexity_rationale,
      complexity_basis: issue.complexity_basis,
      complexity_model: issue.complexity_model,
    },
    type,
    criteria,
    showCriteria,
    bugBody,
    parent,
    parentApprovedPrd,
    prdArtifacts,
    prdDocs,
    planArtifacts,
    workItemDocs,
    children,
    childRollup,
    buildMode,
    blockingIssue,
    displayId: workItemDisplayId({
      type: issue.type,
      epicNumber: issueEpic?.number ?? null,
      itemNo: issue.item_no,
      subNo: issue.sub_no,
    }),
    epics: (epics ?? []) as EpicOption[],
    hasActivePrdRun,
    hasActiveBreakdownRun,
    hasActiveElaborateRun,
    hasElaborationDraft: !!elaborationDraft,
    wireframe,
    runs: (runs ?? []) as never[],
    featureRun,
    runActivity: (runActivity ?? []) as never[],
    events: (events ?? []) as never[],
    approvalRows,
    actorNames,
    workerNames,
    comments,
  };

  return (
    <div className="flex w-full flex-col gap-6">
      {/* US-15.19: the cockpit header — identity, where it sits, and the one
          action it needs — pinned so it never scrolls out from under the
          manager while they read a tab. Negative margins let it span the
          scroll container's padding so nothing shows through behind it. */}
      <div className="sticky -top-4 z-30 -mx-4 -mt-4 flex flex-col gap-2 border-b bg-background/95 px-4 pb-3 pt-4 backdrop-blur supports-[backdrop-filter]:bg-background/80 md:-top-6 md:-mx-6 md:-mt-6 md:px-6 md:pt-6">
      {/* US-24.3: the actions column is shrink-0, so without flex-1 here the
          metadata column starves to width 0 next to a wide action bar and its
          contents wrap a word — or a character — at a time ("US-" / "1.1.1").
          flex-1 makes it claim the space; flex-wrap lets the actions drop to
          their own line on a narrow window instead of squeezing it again. */}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1 basis-72">
          <Link
            href={breadcrumb.href}
            className="mb-1 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="size-3" />
            {breadcrumb.label}
          </Link>
          {/* US-24.4: the type icon leads the line, so every work item starts
              at the same left edge whatever its title. State that belongs to
              the item (status, assignee) follows the title; where the item
              SITS is the separate hierarchy line below, not more badges. */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <TypeBadge type={type} />
            <h1 className="truncate text-xl font-semibold tracking-tight">
              {issue.title}
            </h1>
            <StatusBadge status={issue.status as IssueStatus} />
            <AssigneePicker
              issueId={issue.id}
              orgId={issue.org_id}
              assigneeId={issue.assignee_id ?? null}
            />
            {issue.abandoned_at && <Badge variant="secondary">Abandoned</Badge>}
            {/* US-31.5 */}
            {issue.attempts_blocked_at && (
              <Badge variant="destructive">Attempts exhausted</Badge>
            )}
          </div>

          {/* US-24.4: where this item sits — project > epic > feature > item.
              One quiet line, read left to right, instead of the same facts
              scattered as badges across two rows. */}
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
            <Link
              href={`/projects/${issue.project_id}`}
              className="hover:text-foreground"
            >
              {projectName}
            </Link>
            {issueEpic && (
              <>
                <ChevronRight className="size-3 shrink-0 opacity-50" />
                <span>{epicLabel(issueEpic.number, issueEpic.title)}</span>
              </>
            )}
            {parent && (
              <>
                <ChevronRight className="size-3 shrink-0 opacity-50" />
                <Link
                  href={`/issues/${parent.id}`}
                  className="truncate hover:text-foreground"
                >
                  {parentFeature?.label ? `${parentFeature.label} · ` : ""}
                  {parent.title}
                </Link>
              </>
            )}
            {workItemDisplayId({
              type: issue.type,
              epicNumber: issueEpic?.number ?? null,
              itemNo: issue.item_no,
              subNo: issue.sub_no,
            }) && (
              <>
                <ChevronRight className="size-3 shrink-0 opacity-50" />
                <span className="font-mono text-foreground">
                  {workItemDisplayId({
                    type: issue.type,
                    epicNumber: issueEpic?.number ?? null,
                    itemNo: issue.item_no,
                    subNo: issue.sub_no,
                  })}
                </span>
              </>
            )}
          </div>
          {/* US-24.4: the complexity grade and its flags used to sit here too.
              Overview already opens with the same chips plus the rationale
              they came from, so the header was repeating a weaker copy of a
              block two inches below it. The header answers what this is and
              where it sits; the estimate is Overview's job. */}
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
          <IssueDialog
            orgId={issue.org_id}
            projectId={issue.project_id}
            epics={(epics ?? []) as EpicOption[]}
            issue={{
              id: issue.id,
              title: issue.title,
              body: issue.body,
              acceptance_criteria: criteria,
              type,
              status: issue.status,
              epic_id: issue.epic_id,
            }}
          />
          {issue.status === "in-review" && (
            <Button
              variant="outline"
              render={<Link href={`/review/${issue.id}`} />}
            >
              <GitPullRequest className="size-4" />
              Review
            </Button>
          )}
          {issue.status === "plan-review" && (
            <Button
              variant="outline"
              render={<Link href={`/review/${issue.id}`} />}
            >
              <GitPullRequest className="size-4" />
              Review plan
            </Button>
          )}
          {issue.status === "merged" && <RevertButton issueId={issue.id} />}
          {/* US-12.1: the dispatch button used to live here, competing with
              the stage tracker's action as a second primary control for the
              same POST. There is now one primary action, in the tracker's
              fixed slot, which covers every dispatchable state and also
              offers Draft PRD / Dispatch breakdown — neither of which the
              header button could do. */}
          {/* US-15.15: cooperative stop for an actively-claimed run — asks the
              agent to clean up itself; the reset below is the forced fallback. */}
          {runningClaim && (
            <StopWorkButton
              runId={runningClaim.id}
              alreadyRequested={!!runningClaim.stop_requested_at}
            />
          )}
          {activeRun && (
            <ResetRunButton runId={activeRun.id} runKind={activeRun.kind} />
          )}
          {/* US-68.1: send this item back to a chosen stage, pre-merge only.
              Post-merge is RevertButton's territory. */}
          {!["merged", "done"].includes(issue.status) && (
            <ResetStageButton
              issueId={issue.id}
              issueType={issue.type}
              childCount={children.length}
            />
          )}
          <IssueActions
            issueId={issue.id}
            title={issue.title}
            status={issue.status}
            abandonedAt={issue.abandoned_at}
          />
        </div>
      </div>

      {/* US-31.5: a blocked item explains itself directly under the header —
          attempts spent, who spent them, and the last failure verbatim —
          because nothing will dispatch it until the manager acts. */}
      {issue.attempts_blocked_at && (
        <AttemptsBlockedBanner
          issueId={issue.id}
          summary={{
            attempts: attemptRows?.length ?? 0,
            ceiling: orgRow?.max_item_attempts ?? 5,
            blocked: true,
            by_worker: [...attemptsByWorker.entries()].map(
              ([worker, attempts]) => ({ worker, attempts })
            ),
            last_error: lastAttemptError,
          }}
        />
      )}

      {/* US-15.19: the same tracker, condensed to one row — same stages, same
          realtime, and the same single primary action (us-12.1). */}
      <StageTrackerCard
        input={trackerInput}
        abandoned={!!issue.abandoned_at}
        variant="bar"
      />

      {/* US-22.9: this story is being built inside its feature's run. Say so
          and link to it — no page should claim a story has nothing in flight
          while an agent is actively building it. */}
      {/* US-27.2: the same resolved run feeds this line and the Change
          metrics card, so the two can no longer disagree — and a story its
          feature's run never committed for is told apart from one built
          inside it, rather than both reading as "built with the feature". */}
      {featureRun && (
        <div
          className={
            featureRun.covered === false
              ? "rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm"
              : "rounded-md border border-dashed px-3 py-2 text-sm text-muted-foreground"
          }
        >
          {featureRun.covered === false ? (
            <>
              Its feature was built as one change, but{" "}
              <span className="font-medium">
                no commit in that run covers this story
              </span>{" "}
              — nothing was written for it.{" "}
            </>
          ) : (
            <>Built with its feature as one change — </>
          )}
          <Link
            href={`/issues/${featureRun.issueId}?from=${encodeURIComponent(`/issues/${issue.id}`)}&fromLabel=${encodeURIComponent(issue.title)}`}
            className="font-medium text-foreground underline underline-offset-2"
          >
            see the feature&rsquo;s code run
          </Link>
          {featureRun.status === "succeeded" && featureRun.covered !== false ? (
            <>
              {" · "}
              <Link
                href={`/review/${featureRun.issueId}`}
                className="font-medium text-foreground underline underline-offset-2"
              >
                review the diff
              </Link>
            </>
          ) : null}
          {featureRun.covered === false
            ? ""
            : ". One commit, one review, one decision for every story in it."}
        </div>
      )}

      {(() => {
        // US-13.7: the claimed run's latest narration + liveness, beside
        // the tracker — "what is it doing" next to "what is happening".
        // US-15.19: one line in the header; the full panel is in the Runs tab.
        const active = (runs ?? []).find(
          (r) => r.status === "running" && r.worker_id
        );
        if (!active) return null;
        // US-14.8: what the factory itself last saw the agent do. Derived
        // from the tool calls the API already serves, so it keeps moving
        // between an agent's (rare) progress notes.
        const act = (runActivity ?? []).find((a) => a.run_id === active.id);
        const noteEvent = (events ?? []).find(
          (e) =>
            e.type === "progress-note" &&
            (e.payload as { run_id?: string } | null)?.run_id === active.id
        );
        const w = active.workers as unknown as
          | { name: string }
          | { name: string }[]
          | null;
        return (
          <LiveActivity
            variant="chip"
            issueId={issue.id}
            orgId={issue.org_id}
            runId={active.id}
            workerName={(Array.isArray(w) ? w[0]?.name : w?.name) ?? null}
            claimedAt={active.claimed_at}
            lastHeartbeatAt={active.last_heartbeat_at}
            note={
              ((noteEvent?.payload as { note?: string } | null)?.note ?? null)
            }
            noteAt={noteEvent?.created_at ?? null}
            lastTool={act?.tool ?? null}
            lastToolAt={act?.at ?? null}
          />
        );
      })()}
      </div>

      {/* US-15.20: each work-item type gets its own tab set and layout over
          the same loaded data — a feature leads with its stories and owns a
          PRD tab; everything else owns a Plan tab. */}
      {type === "feature" ? (
        <FeatureView
          data={viewData}
          defaultTab={defaultTab}
          hasRelease={hasRelease}
        />
      ) : type === "bug" ? (
        <BugView
          data={viewData}
          defaultTab={defaultTab}
          hasRelease={hasRelease}
        />
      ) : type === "chore" ? (
        <ChoreView
          data={viewData}
          defaultTab={defaultTab}
          hasRelease={hasRelease}
        />
      ) : (
        <StoryView
          data={viewData}
          defaultTab={defaultTab}
          hasRelease={hasRelease}
        />
      )}
    </div>
  );
}
