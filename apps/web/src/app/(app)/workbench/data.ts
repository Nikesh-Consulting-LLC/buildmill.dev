import { cache } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import { computeTestGateState, type TestRunResultRow } from "@/lib/test-state";
import { deriveBatchGate, type BatchGate } from "@/lib/batch-gate";
import { workItemDisplayId } from "@/lib/work-items";
import { budgetState } from "@/lib/budget";
import { getServerClient } from "@/lib/request-cache";
import {
  rollupFeatureRows,
  type FeatureRollup,
} from "./feature-rollup";
import {
  prepLiveness,
  type PrepLiveness,
  type PrepRow,
} from "./release-liveness";
import type { OpenClarification } from "./clarifications-card";
import type { GuidelineRecommendation } from "./guideline-recommendations-group";
import type { GuidelineRefresh } from "./guideline-refresh-group";

// US-2.19 / US-6.1: the single source of truth for what Things to Do shows
// and — critically — for the "waiting on you" count. The page renders the
// full result; the app shell reads only the count, so the sidebar badge, the
// tab title, and the page header can never disagree.
//
// US-87.2: the shell no longer gets that count by building the whole dataset.
// `org_pending_count` (migration 249) is the same definition counted in the
// database — see `getPendingCount` at the bottom of this file, and the
// warning there about keeping the two in step.

// US-6.3: a queued run is "stalled" once it has waited this long with no
// online, capable worker to claim it. "Online" means a worker was seen within
// the freshness window — every worker call bumps last_seen_at.
export const QUEUE_STALL_MINUTES = 10;
export const WORKER_ONLINE_MINUTES = 5;

export const AGENT_STATUSES = ["queued", "planning", "running"];
export const WAITING_STATUSES = [
  "prd-review",
  "plan-review",
  "in-review",
  "needs-fixes",
  "failed",
  "planned",
  "ready",
  "draft",
];

export type IssueRow = {
  id: string;
  title: string;
  type: string;
  status: string;
  updated_at: string;
  status_changed_at?: string;
  parent_id: string | null;
  project_id: string;
  // US-7.10: the epic number + item/sub sequence behind the display id.
  item_no?: number | null;
  sub_no?: number | null;
  epics?: { number: number } | { number: number }[] | null;
  // US-22.10: the project's build mode decides whether a story's code
  // dispatch belongs to the story or to the feature above it.
  projects:
    | { name: string; build_mode?: string | null }
    | { name: string; build_mode?: string | null }[]
    | null;
};

/** How a row's action behaves. `navigate` opens a full surface (reviews,
 * sign-offs, triage). `dispatch` / `redispatch` act inline against the same
 * dispatch endpoint the work-item page uses (US-6.2). `approve` acts inline
 * against the same approve endpoint the full review page uses, for the gates
 * where a one-click approval needs no input the row can't supply (US-64.x). */
export type TodoActionMode = "navigate" | "dispatch" | "redispatch" | "approve";

/** US-6.5: which peek a review row can expand into. */
export type PeekKind = "prd" | "plan" | "code";

/** US-6.4: how long a wait has aged — drives the amber/red age chip. */
export type AgeLevel = "normal" | "warn" | "bad";

export type TodoItem = {
  id: string;
  title: string;
  type: string;
  /** US-7.10: epic-scoped display id (e.g. US-1.4.1), null when unavailable. */
  displayId: string | null;
  project: string;
  projectId: string;
  reason: string;
  age: string;
  ageLevel: AgeLevel;
  action: string;
  href: string;
  mode: TodoActionMode;
  /** Failed runs keep a link to inspect the log alongside re-dispatch. */
  inspectHref?: string;
  /** Review rows can expand into a peek (US-6.5). */
  peekKind?: PeekKind;
  /** US-24.1: the feature this story belongs to, for nesting. */
  parent?: ParentFeatureRef | null;
  /** US-64.x: set when `mode` is `approve` — the same endpoint the full
   * review page's Approve button calls, with `thenDispatch` mirroring that
   * page's "also dispatch" default so the row's one click matches what a
   * manager would have gotten clicking through. No body is sent for PRD/plan
   * approve — the API already falls back to the feature's standing values. */
  approve?: { endpoint: string; thenDispatch?: string };
  /** US-64.x: a green "go" button — every `approve` row gets this from its
   * mode automatically; sets it explicitly for a `navigate` row (currently
   * only Triage's "Write PRD") that is still the one deliberate next step,
   * not a plain "go look at this". */
  emphasis?: "success";
  /** US-74.5: why this item cannot be dispatched yet, straight from the SQL
   * the factory enforces (`org_issue_dispatch_blocks`). `hard` means
   * `dispatch_issue` would refuse outright; otherwise the run would be
   * created and immediately parked by the pool. Absent = nothing in the way.
   *
   * Never re-derived here. The dashboard once carried its own copy of one of
   * these rules (`featureOwnsBuild` below), which is fine while it agrees
   * with the database and a lie the moment it doesn't. */
  blocked?: DispatchBlock | null;
  /** us-96.7: set only on a synthesized feature row — the children it
   * replaced, what inside needs the manager by name, and the unanimous
   * batch gate when one exists. See feature-rollup.ts. */
  rollup?: FeatureRollup;
};

/** US-74.5: a build-order block on a work item, as the database reports it. */
export type DispatchBlock = { reason: string; hard: boolean };

/** US-24.1/24.2: enough of the owning feature to render a header row. */
export type ParentFeatureRef = {
  id: string;
  displayId: string | null;
  title: string;
  /** US-25.2 → US-84.1: the unanimous batch gate the feature header can
   * clear in one click (curate / plan / code / approve), set only when
   * EVERY non-abandoned child sits at the same gate and there is more than
   * one. Null the rest of the time — a partial batch invites acting on work
   * the manager has not seen, and the per-story rows are still right there. */
  batchGate?: BatchGate | null;
};

export type TodoGroup = { title: string; items: TodoItem[] };

/** US-21.7: a release, not a per-work-item deployed event. */
export type ReleaseRow = {
  id: string;
  version: string;
  status: string;
  projectId: string;
  project: string;
  itemCount: number;
  age: string;
};

/** US-15.6: a PRD or breakdown run in flight against an issue, carried per
 * issue so the dashboard can distinguish queued from running and PRD from
 * breakdown — states that never move issues.status. */
export type ActiveRun = { kind: string; status: string };

export type AgentItem = {
  id: string;
  title: string;
  projectId: string;
  /** US-91.4: the project's name, so In Progress can group by it. Read off
   * the issue row the item is already built from — no extra query. */
  project: string;
  status: string;
  /** US-24.2: the feature this story belongs to, for nesting. */
  parent?: ParentFeatureRef | null;
  /** US-15.6: type icon + readable id, matching every other surface. */
  type: string;
  displayId: string | null;
  /** US-15.6: the tracked PRD/breakdown run behind this row, when that (not a
   * status in AGENT_STATUSES) is why it's in the factory. Drives the badge:
   * queued reads "Queued", running reads "Drafting PRD"/"Creating stories". */
  activeRun?: ActiveRun;
  /** US-6.3: minutes past its lease this item's running claim is — the
   * worker went quiet and the reaper will return it to the pool. */
  staleMinutes?: number;
  /** US-39.4: why a queued run cannot be claimed, in the words the claim gate
   * itself uses. Absent for an ordinary queued run — labelling normal queueing
   * as a problem is how a warning becomes furniture. */
  holdReason?: string;
  /** US-13.6: liveness of the claiming worker, when a claim is live. */
  runId?: string;
  workerName?: string;
  /** US-35.5: the agent on this run, so its name links to its Team profile. */
  workerPrincipalId?: string | null;
  /** US-35.5: for a queued row, who could take it. Never a prediction of who
   *  will — claims are first-come. */
  eligible?: Eligibility;
  runningMinutes?: number;
  /** Minutes since the worker last spoke (claim or any lease-extending
   * call). */
  silentMinutes?: number;
  /** Silent materially longer than its heartbeat cadence — not the same
   * as working. */
  isSilent?: boolean;
  /** US-13.7: the run's most recent progress note, so in-flight items
   * read as what they're doing, not an undifferentiated "running". */
  lastNote?: string;
};

/** US-13.6: a run that died holding its claim — lease expired, worker
 * reported it cannot proceed, or the run failed — with the plain reason. */
export type IncidentRow = {
  id: string;
  issueId: string;
  title: string;
  projectId: string;
  // US-15.18: the org that owns this incident — the client writes a dismissal
  // scoped to it (dashboard_incident_dismissals is org-scoped by RLS).
  orgId: string;
  project: string;
  /** The triggering event type — 'claim-expired' | 'run-released' | 'run-failed'. */
  kind: string;
  /** US-57.15: the run's own kind ('code', 'plan', ...) — the "stage" a
   * manager reads on a failure card. Null for an older event that predates
   * the payload carrying it. */
  runKind: string | null;
  /** US-57.15: who was holding it, for the card's byline — null when the
   * event predates the payload naming a worker. */
  worker: string | null;
  reason: string;
  age: string;
};

/** US-59.7: a run parked in a resumable state — split by the card into a
 * "needs your input" tier (awaiting_input) and an informational one (paused,
 * or a spend-ceiling stopped run that still has a session to resume). */
export type ParkedRun = {
  id: string;
  issueId: string | null;
  issueTitle: string;
  project: string;
  projectId: string;
  kind: string;
  /** 'paused' | 'awaiting_input' | 'stopped' */
  status: string;
  /** Runner's own account of why (turn_limit, clarification, ...), or the
   * gateway's stopped_reason for a resumable `stopped` run. */
  reason: string | null;
  age: string;
  resumeAttempts: number;
  canResume: boolean;
};

/** US-6.4: a project chip with its count of items waiting on the manager. */
export type ProjectChip = {
  id: string;
  name: string;
  waitingCount: number;
};

/** US-6.3: work queued with no online, capable worker to pick it up. */
export type StalledQueue = {
  count: number;
  oldestMinutes: number;
};

export type DeployRow = {
  id: string;
  name: string;
  projectId: string;
  status: string;
  age: string;
};

// US-15.4: the Completed tab shows every finished agent run (PRD drafted,
// stories created, plan written, code submitted, …) — the factory's output as
// it accrues, not only at the final merge.
// US-19.1: **runs only.** Merged/shipped work items used to be unioned in here,
// but agent and duration are well-defined for a run and ambiguous for a shipped
// item (several runs, possibly different agents). Rather than render ragged
// cells or invent an aggregate, Completed is a pure run feed; a merged item
// surfaces as its last run, and as a Releases row once deployed.
export type CompletedItem = {
  /** the finished run's id. */
  id: string;
  issueId: string;
  title: string;
  projectId: string;
  /** US-15.6: type icon + readable id beside the title, like every other surface. */
  type: string;
  displayId: string | null;
  /** plain-words milestone: "PRD drafted", "Plan written", "Code submitted", … */
  label: string;
  /** when it finished (run.finished_at), ISO — the sort key. */
  at: string;
  /** US-19.1: that same instant preformatted server-side ("2h", "1d"), the way
   * ReleaseRow/DeployRow already do. Formatting it in the client component
   * would mean Date.now() during render — impure, and a hydration mismatch. */
  age: string;
  /** US-19.1: the agent that ran it — "" when the worker row is gone. */
  workerName: string;
  /** US-19.1: finished_at − claimed_at, in ms; null when either is missing.
   * Deliberately NOT measured from started_at: started_at survives a requeue
   * while claimed_at tracks the attempt that actually produced this result.
   * Run 50a91484 in prod carries a started_at 13h17m before its finished_at
   * for a run that took five seconds. */
  durationMs: number | null;
  /** US-91.14: what this run cost, straight off `runs.cost_usd`. Null when
   * the run recorded no cost — rendered as "—", never as $0.00, because
   * "nothing was recorded" and "it was free" are different facts. */
  costUsd: number | null;
};

/** US-15.4: run kind → the milestone words shown when that run finishes. */
const COMPLETED_RUN_LABEL: Record<string, string> = {
  prd: "PRD drafted",
  breakdown: "Stories created",
  plan: "Plan written",
  code: "Code submitted",
  test: "Tests reported",
  release: "Release prepared",
  deploy: "Deployed",
};

/** Result of the waiting-side load: the groups the manager must act on, the
 * open worker questions, and the two derived counts. `waitingCount` matches
 * the page's "Waiting on you (N)" header (groups only); `pendingCount` is what
 * the shell badge/tab show (groups + open questions). */
export type WaitingData = {
  groups: TodoGroup[];
  clarificationItems: OpenClarification[];
  /** US-59.7: runs paused/awaiting_input/resumable-stopped, oldest first. */
  parkedRuns: ParkedRun[];
  /** US-5.32: pending agent recommendations against the guidelines. */
  recommendationItems: GuidelineRecommendation[];
  /** US-43.3: open guidelines refreshes — one card per pass, not per
   * proposed section. */
  refreshItems: GuidelineRefresh[];
  waitingCount: number;
  pendingCount: number;
  // Carried forward so loadThingsToDo can build the context cards without
  // re-querying the issues table.
  issues: IssueRow[];
  /** US-15.6: per-issue active PRD/breakdown run ({kind, status}), replacing
   * the old boolean PRD-only set. */
  activeRunByIssue: Map<string, ActiveRun>;
  /** US-24.1/24.2: the feature an issue belongs to, resolved once so both
   * Waiting on you and In the factory nest over the same lookup. */
  parentOf: (issueId: string) => ParentFeatureRef | null;
};

/** US-86.2: the live feature-owned run behind a nested factory group — the
 * run's issue is the FEATURE (migrations 138/139), so per-story liveness
 * lookups find nothing and eleven stories read "Running" with no agent. */
export type FeatureRunInfo = {
  /** US-91.2: the run itself, so In Progress can requeue a silent build and
   * link its CLI window the same way a story row does. */
  runId: string;
  workerName: string;
  workerPrincipalId: string | null;
  runningMinutes: number;
  silentMinutes: number;
  isSilent: boolean;
};

export type ThingsToDoData = WaitingData & {
  agentItems: AgentItem[];
  /** US-86.2: keyed by feature issue id, present only while its run is live. */
  featureRuns: Record<string, FeatureRunInfo>;
  /** US-91.3: principal id → runs the `interactive` module, for the agents
   * currently holding a claim. Absent means no CLI window to offer. */
  interactiveByPrincipal: Record<string, boolean>;
  stalledQueue: StalledQueue | null;
  /** US-13.6: runs that died holding their claim, most recent first. */
  incidents: IncidentRow[];
  /** Per-project waiting counts for the filter chips (unfiltered). */
  projects: ProjectChip[];
  /** US-37.3: projects whose budget is exhausted, so no new run will start. */
  exhaustedBudgets: ExhaustedBudget[];
  /** US-91.18: projects with merged work that no release has shipped. */
  releaseSuggestions: ReleaseSuggestion[];
};

/** US-91.18: a project holding merged work that no release has shipped.
 *
 * The claim is deliberately the cheap one: work items whose status moved to
 * `merged` since the last release that actually SHIPPED. `previous_release`
 * in the API means exactly that — a rejected or rolled-back release leaves
 * its commits unreleased, so the next cut includes them again.
 *
 * The truth of what a cut contains is the commit range, which only
 * `GET /projects/{id}/releases/preview` can answer, and it costs GitHub calls
 * per project — precisely what Phase 87 removed from page loads. So this is a
 * prompt, the dialog is the authority, and the card says so. */
export type ReleaseSuggestion = {
  projectId: string;
  project: string;
  /** The version this is measured from; null when nothing has ever shipped. */
  sinceVersion: string | null;
  /** The first few items, for a claim that can be checked. */
  items: { id: string; displayId: string | null; title: string; type: string }[];
  total: number;
  /** True when `total` hit the query cap and is an understatement. */
  capped: boolean;
  /** Why a cut would be refused right now — no button when set. */
  blocker: string | null;
  /** us-91.18 UAT amendment: when a release is already in flight, the card
   * IS that release — version, live status, and the items it snapshotted —
   * not a prompt to cut another. Null when no release is in flight. */
  flight: {
    id: string;
    version: string;
    status: string;
    /** From the release's own included_items snapshot — the authority. */
    items: { id: string; displayId: string | null; title: string; type: string }[];
    total: number;
    /** Merged items NOT carried by this release — the next cut's cargo. */
    extraMerged: number;
    /** us-103.4: is an agent actually preparing it? Null in the states where
     * no agent holds anything — a deploy pipeline or a human does — because
     * a card that implied one would be lying in a new way. */
    liveness: PrepLiveness | null;
  } | null;
};

/** us-103.4: the release states where an agent holds (or is owed) the job.
 * Everything later is deploy.py's pipeline or a person. */
const AGENT_HELD = new Set(["queued", "running"]);

/** US-37.3: a project that cannot start work because its budget is spent.
 *
 * A condition, not a work item — deliberately NOT counted anywhere near
 * `waitingCount`. That count drives the sidebar badge and the tab title, and
 * us-36.3 shipped three commits before this one precisely because the first
 * version of that fix would have inflated it with things the manager cannot
 * act on as items. */
export type ExhaustedBudget = {
  id: string;
  name: string;
  spent: number;
  budget: number;
};

export function projectName(row: IssueRow): string {
  const p = row.projects;
  return (Array.isArray(p) ? p[0]?.name : p?.name) ?? "";
}

/** US-7.10: the derived work-item id for a Things-to-Do row, or null. */
export function issueDisplayId(row: IssueRow): string | null {
  const e = row.epics;
  const epicNumber = (Array.isArray(e) ? e[0]?.number : e?.number) ?? null;
  return workItemDisplayId({
    type: row.type,
    epicNumber,
    itemNo: row.item_no ?? null,
    subNo: row.sub_no ?? null,
  });
}

export function formatAge(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** `formatAge` returns "just now" under a minute, which reads as "just now
 * ago" wherever a card appends the word. One helper so every surface agrees. */
export function formatAgeAgo(iso: string): string {
  const age = formatAge(iso);
  return age === "just now" ? age : `${age} ago`;
}

/** US-6.4: amber past a day, red past three. */
export function ageLevel(iso: string): AgeLevel {
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours >= 72) return "bad";
  if (hours >= 24) return "warn";
  return "normal";
}

/** Time the item entered its current status — honest, unlike updated_at,
 * which any edit bumps (US-6.4). Falls back to updated_at pre-backfill. */
function statusSince(i: IssueRow): string {
  return i.status_changed_at ?? i.updated_at;
}

function toItem(
  i: IssueRow,
  reason: string,
  action: string,
  href: string,
  mode: TodoActionMode = "navigate",
  inspectHref?: string,
  blocked?: DispatchBlock | null
): TodoItem {
  const since = statusSince(i);
  return {
    id: i.id,
    title: i.title,
    type: i.type,
    displayId: issueDisplayId(i),
    project: projectName(i),
    projectId: i.project_id,
    reason,
    age: formatAge(since),
    ageLevel: ageLevel(since),
    action,
    href,
    mode,
    inspectHref,
    blocked: blocked ?? null,
  };
}

/* eslint-disable @typescript-eslint/no-explicit-any */
type DB = SupabaseClient<any, "public", any>;

/** Loads everything the "Waiting on you" column and the worker-questions card
 * need, and computes the counts the whole shell keys off. */
export async function loadWaiting(
  supabase: DB,
  orgId: string
): Promise<WaitingData> {
  const [
    { data: issueRows },
    { data: uatReleases },
    { data: activePrdRuns },
    { data: openClarifications },
    { data: pendingRecommendations },
    { data: openRefreshes },
    { data: parkedRunRows },
    { data: dispatchBlockRows },
  ] = await Promise.all([
    supabase
      .from("issues")
      .select(
        "id, title, type, status, updated_at, status_changed_at, parent_id, project_id, item_no, sub_no, epics(number), projects!inner(name, archived_at, build_mode)"
      )
      .eq("org_id", orgId)
      .is("abandoned_at", null)
      .is("projects.archived_at", null)
      .in("status", [...AGENT_STATUSES, ...WAITING_STATUSES])
      .order("updated_at", { ascending: true }),
    // US-21.7: releases waiting on the manager, not per-work-item records.
    supabase
      .from("releases")
      .select("id, version, project_id, status, uat_deployed_at, projects!releases_project_id_fkey!inner(name, archived_at)")
      .eq("org_id", orgId)
      .eq("status", "uat-deployed")
      .is("projects.archived_at", null)
      .order("uat_deployed_at", { ascending: true }),
    // US-3.21 + US-15.6: a PRD or breakdown run never moves issues.status, so
    // we track them separately to know a feature isn't actually idle. We carry
    // {kind, status} (not just the issue id) so the dashboard can tell a
    // queued run from a running one and a breakdown-in-progress from a
    // finished PRD. (plan/code runs DO move issues.status — planning/running —
    // so they're already reflected by AGENT_STATUSES and need no separate row.)
    supabase
      .from("runs")
      .select("issue_id, kind, status")
      .eq("org_id", orgId)
      .in("kind", ["prd", "breakdown"])
      .in("status", ["queued", "running"]),
    // US-5.4: open worker questions waiting for the manager's answer.
    supabase
      .from("clarifications")
      .select(
        // US-14.9: options/multi_select drive the choice controls.
        "id, org_id, issue_id, question, options, multi_select, asked_at, workers(name), issues!inner(id, title, project_id, projects!inner(name))"
      )
      .eq("org_id", orgId)
      .is("answered_at", null)
      .order("asked_at", { ascending: true }),
    // US-5.32: pending agent recommendations against the guidelines.
    // US-43.3: bundled rows (refresh_id set) are reviewed as one document on
    // their own card, so this group holds only the ad-hoc ones an agent
    // raised mid-run. Without the filter a refresh floods it with twenty.
    supabase
      .from("guideline_recommendations")
      .select(
        "id, severity, section_title, section_id, proposed_text, rationale, created_at, project_id, workers(name), projects!inner(name, archived_at), project_guidelines(content)"
      )
      .eq("org_id", orgId)
      .eq("status", "pending")
      .is("refresh_id", null)
      .is("projects.archived_at", null)
      .order("created_at", { ascending: true }),
    // US-43.3: one card per open guidelines refresh — the whole pass.
    supabase
      .from("guideline_refreshes")
      .select(
        "id, summary, created_at, project_id, scope, focus, workers(name), projects!inner(name, archived_at), guideline_recommendations(id, status)"
      )
      .eq("org_id", orgId)
      .eq("status", "pending")
      .is("projects.archived_at", null)
      .order("created_at", { ascending: true }),
    // US-59.7: every run parked in a resumable state. `stopped` rides along
    // too but is filtered to resumable ones (a captured session id) below —
    // the vast majority of `stopped`/`failed` history has none and would
    // otherwise flood this card with runs nothing can be done about.
    supabase
      .from("runs")
      .select(
        "id, kind, status, issue_id, resume_reason, resume_state_at, resume_attempts, stopped_reason, claude_session_id, project_id, issues!runs_issue_org_fk(title), projects!inner(name, archived_at)"
      )
      .eq("org_id", orgId)
      .in("status", ["paused", "awaiting_input", "stopped"])
      .is("projects.archived_at", null)
      .order("resume_state_at", { ascending: true }),
    // US-74.5: which work items the build rules will not let through yet, and
    // why. Answered by the same functions dispatch_issue and the claim gate
    // use, so the hourglass this page draws and the refusal the factory would
    // give can never say different things.
    supabase.rpc("org_issue_dispatch_blocks", { p_org: orgId }),
  ]);

  const issues = (issueRows ?? []) as unknown as IssueRow[];

  // US-74.5: issue id → why it can't move. Only blocked items are returned.
  const blockByIssue = new Map<string, DispatchBlock>();
  for (const b of (dispatchBlockRows ?? []) as {
    issue_id: string;
    reason: string | null;
    hard: boolean | null;
  }[]) {
    if (b.reason) {
      blockByIssue.set(b.issue_id, { reason: b.reason, hard: b.hard === true });
    }
  }
  // US-15.6: per-issue active-run record (kind + status), not just a boolean.
  // Running wins over queued when an issue somehow has both, so the badge
  // reflects the more advanced state.
  const activeRunByIssue = new Map<string, ActiveRun>();
  for (const r of (activePrdRuns ?? []) as {
    issue_id: string;
    kind: string;
    status: string;
  }[]) {
    const existing = activeRunByIssue.get(r.issue_id);
    if (!existing || (existing.status !== "running" && r.status === "running")) {
      activeRunByIssue.set(r.issue_id, { kind: r.kind, status: r.status });
    }
  }
  const waiting = issues.filter(
    (i) => WAITING_STATUSES.includes(i.status) && !activeRunByIssue.has(i.id)
  );

  // Second wave: facts that depend on the first queries.
  const readyIds = waiting.filter((i) => i.status === "ready").map((i) => i.id);
  const inReviewIds = waiting
    .filter((i) => i.status === "in-review")
    .map((i) => i.id);

  const [{ data: childRows }, { data: testCases }, { data: codeRunRows }] =
    await Promise.all([
      readyIds.length
        ? supabase
            .from("issues")
            .select("parent_id")
            .in("parent_id", readyIds)
            .is("abandoned_at", null)
        : Promise.resolve({ data: [] as { parent_id: string | null }[] }),
      inReviewIds.length
        ? supabase
            .from("test_cases")
            .select("id, title, issue_id")
            .in("issue_id", inReviewIds)
            .eq("status", "active")
        : Promise.resolve({
            data: [] as { id: string; title: string; issue_id: string }[],
          }),
      // US-64.x: the succeeded code run behind an in-review row, so a clean
      // test gate can one-click "Approve" the same way /review/{id} does.
      // Deliberately only the run keyed DIRECTLY on this issue — a story
      // built inside a feature-level run has no such row (its run's issue_id
      // is the feature's), and approving that run decides every sibling
      // story at once, which is not what a single row's one click should do.
      // Those rows fall back to the review page below, same as today.
      inReviewIds.length
        ? supabase
            .from("runs")
            .select("id, issue_id, created_at")
            .eq("org_id", orgId)
            .eq("kind", "code")
            .eq("status", "succeeded")
            .in("issue_id", inReviewIds)
            .order("created_at", { ascending: false })
        : Promise.resolve({
            data: [] as { id: string; issue_id: string; created_at: string }[],
          }),
    ]);

  const caseIds = (testCases ?? []).map((tc) => tc.id);
  const { data: testResults } = caseIds.length
    ? await supabase
        .from("test_run_results")
        .select("test_case_id, result, recorded_at, test_run_id")
        .in("test_case_id", caseIds)
    : { data: [] as TestRunResultRow[] };

  const parentsWithChildren = new Set(
    (childRows ?? []).map((c) => c.parent_id).filter(Boolean) as string[]
  );

  /** US-22.10: does the feature above this story own its code build? The
   * same predicate `dispatch_issue` enforces — a story with no parent, or a
   * project in `story` mode, owns its own build. */
  function featureOwnsBuild(i: IssueRow): boolean {
    const project = Array.isArray(i.projects) ? i.projects[0] : i.projects;
    const mode = project?.build_mode ?? "story";
    return (mode === "feature" || mode === "epic") && !!i.parent_id;
  }

  function testGateFor(issueId: string) {
    const cases = (testCases ?? []).filter((tc) => tc.issue_id === issueId);
    const caseIdSet = new Set(cases.map((c) => c.id));
    const results = (testResults ?? []).filter((r) =>
      caseIdSet.has(r.test_case_id)
    );
    return computeTestGateState(cases, results);
  }

  function testGateNote(issueId: string): string {
    const gate = testGateFor(issueId);
    if (!gate.cases.length) return "no linked tests";
    if (gate.failing.length) return `${gate.failing.length} test(s) failing`;
    // us-11.4: blocked is its own state — someone looked and hit an
    // obstacle. Reporting it as failing overstates what is known.
    if (gate.blocked.length) return `${gate.blocked.length} test(s) blocked`;
    if (gate.unrun.length) return `${gate.unrun.length} test(s) unrun`;
    return "tests passing";
  }

  // US-64.x: the latest succeeded direct run per issue — `codeRunRows` is
  // ordered newest-first, so the first hit for an issue id wins.
  const codeRunIdByIssue = new Map<string, string>();
  for (const r of codeRunRows ?? []) {
    if (!codeRunIdByIssue.has(r.issue_id as string)) {
      codeRunIdByIssue.set(r.issue_id as string, r.id as string);
    }
  }

  // US-21.7: a release on UAT is waiting on a human to run its test cases and
  // sign off — the gate that unlocks production. This replaces the per-work-item
  // "QA sign-off not recorded" nudge, which asked about a thing that no longer
  // exists.
  const signoffItems: TodoItem[] = (uatReleases ?? []).map((r) => {
    const since = r.uat_deployed_at ?? new Date().toISOString();
    const name =
      (r.projects as unknown as { name: string } | null)?.name ?? "Project";
    return {
      id: r.id,
      title: `Release ${r.version}`,
      type: "release",
      displayId: r.version,
      project: name,
      projectId: r.project_id,
      reason: "On UAT — run its test cases and sign it off",
      age: formatAge(since),
      ageLevel: ageLevel(since),
      action: "Open release",
      href: `/projects/${r.project_id}/releases/${r.id}`,
      mode: "navigate" as const,
    };
  });

  const groups: TodoGroup[] = [
    {
      title: "Reviews",
      items: waiting
        .filter((i) =>
          ["prd-review", "plan-review", "in-review"].includes(i.status)
        )
        .map((i) => {
          if (i.status === "prd-review")
            return {
              ...toItem(
                i,
                "PRD drafted — needs your review",
                "Approve PRD",
                // us-12.2: PRD review lives on the shared review surface
                // now, alongside plan and code review — still the row's
                // Peek/Inspect target and the fallback for anything the
                // one-click approve itself fails on.
                `/review/${i.id}`
              ),
              peekKind: "prd" as const,
              // US-64.x: no body — approve_prd (workflow.py) already falls
              // back to whatever breakdown_mode/instructions the feature
              // carries, the same standing values the dialog on the review
              // page would have prefilled.
              mode: "approve" as const,
              approve: {
                endpoint: `/api/v1/issues/${i.id}/prd/approve`,
                thenDispatch: `/api/v1/issues/${i.id}/breakdown/dispatch`,
              },
            };
          if (i.status === "plan-review")
            return {
              ...toItem(
                i,
                // us-96.5: a bug's gate holds an RCA, and the row says so.
                i.type === "bug"
                  ? "Root cause analysis ready for your review"
                  : "Plan ready for your review",
                i.type === "bug" ? "Approve RCA" : "Approve Plan",
                `/review/${i.id}`
              ),
              peekKind: "plan" as const,
              mode: "approve" as const,
              approve: {
                endpoint: `/api/v1/issues/${i.id}/plan/approve`,
                thenDispatch: `/api/v1/issues/${i.id}/dispatch`,
              },
            };
          // US-64.x: code review merges a PR, and a gate that needs an
          // override (failing/blocked/unrun tests) requires a written reason
          // this row has no field for — one-click only when the gate is
          // clean AND the run lives directly on this issue (a story built
          // inside a feature-level run shares its PR with every sibling, so
          // approving it is a bigger decision than one row should make).
          const runId = codeRunIdByIssue.get(i.id);
          const clean = !testGateFor(i.id).needsOverride;
          const oneClick = !!runId && clean;
          return {
            ...toItem(
              i,
              `Code ready for review · ${testGateNote(i.id)}`,
              "Approve",
              `/review/${i.id}`
            ),
            peekKind: "code" as const,
            mode: oneClick ? ("approve" as const) : ("navigate" as const),
            approve: oneClick
              ? { endpoint: `/api/v1/runs/${runId}/approve` }
              : undefined,
          };
        }),
    },
    { title: "QA sign-offs", items: signoffItems },
    {
      title: "Fix & retry",
      items: waiting
        .filter((i) => ["needs-fixes", "failed"].includes(i.status))
        .map((i) =>
          i.status === "needs-fixes"
            ? toItem(
                i,
                "Rejected with feedback — send it back to the factory",
                "Re-dispatch",
                `/issues/${i.id}`,
                "redispatch",
                undefined,
                blockByIssue.get(i.id)
              )
            : toItem(
                i,
                "Run failed — inspect the log and decide",
                "Re-dispatch",
                `/issues/${i.id}`,
                "redispatch",
                `/issues/${i.id}?panel=run-log`,
                blockByIssue.get(i.id)
              )
        ),
    },
    {
      title: "Dispatch",
      items: waiting
        .filter(
          (i) =>
            // US-22.10: a `planned` story in a feature/epic-mode project is
            // built by its feature. `dispatch_issue` (migration 137) refuses
            // it, so offering it here would be the same shown-vs-enforced
            // divergence us-14.6 fixed for features. The feature's own batch
            // action is on the feature; `failed` / `needs-fixes` stay in Fix
            // & retry above, which is the exemption that keeps a stuck batch
            // recoverable.
            (i.status === "planned" && !featureOwnsBuild(i)) ||
            (i.status === "ready" &&
              !parentsWithChildren.has(i.id) &&
              // US-14.6: never offer an action the factory will refuse.
              // `dispatch_issue` (migration 104) rejects a plan run on a
              // feature outright — "a feature is not planned directly —
              // approve its PRD and break it into stories, then plan
              // those" — the us-11.2 guard. A feature sits in `ready`
              // with no children for the whole window between its PRD
              // being approved and its breakdown submitting, and this
              // filter offered "Dispatch planning" for all of it.
              i.type !== "feature")
        )
        .map((i) =>
          i.status === "planned"
            ? toItem(
                i,
                "Plan approved — ready to code",
                "Dispatch coding",
                `/issues/${i.id}`,
                "dispatch",
                undefined,
                blockByIssue.get(i.id)
              )
            : toItem(
                i,
                "Ready — send it for planning",
                "Dispatch planning",
                `/issues/${i.id}`,
                "dispatch",
                undefined,
                blockByIssue.get(i.id)
              )
        ),
    },
    {
      title: "Triage",
      items: waiting
        .filter((i) => i.status === "draft")
        .map((i) => ({
          // Only a feature's next step is a PRD; a story, bug or chore goes
          // straight to planning and never has one.
          ...toItem(
            i,
            i.type === "feature"
              ? "Draft — describe it, then draft a PRD"
              : "Draft — flesh it out or dispatch planning",
            i.type === "feature" ? "Write PRD" : "Open draft",
            `/issues/${i.id}`
          ),
          emphasis: "success" as const,
        })),
    },
  ];

  // US-36.3: nothing waiting on the manager may be invisible.
  //
  // The five groups above are each a deliberate filter, and two of them exclude
  // items on purpose: us-22.10 drops a `planned` story whose feature owns the
  // build, and us-14.6 drops a `ready` feature that already has children —
  // both because `dispatch_issue` would refuse the action. Those guards are
  // right. Implementing them by removing the item from its group was not: it
  // removed the ROW, not the button, so four real items sat waiting on the
  // manager and appeared on neither tab.
  //
  // This is a structural backstop rather than a patch to those two conditions,
  // because the trap belongs to the shape: any future status or build-mode rule
  // that filters an item out of every group would strand it the same way. If
  // the base query returned it and nothing claimed it, it still gets a row.
  // Deliberately NOT pushed into `groups`: that array feeds `waitingCount`,
  // which drives the sidebar badge and the tab title. These items have no
  // action, so counting them would inflate "N things to do" with things the
  // manager cannot do — the opposite of the honesty this page is for. They are
  // rendered, not counted.
  const claimed = new Set(groups.flatMap((g) => g.items.map((it) => it.id)));
  const stranded = waiting.filter((i) => !claimed.has(i.id));
  const strandedGroup: TodoGroup | null = stranded.length
    ? {
        title: "No action available",
        items: stranded.map((i) =>
          toItem(
            i,
            i.status === "planned" && featureOwnsBuild(i)
              ? "Planned — its feature owns the build, dispatch from there"
              : i.status === "ready" && i.type === "feature"
                ? "Ready — its stories are planned from here"
                : `${i.status} — nothing to do from this page`,
            // No action: these are exactly the items the factory would refuse,
            // so the row links to the item and promises nothing (us-14.6).
            "Open",
            `/issues/${i.id}`
          )
        ),
      }
    : null;

  const visibleGroups: TodoGroup[] = [
    ...groups.filter((g) => g.items.length > 0),
    ...(strandedGroup ? [strandedGroup] : []),
  ];

  const clarificationItems: OpenClarification[] = (openClarifications ?? []).map(
    (c) => {
      const issue = c.issues as unknown as {
        id: string;
        title: string;
        project_id: string;
        projects: { name: string } | { name: string }[] | null;
      } | null;
      const workerRel = c.workers as unknown as
        | { name: string }
        | { name: string }[]
        | null;
      const p = issue?.projects;
      return {
        id: c.id as string,
        issueId: c.issue_id as string,
        orgId: c.org_id as string,
        issueTitle: issue?.title ?? "work item",
        project: (Array.isArray(p) ? p[0]?.name : p?.name) ?? "",
        projectId: issue?.project_id ?? "",
        worker:
          (Array.isArray(workerRel) ? workerRel[0]?.name : workerRel?.name) ??
          "worker",
        question: c.question as string,
        // US-14.9: the choices the agent offered, if any.
        options: (c.options as
          | { label: string; description?: string }[]
          | null) ?? null,
        multiSelect: !!c.multi_select,
        age: formatAge(c.asked_at as string),
      };
    }
  );

  // US-59.7: split into "needs your input" and informational at render time
  // (the card's own decision, not this loader's) — here we just shape every
  // row. A `stopped` run without a captured session has nothing to resume
  // into and is dropped rather than shown as a dead end.
  const parkedRuns: ParkedRun[] = (parkedRunRows ?? [])
    .filter(
      (r) =>
        r.status !== "stopped" || (r.claude_session_id as string | null)
    )
    .map((r) => {
      const issue = r.issues as unknown as { title: string } | null;
      const p = r.projects as unknown as { name: string } | null;
      return {
        id: r.id as string,
        issueId: (r.issue_id as string | null) ?? null,
        issueTitle: issue?.title ?? "work item",
        project: p?.name ?? "",
        projectId: r.project_id as string,
        kind: r.kind as string,
        status: r.status as string,
        reason:
          (r.status === "stopped"
            ? (r.stopped_reason as string | null)
            : (r.resume_reason as string | null)) ?? null,
        age: r.resume_state_at
          ? formatAgeAgo(r.resume_state_at as string)
          : "—",
        resumeAttempts: (r.resume_attempts as number | null) ?? 0,
        canResume: r.status === "stopped",
      };
    });

  // US-5.32: pending guideline recommendations, sorted in the group component;
  // before-text comes from the target section's current content.
  const recommendationItems: GuidelineRecommendation[] = (
    pendingRecommendations ?? []
  ).map((r) => {
    const p = r.projects as unknown as
      | { name: string }
      | { name: string }[]
      | null;
    const w = r.workers as unknown as
      | { name: string }
      | { name: string }[]
      | null;
    const section = r.project_guidelines as unknown as
      | { content: string }
      | { content: string }[]
      | null;
    return {
      id: r.id as string,
      project: (Array.isArray(p) ? p[0]?.name : p?.name) ?? "",
      projectId: r.project_id as string,
      worker: (Array.isArray(w) ? w[0]?.name : w?.name) ?? "worker",
      severity: r.severity as string,
      sectionTitle: r.section_title as string,
      newSection: r.section_id === null,
      currentText:
        (Array.isArray(section) ? section[0]?.content : section?.content) ?? "",
      proposedText: r.proposed_text as string,
      rationale: r.rationale as string,
      age: formatAge(r.created_at as string),
    };
  });

  // US-43.3: one card per open refresh. The pending count is what the
  // manager has left to decide, which is not the same as the number of
  // sections the agent proposed — a half-finished review says so.
  const refreshItems: GuidelineRefresh[] = (openRefreshes ?? []).map((r) => {
    const p = r.projects as unknown as
      | { name: string }
      | { name: string }[]
      | null;
    const w = r.workers as unknown as
      | { name: string }
      | { name: string }[]
      | null;
    const recs = (r.guideline_recommendations ?? []) as unknown as {
      id: string;
      status: string;
    }[];
    return {
      id: r.id as string,
      project: (Array.isArray(p) ? p[0]?.name : p?.name) ?? "",
      projectId: r.project_id as string,
      worker: (Array.isArray(w) ? w[0]?.name : w?.name) ?? "",
      summary: (r.summary as string) ?? "",
      pendingSections: recs.filter((x) => x.status === "pending").length,
      totalSections: recs.length,
      // A refresh is `pending` from the moment it is dispatched, which covers
      // BOTH "the agent has not handed back yet" and "waiting on you". Only
      // the second is a review. Without this the card rendered an in-flight
      // run as "0 sections to review" with a live button.
      ready: recs.length > 0,
      // Already carries "ago" where it reads right — "just now ago" was on
      // the card before this.
      age: formatAgeAgo(r.created_at as string),
    };
  });

  // US-24.1: the feature each story belongs to, so Waiting on you can nest
  // them the way Work items does. Resolved once here, over the issues the
  // dashboard has already loaded plus a lookup for any parent that is not
  // itself in a waiting state (a feature at `ready` while its stories are
  // dispatched is the normal case).
  const parentIdByIssue = new Map<string, string>();
  for (const i of issues) {
    if (i.parent_id) parentIdByIssue.set(i.id, i.parent_id);
  }
  const parentIds = [...new Set(parentIdByIssue.values())];
  const parentById = new Map<string, ParentFeatureRef>();
  if (parentIds.length) {
    const { data: parentRows } = await supabase
      .from("issues")
      .select("id, title, type, item_no, sub_no, epics(number)")
      .in("id", parentIds);
    for (const row of parentRows ?? []) {
      const r = row as unknown as IssueRow;
      parentById.set(r.id, {
        id: r.id,
        displayId: issueDisplayId(r),
        title: r.title,
      });
    }
  }
  // US-25.2 → US-84.1: which of those features could be cleared in one
  // action, at any gate the factory has a batch mechanism for. The question
  // is about ALL of a feature's children, not just the ones this dashboard
  // loaded — `issues` above is filtered to waiting/agent statuses, so a
  // sibling sitting at `done` or `draft` is invisible to it and would make
  // a half-ready feature look unanimous. Hence a second, narrow read.
  if (parentIds.length) {
    const { data: siblingRows } = await supabase
      .from("issues")
      .select("parent_id, status")
      .in("parent_id", parentIds)
      .is("abandoned_at", null);
    const statusesByParent = new Map<string, string[]>();
    for (const row of (siblingRows ?? []) as {
      parent_id: string | null;
      status: string;
    }[]) {
      if (!row.parent_id) continue;
      const list = statusesByParent.get(row.parent_id) ?? [];
      list.push(row.status);
      statusesByParent.set(row.parent_id, list);
    }
    for (const [id, feature] of parentById) {
      // Unanimous, and more than one — "Curate all 1 stories" is just the
      // row's own action wearing a hat. deriveBatchGate enforces both.
      feature.batchGate = deriveBatchGate(statusesByParent.get(id) ?? []);
    }
  }

  function parentOf(issueId: string): ParentFeatureRef | null {
    const pid = parentIdByIssue.get(issueId);
    return (pid && parentById.get(pid)) || null;
  }
  for (const g of visibleGroups) {
    for (const item of g.items) item.parent = parentOf(item.id);
  }

  // us-96.7: the feature is the triage unit — parented items across every
  // actionable group collapse into one synthesized feature row (the
  // stranded group deliberately keeps per-run rows: it lists what nothing
  // has claimed, which is a fact about runs, not a decision about work).
  // Mutates the group arrays in place, so `waitingCount` below counts the
  // rolled units and stays in step with org_pending_count (migration 259).
  rollupFeatureRows(groups);

  // Guideline recommendations count as items waiting on the manager.
  // US-43.3: a refresh counts ONCE, not once per section it proposed — the
  // manager has one review to open, and counting twenty would make a single
  // action look like twenty and swamp every other thing on the page.
  const waitingCount =
    groups.reduce((n, g) => n + g.items.length, 0) +
    recommendationItems.length +
    // Running refreshes are shown, but they are not waiting on anyone.
    refreshItems.filter((r) => r.ready).length;
  const pendingCount = waitingCount + clarificationItems.length;

  return {
    // US-36.3: what the page renders — the actionable groups plus anything
    // nothing claimed. `waitingCount` above deliberately counts only the
    // actionable ones.
    // Re-filtered: the rollup above can empty a group whose only rows were
    // children now carried by a feature row in an earlier group.
    groups: visibleGroups.filter((g) => g.items.length > 0),
    clarificationItems,
    parkedRuns,
    recommendationItems,
    refreshItems,
    waitingCount,
    pendingCount,
    issues,
    activeRunByIssue,
    // US-24.2: In the factory nests the same way, over the same lookup —
    // resolved once here rather than fetched twice.
    parentOf,
  };
}

/** US-6.3: reads the two "is the factory actually moving?" signals from
 * existing tables — a queued run stalled with no online capable worker, and a
 * running claim whose worker went quiet past its lease. Mirrors the pool's own
 * rule (worker_has_grant, migration 199): project access rows are fail-closed
 * (zero rows = nothing), and the agent's kind checkboxes bound the kinds. */
export type RunLiveness = {
  runId: string;
  /** US-91.3: the claiming worker, so one batched `runner_config` read can
   *  say which of them run the `interactive` module (and therefore have a
   *  CLI window worth linking). */
  workerId: string | null;
  workerName: string;
  /** US-35.5: the agent behind the claim, so the row can link to its profile.
   *  Null for a worker with no principal (nothing links, the name still shows). */
  workerPrincipalId: string | null;
  runningMinutes: number;
  silentMinutes: number;
  isSilent: boolean;
};

/** US-35.5: who could take a queued item.
 *
 * The factory does not pre-assign work — a queued run sits in a pool and any
 * eligible agent claims it first-come. So this is deliberately the eligible
 * SET, never a prediction of which one will win. `blockedReason` is set only
 * when the set is empty, and says which condition failed. */
export type Eligibility = {
  agents: { principalId: string | null; name: string }[];
  blockedReason: string | null;
};

async function loadFactoryHealth(
  supabase: DB,
  orgId: string
): Promise<{
  staleByIssue: Map<string, number>;
  livenessByIssue: Map<string, RunLiveness>;
  stalledQueue: StalledQueue | null;
  eligibilityByIssue: Map<string, Eligibility>;
}> {
  const now = Date.now();
  const onlineCutoff = new Date(
    now - WORKER_ONLINE_MINUTES * 60_000
  ).toISOString();
  const queueCutoff = new Date(now - QUEUE_STALL_MINUTES * 60_000).toISOString();

  // US-9.7 follow-up: these three relied on RLS alone (no org_id filter),
  // which permits every org the caller belongs to, not just the active one —
  // a multi-org manager's stalled-queue badge and claim-eligibility reasoning
  // could mix in another org's queued runs and online agents.
  const [{ data: runningRuns }, { data: queuedRuns }, { data: onlineWorkers }] =
    await Promise.all([
      supabase
        .from("runs")
        .select(
          "id, issue_id, worker_id, claimed_at, claim_expires_at, last_heartbeat_at, workers(name, type, principal_id)"
        )
        .eq("org_id", orgId)
        .eq("status", "running")
        .not("worker_id", "is", null),
      // US-35.5: every queued run, not only the ones already past the stall
      // threshold — each row wants to say who can take it, not just whether it
      // has been waiting too long. The stall count is derived from this below.
      supabase
        .from("runs")
        .select(
          "id, kind, created_at, issue_id, issues!runs_issue_org_fk!inner(project_id)"
        )
        .eq("org_id", orgId)
        .eq("status", "queued"),
      supabase
        .from("workers")
        .select(
          "id, name, principal_id, runner_config!runner_config_worker_id_fkey(enabled_kinds)"
        )
        .eq("org_id", orgId)
        .eq("status", "active")
        .gt("last_seen_at", onlineCutoff),
    ]);

  // US-6.3 + US-13.6: every live claim gets a liveness reading — how long
  // it has run and how long since the worker last spoke. "Silent" means
  // materially longer than the worker's heartbeat cadence (autonomous
  // leases are 15 minutes; human claims heartbeat on every tool call), or
  // a lease already expired and awaiting the sweep. A healthy long run
  // heartbeats and never trips this.
  const staleByIssue = new Map<string, number>();
  const livenessByIssue = new Map<string, RunLiveness>();
  for (const r of runningRuns ?? []) {
    type W = { name: string; type: string; principal_id: string | null };
    const worker = r.workers as unknown as W | W[] | null;
    const w = Array.isArray(worker) ? worker[0] : worker;
    const claimedAt = r.claimed_at
      ? new Date(r.claimed_at as string).getTime()
      : now;
    const lastHeard = r.last_heartbeat_at
      ? new Date(r.last_heartbeat_at as string).getTime()
      : claimedAt;
    const runningMinutes = Math.max(0, Math.floor((now - claimedAt) / 60_000));
    const silentMinutes = Math.max(0, Math.floor((now - lastHeard) / 60_000));
    const leaseLapsed =
      r.claim_expires_at !== null &&
      new Date(r.claim_expires_at as string).getTime() < now;
    const silentThreshold = w?.type === "human" ? 30 : 20;
    const liveness: RunLiveness = {
      runId: r.id as string,
      workerId: (r.worker_id as string | null) ?? null,
      workerName: w?.name ?? "",
      workerPrincipalId: w?.principal_id ?? null,
      runningMinutes,
      silentMinutes,
      isSilent: leaseLapsed || silentMinutes >= silentThreshold,
    };
    const prev = livenessByIssue.get(r.issue_id as string);
    if (!prev || silentMinutes > prev.silentMinutes)
      livenessByIssue.set(r.issue_id as string, liveness);
    if (leaseLapsed) {
      const mins = Math.floor(
        (now - new Date(r.claim_expires_at as string).getTime()) / 60_000
      );
      const prevStale = staleByIssue.get(r.issue_id as string);
      if (prevStale === undefined || mins > prevStale)
        staleByIssue.set(r.issue_id as string, mins);
    }
  }

  // Which online agents can claim what. US-13.10: grants are per-kind rows —
  // a queued breakdown run needs an online agent holding `breakdown` on that
  // project.
  //
  // US-35.5 / US-31.3: **an agent with no grants can claim nothing.** This read
  // used to treat a grantless agent as unrestricted ("can claim anything"),
  // which was true before us-31.3 made the pool gate fail-closed and false
  // after it. The effect was backwards in the worst way: one grantless agent
  // online — exactly the agent that can take nothing — suppressed the stall
  // warning for the whole org. The rule here now mirrors `db.worker_idle_reason`
  // and the pool, because two surfaces disagreeing about who can work is the
  // defect this story exists to remove.
  const online = (onlineWorkers ?? []) as unknown as {
    id: string;
    name: string;
    principal_id: string | null;
    runner_config:
      | { enabled_kinds: string[] | null }
      | { enabled_kinds: string[] | null }[]
      | null;
  }[];
  // US-53.4: the agent's kind checkboxes bound what it may claim — null (or
  // no config row) means every kind. Mirrors the pool's own predicate in
  // db.list_worker_pool, for the same two-surfaces-agree reason as above.
  const enabledKindsByWorker = new Map<string, string[] | null>();
  for (const w of online) {
    const rc = Array.isArray(w.runner_config)
      ? w.runner_config[0]
      : w.runner_config;
    enabledKindsByWorker.set(w.id, rc?.enabled_kinds ?? null);
  }
  // US-55.1: a worker_capabilities row means project ACCESS (the per-kind
  // matrix is retired; migration 199). Mirrors worker_has_grant: access to
  // the project AND the agent's own kind checkboxes allow the raw run kind —
  // no kind→capability mapping any more, kinds are first-class.
  const accessByWorker = new Map<string, Set<string>>(); // worker -> projects
  if (online.length) {
    const { data: caps } = await supabase
      .from("worker_capabilities")
      .select("worker_id, project_id")
      .in(
        "worker_id",
        online.map((w) => w.id)
      );
    for (const c of caps ?? []) {
      const key = c.worker_id as string;
      const set = accessByWorker.get(key) ?? new Set<string>();
      set.add(c.project_id as string);
      accessByWorker.set(key, set);
    }
  }

  const eligibleFor = (kind: string, projectId: string) => {
    return online.filter((w) => {
      if (!accessByWorker.get(w.id)?.has(projectId)) return false;
      const kinds = enabledKindsByWorker.get(w.id);
      return kinds === null || kinds === undefined || kinds.includes(kind);
    });
  };

  const eligibilityByIssue = new Map<string, Eligibility>();
  let count = 0;
  let oldestMinutes = 0;
  for (const r of queuedRuns ?? []) {
    const rel = r.issues as unknown as
      | { project_id: string }
      | { project_id: string }[]
      | null;
    const projectId =
      (Array.isArray(rel) ? rel[0]?.project_id : rel?.project_id) ?? "";
    const kind = r.kind as string;
    const agents = eligibleFor(kind, projectId);
    const issueId = r.issue_id as string | null;
    if (issueId) {
      eligibilityByIssue.set(issueId, {
        agents: agents.map((w) => ({
          principalId: w.principal_id,
          name: w.name,
        })),
        blockedReason: agents.length
          ? null
          : online.length === 0
            ? "no agent is online"
            : online.some((w) => accessByWorker.get(w.id)?.has(projectId))
              ? `every online agent with access to this project has '${kind}' unchecked in its settings`
              : "no online agent has access to this project",
      });
    }
    if (agents.length) continue;
    // Only count toward the stall warning once it has actually been waiting —
    // a run queued ten seconds ago with nobody yet online is not a stall.
    if ((r.created_at as string) >= queueCutoff) continue;
    count += 1;
    const mins = Math.floor(
      (now - new Date(r.created_at as string).getTime()) / 60_000
    );
    if (mins > oldestMinutes) oldestMinutes = mins;
  }

  return {
    staleByIssue,
    livenessByIssue,
    stalledQueue: count > 0 ? { count, oldestMinutes } : null,
    eligibilityByIssue,
  };
}

/** The full page load: waiting side + the ambient context cards (Releases, In
 * the factory, Completed) + factory-health signals. Reuses the issue rows
 * already fetched by loadWaiting. */
export async function loadThingsToDo(
  supabase: DB,
  orgId: string
): Promise<ThingsToDoData> {
  // US-87.1: through the request cache, so a second reader in the same render
  // (the shell, a sibling card) shares this execution instead of repeating
  // eight org-wide queries.
  const waiting = await getWaiting(orgId);

  // US-13.6: run-death events from the last 48h — lease expiries, worker
  // give-ups and failures — so "came back later and nothing happened" is
  // distinguishable from "still going" without reading a terminal.
  const incidentCutoff = new Date(Date.now() - 48 * 3600_000).toISOString();

  // US-91.19: the deploy-in-flight, finished-run and released-version queries
  // that fed the retired Completed and Releases tabs are gone with them.
  // Deleting a tab while still paying for its data would be the worst of both.
  const [{ data: incidentEvents }, { data: dismissedRows }, health] =
    await Promise.all([
      supabase
        .from("issue_events")
        .select(
          "id, issue_id, type, payload, created_at, issues!inner(id, title, status, org_id, project_id, abandoned_at, projects!inner(name, archived_at))"
        )
        .eq("org_id", orgId)
        .in("type", ["claim-expired", "run-released", "run-failed"])
        .gt("created_at", incidentCutoff)
        .order("created_at", { ascending: false })
        .limit(25),
      // US-15.18: incidents the org has acknowledged — filtered out below.
      supabase
        .from("dashboard_incident_dismissals")
        .select("event_id")
        .eq("org_id", orgId),
      loadFactoryHealth(supabase, orgId),
    ]);

  // Latest death per issue, skipping issues that have since moved on to a
  // healthy in-flight or completed state (a requeued run being worked is
  // not an incident any more — but a queued one still is, since it died).
  const seenIncidentIssues = new Set<string>();
  // US-15.18: incidents the org has cleared never render. Keyed by the
  // issue_events.id, so a *newer* death on the same issue (a new id) reappears.
  const dismissedEventIds = new Set(
    (dismissedRows ?? []).map((d) => d.event_id as string)
  );
  const incidents: IncidentRow[] = [];
  for (const e of incidentEvents ?? []) {
    if (dismissedEventIds.has(e.id as string)) continue;
    const issueRel = e.issues as unknown as
      | {
          id: string;
          title: string;
          status: string;
          org_id: string;
          project_id: string;
          abandoned_at: string | null;
          projects: { name: string } | { name: string }[] | null;
        }
      | null;
    if (!issueRel || issueRel.abandoned_at) continue;
    if (seenIncidentIssues.has(issueRel.id)) continue;
    seenIncidentIssues.add(issueRel.id);
    if (["planning", "running", "merged", "done", "in-review"].includes(issueRel.status))
      continue;
    const payload = (e.payload ?? {}) as {
      note?: string;
      worker?: string;
      error?: string;
      held_minutes?: number;
      kind?: string;
    };
    const who = payload.worker ? `${payload.worker} ` : "";
    const reason =
      e.type === "claim-expired"
        ? `${who}went silent — ${
            payload.held_minutes
              ? `lease expired after ${payload.held_minutes} min`
              : "lease expired"
          }; requeued`
        : e.type === "run-released"
          ? `${who}handed it back${payload.note ? `: ${payload.note}` : ""}`
          : `run failed${payload.error ? `: ${payload.error}` : ""}`;
    const proj = issueRel.projects;
    incidents.push({
      id: e.id as unknown as string,
      issueId: issueRel.id,
      title: issueRel.title,
      projectId: issueRel.project_id,
      orgId: issueRel.org_id,
      project: (Array.isArray(proj) ? proj[0]?.name : proj?.name) ?? "",
      kind: e.type as string,
      runKind: payload.kind ?? null,
      worker: payload.worker ?? null,
      reason,
      age: formatAge(e.created_at as string),
    });
  }

  const inFlightIssueIds = waiting.issues
    .filter(
      (i) =>
        AGENT_STATUSES.includes(i.status) ||
        waiting.activeRunByIssue.has(i.id)
    )
    .map((i) => i.id);

  // US-13.7: the latest progress note per in-flight item — the richest
  // signal in the system, shown instead of the words "Progress note".
  const noteByIssue = new Map<string, string>();
  if (inFlightIssueIds.length) {
    const { data: noteEvents } = await supabase
      .from("issue_events")
      .select("issue_id, payload, created_at")
      .eq("type", "progress-note")
      .in("issue_id", inFlightIssueIds)
      .order("created_at", { ascending: false })
      .limit(50);
    for (const e of noteEvents ?? []) {
      const note = (e.payload as { note?: string } | null)?.note;
      if (note && !noteByIssue.has(e.issue_id as string))
        noteByIssue.set(e.issue_id as string, note);
    }
  }

  // US-39.4: why a queued run is not being picked up. The reason already
  // existed and was already enforced — `run_hold_reason` is the SAME function
  // `claim_run` refuses a claim with — and `/factory-queue` already showed it.
  // This tab, built by us-35.5 to answer exactly this question, never read it,
  // so a manager watching "Queued" was guessing at capacity or cost while the
  // database held a sentence: "paused: story US-1.1.2 needs your attention".
  //
  // Its own query rather than the activeRunByIssue map above, which covers only
  // prd/breakdown runs — the run this was reported against was a `plan`, and
  // reusing that map would have silently missed exactly the case it came from.
  // The RPC is the one `/factory-queue` calls: a second implementation would be
  // a second thing to keep in step with the claim gate, and the two disagreeing
  // is worse than neither existing.
  const holdByIssue = new Map<string, string>();
  {
    const { data: queuedRuns } = await supabase
      .from("runs")
      .select("id, issue_id, org_id")
      .eq("status", "queued")
      .not("issue_id", "is", null);
    const rows = (queuedRuns ?? []) as {
      id: string;
      issue_id: string;
      org_id: string;
    }[];
    if (rows.length) {
      const issueByRun = new Map(rows.map((r) => [r.id, r.issue_id]));
      for (const org of new Set(rows.map((r) => r.org_id))) {
        const { data: holds } = await supabase.rpc("org_queue_hold_reasons", {
          p_org: org,
        });
        for (const h of (holds ?? []) as {
          run_id: string;
          reason: string | null;
        }[]) {
          const issueId = h.reason ? issueByRun.get(h.run_id) : undefined;
          if (issueId) holdByIssue.set(issueId, h.reason as string);
        }
      }
    }
  }

  const agentItems: AgentItem[] = waiting.issues
    .filter(
      (i) =>
        AGENT_STATUSES.includes(i.status) ||
        waiting.activeRunByIssue.has(i.id)
    )
    .map((i) => {
      const live = health.livenessByIssue.get(i.id);
      return {
        id: i.id,
        title: i.title,
        projectId: i.project_id,
        project: projectName(i),
        status: i.status,
        type: i.type,
        displayId: issueDisplayId(i),
        // US-24.2: nest under the feature that owns it, like Work items.
        parent: waiting.parentOf(i.id),
        // US-15.6: the tracked PRD/breakdown run, when that's why this row is
        // here (its issues.status is a waiting state, not an AGENT_STATUS).
        activeRun: waiting.activeRunByIssue.get(i.id),
        staleMinutes: health.staleByIssue.get(i.id),
        holdReason: holdByIssue.get(i.id),
        runId: live?.runId,
        workerName: live?.workerName,
        workerPrincipalId: live?.workerPrincipalId ?? null,
        // Only meaningful while nothing holds a claim on it.
        eligible: live ? undefined : health.eligibilityByIssue.get(i.id),
        runningMinutes: live?.runningMinutes,
        silentMinutes: live?.silentMinutes,
        isSilent: live?.isSilent,
        lastNote: noteByIssue.get(i.id),
      };
    });

  // Per-project waiting counts for the filter chips, from the unfiltered
  // groups + recommendations so a chip's count is independent of the filter.
  const projectCounts = new Map<string, { name: string; count: number }>();
  const bump = (projectId: string, name: string) => {
    const entry = projectCounts.get(projectId) ?? { name, count: 0 };
    entry.count += 1;
    projectCounts.set(projectId, entry);
  };
  for (const g of waiting.groups) {
    for (const item of g.items) bump(item.projectId, item.project);
  }
  for (const rec of waiting.recommendationItems) {
    bump(rec.projectId, rec.project);
  }
  const projects: ProjectChip[] = [...projectCounts.entries()]
    .map(([id, { name, count }]) => ({ id, name, waitingCount: count }))
    .sort((a, b) => a.name.localeCompare(b.name));

  // US-37.3: a budget that has stopped work is a condition of the project, and
  // the manager should learn it from the page they already watch rather than by
  // pressing Dispatch and being told no, one item at a time.
  //
  // The comparison is `spent >= budget`, which is migration 164's trigger
  // exactly. If the two ever drift, the trigger is the authority and this
  // banner is lying — so they are the same expression, via `budgetState`.
  const exhaustedBudgets: ExhaustedBudget[] = [];
  const { data: budgeted } = await supabase
    .from("projects")
    .select("id, name, org_id, budget_enabled, budget_usd")
    .eq("org_id", orgId)
    .eq("budget_enabled", true)
    .is("archived_at", null);
  const budgetedRows = (budgeted ?? []) as {
    id: string;
    name: string;
    org_id: string;
    budget_enabled: boolean | null;
    budget_usd: number | null;
  }[];
  if (budgetedRows.length) {
    // One grouped read per org (normally one), not one per project — the same
    // rule us-37.4 holds the projects list to.
    const spentById = new Map<string, number>();
    for (const org of new Set(budgetedRows.map((p) => p.org_id))) {
      const { data: rows } = await supabase.rpc("org_project_spend", {
        p_org: org,
      });
      for (const r of (rows ?? []) as {
        project_id: string;
        spent_usd: number | null;
      }[]) {
        spentById.set(r.project_id, Number(r.spent_usd ?? 0));
      }
    }
    for (const p of budgetedRows) {
      const state = budgetState(p, {
        spent: spentById.get(p.id) ?? 0,
        unmeasured: 0,
      });
      if (state.exhausted && state.budget != null) {
        exhaustedBudgets.push({
          id: p.id,
          name: p.name,
          spent: state.spent,
          budget: state.budget,
        });
      }
    }
  }
  exhaustedBudgets.sort((a, b) => a.name.localeCompare(b.name));

  // US-86.2: a feature-owned build's telemetry lives on the FEATURE's run,
  // which no story row can find. Resolve it once per parent feature so the
  // factory tab's header can carry the run's truth.
  const featureRuns: Record<string, FeatureRunInfo> = {};
  for (const i of agentItems) {
    const p = i.parent;
    if (!p || featureRuns[p.id]) continue;
    const live = health.livenessByIssue.get(p.id);
    if (live) {
      featureRuns[p.id] = {
        runId: live.runId,
        workerName: live.workerName,
        workerPrincipalId: live.workerPrincipalId,
        runningMinutes: live.runningMinutes,
        silentMinutes: live.silentMinutes,
        isSilent: live.isSilent,
      };
    }
  }

  // US-91.3: which of the claiming agents have a CLI window worth linking.
  // One `runner_config` read for the workers actually holding a claim — the
  // roster's own condition (`enabled_modules` contains `interactive`), never
  // a query per row.
  const interactiveByPrincipal: Record<string, boolean> = {};
  const principalByWorker = new Map<string, string>();
  for (const live of health.livenessByIssue.values()) {
    if (live.workerId && live.workerPrincipalId)
      principalByWorker.set(live.workerId, live.workerPrincipalId);
  }
  if (principalByWorker.size) {
    const { data: configs } = await supabase
      .from("runner_config")
      .select("worker_id, enabled_modules")
      .in("worker_id", [...principalByWorker.keys()]);
    for (const c of (configs ?? []) as {
      worker_id: string;
      enabled_modules: string[] | null;
    }[]) {
      const pid = principalByWorker.get(c.worker_id);
      if (pid && (c.enabled_modules ?? []).includes("interactive"))
        interactiveByPrincipal[pid] = true;
    }
  }


  // US-91.18: merged work that is sitting unreleased. Three org-scoped reads,
  // none of them per project and none of them touching GitHub.
  const releaseSuggestions: ReleaseSuggestion[] = [];
  {
    const MERGED_CAP = 500;
    const [
      { data: relRows },
      { data: projRows },
      { data: mergedRows },
      { data: prepRows },
    ] = await Promise.all([
        supabase
          .from("releases")
          .select("id, project_id, version, status, released_at, created_at, included_items")
          .eq("org_id", orgId)
          // us-103.4: `notes-ready`, `deploying` and `uat-deploy-failed` were
          // missing, so a release sitting in any of them showed no card at
          // all — invisible in exactly the states that need a manager.
          .in("status", [
            "released",
            "queued",
            "running",
            "notes-ready",
            "deploying",
            "uat-deployed",
            "uat-deploy-failed",
            "uat-signed-off",
            "promoting",
          ]),
        supabase
          .from("projects")
          .select("id, name, release_uat_deployment_id")
          .eq("org_id", orgId)
          .is("archived_at", null),
        supabase
          .from("issues")
          .select(
            "id, title, type, item_no, sub_no, project_id, status_changed_at, epics(number)"
          )
          .eq("org_id", orgId)
          .eq("status", "merged")
          .is("abandoned_at", null)
          .order("status_changed_at", { ascending: false })
          .limit(MERGED_CAP),
        // us-103.4: the liveness the card never had. `release_prep_runs`
        // carries worker_id, claimed_at and claim_expires_at — everything the
        // run liveness pass reads, under different table cover. Org-scoped
        // rather than trusting RLS alone: the US-9.7 lesson is that RLS
        // permits every org the manager belongs to, not just the active one.
        supabase
          .from("release_prep_runs")
          .select(
            "id, release_id, status, claimed_at, claim_expires_at, " +
              "workers!release_prep_runs_worker_id_fkey(name, principal_id)"
          )
          .eq("org_id", orgId)
          .in("status", ["queued", "running"]),
      ]);

    type Rel = {
      id: string;
      project_id: string;
      version: string;
      status: string;
      released_at: string | null;
      created_at: string;
      included_items: unknown;
    };
    const rels = (relRows ?? []) as Rel[];

    // us-103.4: the prep row per release, reshaped for `prepLiveness`. A
    // `queued` row is a job nobody has taken; `running` is one an agent
    // holds — and whether it is still breathing is what the card could not
    // say for two and a half hours on 2026-08-16.
    type PrepRowShape = {
      release_id: string;
      status: string;
      claimed_at: string | null;
      claim_expires_at: string | null;
      workers: { name: string; principal_id: string | null }
        | { name: string; principal_id: string | null }[]
        | null;
    };
    const prepByRelease = new Map<string, PrepRow>();
    for (const row of (prepRows ?? []) as unknown as PrepRowShape[]) {
      const w = Array.isArray(row.workers) ? row.workers[0] : row.workers;
      prepByRelease.set(row.release_id, {
        workerName: w?.name ?? "",
        workerPrincipalId: w?.principal_id ?? null,
        claimedAt: row.status === "running" ? row.claimed_at : null,
        claimExpiresAt: row.claim_expires_at,
      });
    }
    // The last release that actually shipped, per project.
    const shipped = new Map<string, Rel>();
    for (const r of rels) {
      if (r.status !== "released") continue;
      const prev = shipped.get(r.project_id);
      if (!prev || (r.released_at ?? "") > (prev.released_at ?? ""))
        shipped.set(r.project_id, r);
    }
    const inFlight = new Map<string, Rel>();
    for (const r of rels) {
      if (r.status === "released") continue;
      if (!inFlight.has(r.project_id)) inFlight.set(r.project_id, r);
    }

    const merged = (mergedRows ?? []) as unknown as IssueRow[] &
      { status_changed_at: string | null; project_id: string }[];
    const cappedOverall = (mergedRows ?? []).length >= MERGED_CAP;

    for (const proj of (projRows ?? []) as {
      id: string;
      name: string;
      release_uat_deployment_id: string | null;
    }[]) {
      const last = shipped.get(proj.id) ?? null;
      const cutoff = last?.released_at ?? null;
      const mine = merged.filter(
        (i) =>
          i.project_id === proj.id &&
          (!cutoff || (i.status_changed_at ?? "") > cutoff)
      );
      // AC5 (amended during UAT 2026-08-14): a release already in flight IS
      // the card — its version, live status, and its own included_items
      // snapshot — never a prompt to cut another. Other blockers keep the
      // chip-and-no-button shape.
      const flightRel = inFlight.get(proj.id);
      // us-103.4: an in-flight release is shown even when nothing new has
      // merged behind it. This `continue` used to fire first, and release
      // 2026.08.16.3 carried zero items — so the Workbench showed no card at
      // all for the very release that was stuck.
      if (!mine.length && !flightRel) continue;

      let flight: ReleaseSuggestion["flight"] = null;
      if (flightRel) {
        const snapshot = (
          Array.isArray(flightRel.included_items) ? flightRel.included_items : []
        ) as { issue_id: string; title: string; type: string; display_id: string | null }[];
        const carried = new Set(snapshot.map((x) => x.issue_id));
        // us-103.4: only the notes leg has an agent. `deploying`, `uat-deployed`,
        // `uat-signed-off` and `promoting` are pipelines or people, and the
        // card must not imply an agent is sitting on them.
        const prep = prepByRelease.get(flightRel.id) ?? null;
        flight = {
          id: flightRel.id,
          version: flightRel.version,
          status: flightRel.status,
          items: snapshot.slice(0, 3).map((x) => ({
            id: x.issue_id,
            displayId: x.display_id ?? null,
            title: x.title,
            type: x.type,
          })),
          total: snapshot.length,
          extraMerged: mine.filter((i) => !carried.has(i.id)).length,
          liveness: AGENT_HELD.has(flightRel.status)
            ? prepLiveness(prep, flightRel.created_at, Date.now())
            : null,
        };
      }
      const blocker =
        !flight && !proj.release_uat_deployment_id
          ? "no UAT deployment is designated for releases"
          : null;

      releaseSuggestions.push({
        projectId: proj.id,
        project: proj.name,
        sinceVersion: last?.version ?? null,
        items: mine.slice(0, 3).map((i) => ({
          id: i.id,
          displayId: issueDisplayId(i),
          title: i.title,
          type: i.type,
        })),
        total: mine.length,
        capped: cappedOverall,
        blocker,
        flight,
      });
    }
    releaseSuggestions.sort((a, b) => a.project.localeCompare(b.project));
  }

  return {
    ...waiting,
    releaseSuggestions,
    agentItems,
    featureRuns,
    interactiveByPrincipal,
    stalledQueue: health.stalledQueue,
    incidents,
    projects,
    exhaustedBudgets,
  };
}

// ---------------------------------------------------------------------------
// US-87.1 / US-87.2: the shell's two entry points.
// ---------------------------------------------------------------------------

/** US-87.1: the Things-to-Do dataset, once per request. `/workbench` renders
 * inside the app shell, and both used to call `loadWaiting` — eight org-wide
 * queries, run twice for one page. `React.cache` is per-render-pass, so the
 * two share one execution and two different requests never do. */
export const getWaiting = cache(
  async (orgId: string): Promise<WaitingData> => {
    const supabase = await getServerClient();
    return loadWaiting(supabase as unknown as DB, orgId);
  }
);

/** US-87.2: the shell badge — a count, not a dataset.
 *
 * `org_pending_count` (migration 249) mirrors `loadWaiting`'s `pendingCount`
 * group for group. THE TWO ARE ONE DEFINITION IN TWO PLACES and must be
 * changed together; the badge disagreeing with the page header it links to is
 * a worse bug than a slow badge, which is exactly why the RPC carries the
 * same comment pointing back here.
 *
 * A page that already needs the whole dataset (Things to Do) should read
 * `getWaiting(orgId).pendingCount` instead — same number, no extra round
 * trip, since the dataset is already in the request cache. */
export const getPendingCount = cache(async (orgId: string): Promise<number> => {
  const supabase = await getServerClient();
  const { data, error } = await supabase.rpc("org_pending_count", {
    p_org: orgId,
  });
  if (error) {
    // A badge that cannot be counted shows nothing rather than a wrong
    // number, and never takes the page down with it.
    console.error("org_pending_count failed", error.message);
    return 0;
  }
  return typeof data === "number" ? data : 0;
});
