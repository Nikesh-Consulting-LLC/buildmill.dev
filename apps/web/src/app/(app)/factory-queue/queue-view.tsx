"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  AlertTriangle,
  Bot,
  GripVertical,
  ListOrdered,
  Loader2,
  Pause,
  Play,
  UserCheck,
  Workflow,
  Zap,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import { RUN_KIND_LABELS, type RunKind } from "@/lib/run-kinds";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState } from "@/components/empty-state";
import { CancelRunDialog } from "@/components/cancel-run-dialog";
import { IDLE_LABELS, idleTone, type IdleReason } from "../team/team-view";
import type { QueueGroup, QueueItem, QueueRunState } from "./data";

/** US-57.14: an agent the org is counting on to pick up queued work, but
 * which cannot right now — everything `worker_idle_reason` calls a condition
 * rather than the healthy `working`/`idle` pair. Scoped to `type = 'autonomous'`
 * workers only: a human worker sitting "idle" is not a factory problem. */
type StuckAgent = {
  principalId: string;
  name: string;
  reason: string;
  detail?: string;
};

function useStuckAgents(orgId: string): StuckAgent[] {
  const [stuck, setStuck] = useState<StuckAgent[]>([]);
  useEffect(() => {
    if (!orgId) return;
    let cancelled = false;
    async function load() {
      const supabase = createClient();
      const [{ data: workers }, reasonsBody] = await Promise.all([
        supabase
          .from("workers")
          .select("id, name, principal_id, type")
          .eq("org_id", orgId)
          .eq("type", "autonomous"),
        apiFetch(`/api/v1/agents/idle-reasons?org=${orgId}`).catch(() => null),
      ]);
      if (cancelled) return;
      const reasons = (reasonsBody?.reasons ?? {}) as Record<string, IdleReason>;
      const out: StuckAgent[] = [];
      for (const w of workers ?? []) {
        const idle = reasons[w.id as string];
        if (!idle || idle.reason === "working" || idle.reason === "idle") continue;
        out.push({
          principalId: (w.principal_id as string | null) ?? (w.id as string),
          name: (w.name as string) || "an agent",
          reason: idle.reason,
          detail: idle.detail,
        });
      }
      setStuck(out);
    }
    // Best-effort, same as Team's own fetch of this endpoint: a stuck-agent
    // banner is a hint, not load-bearing data the queue depends on to render.
    load().catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [orgId]);
  return stuck;
}

function StuckAgentsBanner({ agents }: { agents: StuckAgent[] }) {
  if (!agents.length) return null;
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-900 dark:bg-amber-950">
      <div className="flex items-center gap-2 font-medium text-amber-900 dark:text-amber-200">
        <AlertTriangle className="size-4 shrink-0" />
        {agents.length === 1
          ? "1 agent can't work right now — it needs fixing"
          : `${agents.length} agents can't work right now — they need fixing`}
      </div>
      <ul className="flex flex-col gap-1 pl-6 text-xs text-amber-800 dark:text-amber-300">
        {agents.map((a) => (
          <li key={a.principalId}>
            <Link href={`/team/${a.principalId}`} className="font-medium hover:underline">
              {a.name}
            </Link>
            {" — "}
            <span className={idleTone(a.reason)}>
              {IDLE_LABELS[a.reason] ?? a.reason}
            </span>
            {a.detail && <span className="text-amber-700/80 dark:text-amber-400/80"> · {a.detail}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** US-15.2: state pill styling, the same visual language as the stage
 * tracker (blue = the factory is working, amber = waiting on you, muted =
 * not yet, dashed = deliberately held back). */
const STATE_META: Record<
  QueueRunState,
  { label: string; className: string }
> = {
  running: {
    label: "Running",
    className: "bg-blue-600 text-white dark:bg-blue-600",
  },
  "blocked-on-you": {
    label: "Blocked — waiting on you",
    className: "bg-amber-400 text-amber-950 dark:bg-amber-500",
  },
  queued: {
    label: "Queued",
    className: "bg-muted text-muted-foreground",
  },
  paused: {
    label: "Paused",
    className:
      "border border-dashed bg-transparent text-muted-foreground",
  },
  held: {
    label: "Held",
    className:
      "border border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  },
};

function runKindLabel(kind: string): string {
  return RUN_KIND_LABELS[kind as RunKind] ?? kind;
}

/** US-15.11: the order a project's queued rows would take sorted by work-item
 * number — epic → item → sub ascending, with rows that have no resolvable
 * number sent to the end, most-recently-created first. Only status==='queued'
 * rows have an editable order (the same carve-out drag respects; running rows
 * are already claimed). Returns run ids in the target order. */
function orderedByWorkItemNumber(items: QueueItem[]): string[] {
  const queued = items.filter((i) => i.status === "queued");
  const resolvable = queued.filter((i) => i.displayId != null);
  const unresolvable = queued.filter((i) => i.displayId == null);
  resolvable.sort(
    (a, b) =>
      (a.epicNumber ?? 0) - (b.epicNumber ?? 0) ||
      (a.itemNo ?? 0) - (b.itemNo ?? 0) ||
      (a.subNo ?? 0) - (b.subNo ?? 0)
  );
  unresolvable.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  return [...resolvable, ...unresolvable].map((i) => i.id);
}

function sameOrder(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

function StatePill({ item }: { item: QueueItem }) {
  const meta = STATE_META[item.state];
  return (
    <Badge
      variant="outline"
      className={meta.className}
      title={item.heldReason ?? undefined}
    >
      {meta.label}
    </Badge>
  );
}

/** US-24.2: interleave a feature row above the runs whose stories share one.
 * Presentation only — every run still appears exactly once, so the group count,
 * drag-to-reorder and pause-all are untouched. A feature owning a single run in
 * this project renders flat. */
type NestedQueueRow =
  | { kind: "feature"; feature: NonNullable<QueueItem["parent"]>; count: number }
  | { kind: "item"; item: QueueItem; nested: boolean };

function nestQueueItems(items: QueueItem[]): NestedQueueRow[] {
  const counts = new Map<string, number>();
  for (const i of items) {
    if (i.parent) counts.set(i.parent.id, (counts.get(i.parent.id) ?? 0) + 1);
  }
  const seen = new Set<string>();
  const out: NestedQueueRow[] = [];
  for (const i of items) {
    const p = i.parent ?? null;
    const n = p ? (counts.get(p.id) ?? 0) : 0;
    const grouped = !!p && n > 1;
    if (p && grouped && !seen.has(p.id)) {
      seen.add(p.id);
      out.push({ kind: "feature", feature: p, count: n });
    }
    out.push({ kind: "item", item: i, nested: grouped });
  }
  return out;
}

function QueueRow({
  item,
  draggable,
  dragging,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  onTogglePause,
  busy,
}: {
  item: QueueItem;
  draggable: boolean;
  dragging: boolean;
  onDragStart?: (e: React.DragEvent) => void;
  onDragEnd?: () => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
  onTogglePause?: () => void;
  busy: boolean;
}) {
  const href = item.issueId
    ? `/issues/${item.issueId}?from=${encodeURIComponent("/factory-queue")}&fromLabel=${encodeURIComponent("Factory Queue")}`
    : null;
  const title = item.issueTitle ?? `${runKindLabel(item.kind)} run`;

  return (
    <div
      draggable={draggable}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      onDragOver={draggable ? onDragOver : undefined}
      onDrop={draggable ? onDrop : undefined}
      className={`flex flex-col gap-1.5 rounded-lg border px-3 py-2.5 transition-colors ${
        dragging ? "opacity-40" : "hover:bg-accent/40"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          {draggable && (
            <GripVertical className="mt-0.5 size-4 shrink-0 cursor-grab text-muted-foreground/50" />
          )}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              {item.epic && (
                <span className="text-xs text-muted-foreground">{item.epic}</span>
              )}
              {item.displayId && (
                <span className="font-mono text-xs text-muted-foreground">
                  {item.displayId}
                </span>
              )}
            </div>
            {href ? (
              <Link href={href} className="text-sm font-medium hover:underline">
                {title}
              </Link>
            ) : (
              <p className="text-sm font-medium">{title}</p>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant="secondary">{runKindLabel(item.kind)}</Badge>
          <StatePill item={item} />
        </div>
      </div>

      {item.heldReason && (
        <p className="text-xs text-muted-foreground">{item.heldReason}</p>
      )}

      {item.status === "running" && (
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <Bot className="size-3" />
          <span>
            {item.workerName || "worker"}
            {item.activity && ` · ${item.activity}`}
            {item.silentMinutes !== null &&
              ` · last heard ${
                item.silentMinutes === 0 ? "just now" : `${item.silentMinutes}m ago`
              }`}
          </span>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        {onTogglePause && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 px-2 text-xs"
            disabled={busy}
            onClick={onTogglePause}
            title={
              item.state === "paused"
                ? "Resume — offer this to workers again"
                : "Pause — keep its place, but don't offer it to a worker"
            }
          >
            {busy ? (
              <Loader2 className="size-3 animate-spin" />
            ) : item.state === "paused" ? (
              <Play className="size-3" />
            ) : (
              <Pause className="size-3" />
            )}
            {item.state === "paused" ? "Resume" : "Pause"}
          </Button>
        )}
        {/* US-27.10: the third option. Pause keeps a run, reset re-queues it,
            cancel ends it — the queue could do the first two and had no way
            to say "this should not have been dispatched" at all. */}
        <CancelRunDialog
          runId={item.id}
          runKind={runKindLabel(item.kind).toLowerCase()}
          running={item.status === "running"}
        />
      </div>
    </div>
  );
}

export function QueueView({
  groups,
  orgId,
}: {
  groups: QueueGroup[];
  orgId: string;
}) {
  const router = useRouter();
  const stuckAgents = useStuckAgents(orgId);
  const [draggedId, setDraggedId] = useState<string | null>(null);
  const [busyRun, setBusyRun] = useState<string | null>(null);
  const [busyReorder, setBusyReorder] = useState(false);
  // US-15.11: which project group's bulk action (sort / pause all / resume
  // all) is in flight, so that group's buttons disable together.
  const [busyGroup, setBusyGroup] = useState<string | null>(null);

  // US-15.2: live — claims, completions, reorders and pause/resume all mirror
  // onto `runs` (and a held row's release mirrors onto `issues`), so those two
  // tables are a sufficient live signal for the whole page.
  useEffect(() => {
    if (!orgId) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => router.refreshSilently(), 400);
    };

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`factory-queue-${orgId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          { event: "*", schema: "public", table: "runs", filter: `org_id=eq.${orgId}` },
          refresh
        )
        .on(
          "postgres_changes",
          { event: "*", schema: "public", table: "issues", filter: `org_id=eq.${orgId}` },
          refresh
        )
        .subscribe();
    }
    subscribe();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (channel) supabase.removeChannel(channel);
    };
  }, [orgId, router]);

  async function togglePause(item: QueueItem) {
    setBusyRun(item.id);
    const supabase = createClient();
    await supabase.rpc("set_run_paused", {
      p_run: item.id,
      p_paused: item.state !== "paused",
    });
    setBusyRun(null);
    router.refresh();
  }

  async function persistOrder(projectId: string, orderedRunIds: string[]) {
    setBusyReorder(true);
    const supabase = createClient();
    await supabase.rpc("reorder_factory_queue", {
      p_project: projectId,
      p_run_ids: orderedRunIds,
    });
    setBusyReorder(false);
    router.refresh();
  }

  // US-15.11: auto-sort a project's queued rows by work-item number, through
  // the very same reorder_factory_queue RPC drag uses — so "shown" stays
  // "enforced". A no-op (already sorted, or nothing to sort) writes nothing.
  async function sortByNumber(group: QueueGroup) {
    const current = group.items
      .filter((i) => i.status === "queued")
      .map((i) => i.id);
    const ordered = orderedByWorkItemNumber(group.items);
    if (ordered.length < 2 || sameOrder(current, ordered)) return;
    setBusyGroup(group.projectId);
    const supabase = createClient();
    await supabase.rpc("reorder_factory_queue", {
      p_project: group.projectId,
      p_run_ids: ordered,
    });
    setBusyGroup(null);
    router.refresh();
  }

  // US-15.11: pause (or resume) every row in one project group in one action,
  // reusing the per-row set_run_paused RPC — only the paused flag changes, each
  // row keeps its queue_rank. Pausing targets queued + held rows; resuming
  // targets paused rows.
  async function bulkPause(group: QueueGroup, paused: boolean) {
    const ids = group.items
      .filter((i) =>
        paused
          ? i.state === "queued" || i.state === "held"
          : i.state === "paused"
      )
      .map((i) => i.id);
    if (!ids.length) return;
    setBusyGroup(group.projectId);
    const supabase = createClient();
    await Promise.all(
      ids.map((id) =>
        supabase.rpc("set_run_paused", { p_run: id, p_paused: paused })
      )
    );
    setBusyGroup(null);
    router.refresh();
  }

  function handleDrop(group: QueueGroup, targetId: string) {
    return (e: React.DragEvent) => {
      e.preventDefault();
      const sourceId = e.dataTransfer.getData("text/plain") || draggedId;
      setDraggedId(null);
      if (!sourceId || sourceId === targetId) return;

      // Only the draggable subset (status === 'queued') has an order to
      // edit — running rows are already claimed and are never reordered.
      const draggableIds = group.items
        .filter((i) => i.status === "queued")
        .map((i) => i.id);
      const fromIdx = draggableIds.indexOf(sourceId);
      const toIdx = draggableIds.indexOf(targetId);
      if (fromIdx === -1 || toIdx === -1) return;

      const reordered = [...draggableIds];
      reordered.splice(fromIdx, 1);
      reordered.splice(toIdx, 0, sourceId);
      persistOrder(group.projectId, reordered);
    };
  }

  if (!groups.length) {
    return (
      <div className="flex flex-col gap-4">
        <StuckAgentsBanner agents={stuckAgents} />
        <EmptyState
          icon={UserCheck}
          title="Nothing in the factory"
          description="Everything is waiting on you, or done. Dispatch something from Things to Do and it will show up here."
        />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <StuckAgentsBanner agents={stuckAgents} />
      {groups.map((group) => {
        const groupBusy = busyGroup === group.projectId || busyReorder;
        const sortableCount = group.items.filter(
          (i) => i.status === "queued"
        ).length;
        const pausableCount = group.items.filter(
          (i) => i.state === "queued" || i.state === "held"
        ).length;
        const pausedCount = group.items.filter(
          (i) => i.state === "paused"
        ).length;
        return (
        <Card key={group.projectId} className="min-w-0">
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-base">
                  {group.projectName} ({group.items.length})
                </CardTitle>
                {/* US-17.5: the project's task-processing governance, in effect. */}
                {group.buildMode !== "story" && (
                  <Badge
                    variant="outline"
                    className="gap-1 text-xs capitalize"
                    title="Build mode: work is routed as a phase-batched unit"
                  >
                    <Workflow className="size-3" />
                    By {group.buildMode}
                  </Badge>
                )}
                {(group.autoApprove.prd ||
                  group.autoApprove.plan ||
                  group.autoApprove.code) && (
                  <Badge
                    variant="outline"
                    className="gap-1 border-amber-300 bg-amber-50 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
                    title="These gates clear automatically when a run is submitted — no review"
                  >
                    <Zap className="size-3" />
                    Auto-approve:{" "}
                    {[
                      group.autoApprove.prd && "PRD",
                      group.autoApprove.plan && "plan",
                      group.autoApprove.code && "code",
                    ]
                      .filter(Boolean)
                      .join(", ")}
                  </Badge>
                )}
              </div>
              {/* US-15.11: per-project bulk controls — auto-sort by work-item
                  number, pause the whole queue, resume the whole queue. */}
              <div className="flex items-center gap-1.5">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1 px-2 text-xs"
                  disabled={groupBusy || sortableCount < 2}
                  onClick={() => sortByNumber(group)}
                  title="Reorder this project's queued runs ascending by work-item number"
                >
                  <ListOrdered className="size-3" />
                  Sort by number
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1 px-2 text-xs"
                  disabled={groupBusy || pausableCount === 0}
                  onClick={() => bulkPause(group, true)}
                  title="Pause every queued and held run in this project"
                >
                  <Pause className="size-3" />
                  Pause all
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 gap-1 px-2 text-xs"
                  disabled={groupBusy || pausedCount === 0}
                  onClick={() => bulkPause(group, false)}
                  title="Resume every paused run in this project"
                >
                  <Play className="size-3" />
                  Resume all
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {nestQueueItems(group.items).map((row) => {
              if (row.kind === "feature") {
                return (
                  <div
                    key={`feat-${row.feature.id}`}
                    className="flex items-center gap-2 pt-1 text-xs text-muted-foreground"
                  >
                    {row.feature.displayId && (
                      <span className="font-mono">{row.feature.displayId}</span>
                    )}
                    <Link
                      href={`/issues/${row.feature.id}?from=dashboard`}
                      className="truncate font-medium text-foreground hover:underline"
                    >
                      {row.feature.title}
                    </Link>
                    <span>
                      · {row.count} {row.count === 1 ? "story" : "stories"}
                    </span>
                  </div>
                );
              }
              const item = row.item;
              const draggable = item.status === "queued" && !busyReorder;
              const rowEl = (
                <QueueRow
                  key={item.id}
                  item={item}
                  draggable={draggable}
                  dragging={draggedId === item.id}
                  onDragStart={(e) => {
                    setDraggedId(item.id);
                    e.dataTransfer.effectAllowed = "move";
                    e.dataTransfer.setData("text/plain", item.id);
                  }}
                  onDragEnd={() => setDraggedId(null)}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={handleDrop(group, item.id)}
                  onTogglePause={
                    item.state === "queued" || item.state === "paused"
                      ? () => togglePause(item)
                      : undefined
                  }
                  busy={busyRun === item.id}
                />
              );
              return row.nested ? (
                <div key={item.id} className="ml-1 border-l pl-3">
                  {rowEl}
                </div>
              ) : (
                rowEl
              );
            })}
          </CardContent>
        </Card>
        );
      })}
    </div>
  );
}
