import type { SupabaseClient } from "@supabase/supabase-js";
import { workItemDisplayId } from "@/lib/work-items";
import { activityPhrase } from "@/lib/run-activity";

/* eslint-disable @typescript-eslint/no-explicit-any */
type DB = SupabaseClient<any, "public", any>;

/** US-15.2: every state a queue row can be in, in the manager's language.
 * `blocked-on-you` is a running run whose last activity was a clarification
 * — the run holds its claim but is waiting on the manager, not working.
 * `held` is the us-15.3 sibling-approval gate. */
export type QueueRunState =
  | "running"
  | "blocked-on-you"
  | "queued"
  | "paused"
  | "held";

export type QueueItem = {
  /** run id — draggable identity, and the RPC key for reorder/pause. */
  id: string;
  kind: string;
  /** the DB row's raw status ('queued' | 'running') — reorder/pause only
   * ever act on 'queued' rows; running rows are never draggable. */
  status: "queued" | "running";
  projectId: string;
  projectName: string;
  issueId: string | null;
  issueTitle: string | null;
  displayId: string | null;
  epic: string | null;
  /** US-15.11: raw numbering behind displayId, so the client can auto-sort a
   * project's queued rows by epic → item → sub without re-parsing the label. */
  epicNumber: number | null;
  itemNo: number | null;
  subNo: number | null;
  state: QueueRunState;
  /** us-15.3: why a held row is held. */
  heldReason: string | null;
  /** US-24.2: the feature that owns this run's issue, for nesting. */
  parent: { id: string; displayId: string | null; title: string } | null;
  createdAt: string;
  /** running only: who holds it, and the us-14.8 activity phrase. */
  workerName: string | null;
  activity: string | null;
  silentMinutes: number | null;
};

export type QueueGroup = {
  projectId: string;
  projectName: string;
  /** US-17.5: the project's task-processing governance, shown on the queue. */
  buildMode: string;
  autoApprove: { prd: boolean; plan: boolean; code: boolean };
  items: QueueItem[];
};

type RunRow = {
  id: string;
  kind: string;
  status: string;
  queue_rank: number | null;
  paused_at: string | null;
  created_at: string;
  claimed_at: string | null;
  issue_id: string | null;
  project_id: string;
  issues: {
    id: string;
    title: string;
    type: string;
    item_no: number | null;
    sub_no: number | null;
    parent_id: string | null;
    epics: { number: number; title: string } | { number: number; title: string }[] | null;
  } | null;
  projects:
    | ProjectMode
    | ProjectMode[]
    | null;
  workers: { name: string } | { name: string }[] | null;
};

type ProjectMode = {
  id: string;
  name: string;
  build_mode: string | null;
  auto_approve_prd: boolean | null;
  auto_approve_plan: boolean | null;
  auto_approve_code: boolean | null;
};

function one<T>(v: T | T[] | null): T | null {
  return Array.isArray(v) ? (v[0] ?? null) : v;
}

/** US-15.2: the whole factory queue — every queued and running run for the
 * org, grouped by project, in the order workers will pull it. Mirrors
 * `db.list_factory_queue` (the API/MCP read) but as a direct Supabase read,
 * consistent with how the rest of Things to Do is built ("Build less API"). */
export async function loadFactoryQueue(
  supabase: DB,
  orgId: string
): Promise<QueueGroup[]> {
  const { data: runRows, error: runError } = await supabase
    .from("runs")
    .select(
      "id, kind, status, queue_rank, paused_at, created_at, claimed_at, issue_id, project_id, " +
        "issues!runs_issue_org_fk(id, title, type, item_no, sub_no, parent_id, epics(number, title)), " +
        "projects!inner(id, name, archived_at, build_mode, auto_approve_prd, auto_approve_plan, auto_approve_code), " +
        "workers(name)"
    )
    .eq("org_id", orgId)
    .in("status", ["queued", "running"])
    .is("projects.archived_at", null);
  if (runError) console.error("loadFactoryQueue runs query failed:", runError);

  const runs = (runRows ?? []) as unknown as RunRow[];

  // US-17.5: the held state + its reason come from the SAME function the pool
  // enforces (run_hold_reason), via org_queue_hold_reasons — so what the manager
  // sees held (us-15.3 sibling-curation, and feature/epic build-mode phase
  // batches) is exactly what a worker is refused. No shown-vs-enforced drift.
  const holdReasonByRun = new Map<string, string>();
  const { data: holds } = await supabase.rpc("org_queue_hold_reasons", {
    p_org: orgId,
  });
  for (const h of (holds ?? []) as { run_id: string; reason: string | null }[]) {
    if (h.reason) holdReasonByRun.set(h.run_id, h.reason);
  }

  // US-14.8: the newest tool call per running run, for the activity phrase
  // and the blocked-on-you substate (same signal the work-item page uses).
  const runningIds = runs.filter((r) => r.status === "running").map((r) => r.id);
  const lastActivityByRun = new Map<string, { tool: string; at: string }>();
  if (runningIds.length) {
    const { data: activity } = await supabase
      .from("run_activity")
      .select("run_id, tool, at, id")
      .in("run_id", runningIds)
      .order("at", { ascending: false })
      .order("id", { ascending: false });
    for (const a of activity ?? []) {
      const rid = a.run_id as string;
      if (!lastActivityByRun.has(rid)) {
        lastActivityByRun.set(rid, { tool: a.tool as string, at: a.at as string });
      }
    }
  }

  // US-24.2: the feature each queued story belongs to, so the queue can nest
  // them. Without this the hold reasons ("waiting: story US-1.1.1 ahead of
  // this one") name a sibling that is nowhere near them in a flat list.
  const parentIds = [
    ...new Set(
      runs
        .map((r) => one(r.issues)?.parent_id)
        .filter((id): id is string => !!id)
    ),
  ];
  const parentById = new Map<
    string,
    { id: string; displayId: string | null; title: string }
  >();
  if (parentIds.length) {
    const { data: parentRows } = await supabase
      .from("issues")
      .select("id, title, type, item_no, sub_no, epics(number)")
      .in("id", parentIds);
    for (const row of parentRows ?? []) {
      const pr = row as unknown as {
        id: string;
        title: string;
        type: string;
        item_no: number | null;
        sub_no: number | null;
        epics: { number: number } | { number: number }[] | null;
      };
      parentById.set(pr.id, {
        id: pr.id,
        title: pr.title,
        displayId: workItemDisplayId({
          type: pr.type,
          epicNumber: one(pr.epics)?.number ?? null,
          itemNo: pr.item_no,
          subNo: pr.sub_no,
        }),
      });
    }
  }

  const now = Date.now();
  const items: QueueItem[] = runs.map((r) => {
    const issue = one(r.issues);
    const project = one(r.projects);
    const worker = one(r.workers);
    const epic = issue?.epics ? one(issue.epics) : null;
    const holdReason = holdReasonByRun.get(r.id) ?? null;
    const held = holdReason !== null;

    let state: QueueRunState;
    let workerName: string | null = null;
    let activity: string | null = null;
    let silentMinutes: number | null = null;
    if (r.status === "running") {
      const last = lastActivityByRun.get(r.id);
      const isClarification = last?.tool === "request_clarification";
      state = isClarification ? "blocked-on-you" : "running";
      workerName = worker?.name ?? null;
      activity = last ? activityPhrase(last.tool) : null;
      silentMinutes = last
        ? Math.max(0, Math.floor((now - new Date(last.at).getTime()) / 60_000))
        : null;
    } else if (r.paused_at) {
      state = "paused";
    } else if (held) {
      state = "held";
    } else {
      state = "queued";
    }

    return {
      id: r.id,
      kind: r.kind,
      status: r.status as "queued" | "running",
      projectId: r.project_id,
      projectName: project?.name ?? "",
      issueId: issue?.id ?? null,
      issueTitle: issue?.title ?? null,
      displayId: issue
        ? workItemDisplayId({
            type: issue.type,
            epicNumber: epic?.number ?? null,
            itemNo: issue.item_no,
            subNo: issue.sub_no,
          })
        : null,
      epic: epic ? `Epic ${epic.number} · ${epic.title}` : null,
      epicNumber: epic?.number ?? null,
      itemNo: issue?.item_no ?? null,
      subNo: issue?.sub_no ?? null,
      state,
      heldReason: state === "held" ? holdReason : null,
      parent: (issue?.parent_id && parentById.get(issue.parent_id)) || null,
      createdAt: r.created_at,
      workerName,
      activity,
      silentMinutes,
    };
  });

  // Pull order: within a project, running first (already past the queue),
  // then the manager's rank (unranked to the back by age) — the exact order
  // list_worker_pool / list_factory_queue use, so "what's shown" IS "what's
  // next".
  const rankByRun = new Map(runs.map((r) => [r.id, r.queue_rank]));
  const createdByRun = new Map(runs.map((r) => [r.id, r.created_at]));
  items.sort((a, b) => {
    if (a.projectName !== b.projectName)
      return a.projectName.localeCompare(b.projectName);
    const aRunning = a.status === "running" ? 0 : 1;
    const bRunning = b.status === "running" ? 0 : 1;
    if (aRunning !== bRunning) return aRunning - bRunning;
    const aRank = rankByRun.get(a.id);
    const bRank = rankByRun.get(b.id);
    if (aRank !== bRank) {
      if (aRank == null) return 1;
      if (bRank == null) return -1;
      return aRank - bRank;
    }
    return (createdByRun.get(a.id) ?? "").localeCompare(createdByRun.get(b.id) ?? "");
  });

  // US-17.5: each project's task-processing governance, for the queue header.
  const modeByProject = new Map<string, ProjectMode>();
  for (const r of runs) {
    const p = one(r.projects);
    if (p && !modeByProject.has(p.id)) modeByProject.set(p.id, p);
  }

  const groups = new Map<string, QueueGroup>();
  for (const item of items) {
    const pm = modeByProject.get(item.projectId);
    const g = groups.get(item.projectId) ?? {
      projectId: item.projectId,
      projectName: item.projectName,
      buildMode: pm?.build_mode ?? "story",
      autoApprove: {
        prd: !!pm?.auto_approve_prd,
        plan: !!pm?.auto_approve_plan,
        code: !!pm?.auto_approve_code,
      },
      items: [],
    };
    g.items.push(item);
    groups.set(item.projectId, g);
  }
  return [...groups.values()].sort((a, b) =>
    a.projectName.localeCompare(b.projectName)
  );
}
