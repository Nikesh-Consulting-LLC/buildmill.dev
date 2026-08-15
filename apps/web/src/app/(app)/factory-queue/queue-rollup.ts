import type { QueueItem, QueueRunState } from "./data";
/** us-96.7: one row per feature in the queue. The runs of stories that share
 * a parent collapse into a single feature unit — rolled-up state, the active
 * child's worker on the row, the batch's blocker when held — and the unit is
 * what the manager drags and pauses. Pure, so the view and its tests share
 * one definition. US-24.2's presentation nesting retires with it. */
export type QueueFeatureRow = {
  kind: "feature-rollup";
  feature: NonNullable<QueueItem["parent"]>;
  projectId: string;
  /** Members in their current queue order. */
  members: QueueItem[];
  /** Precedence: running > blocked-on-you > held > paused > queued. */
  state: QueueRunState;
  /** The member an agent is actually working (running or blocked-on-you). */
  active: QueueItem | null;
  /** The first held member's reason — the batch's current blocker. */
  heldReason: string | null;
  pausedCount: number;
  queuedCount: number;
  /** The drag unit is the feature — draggable only while every member is
   * still queued; a claimed member pins the block, like a running row. */
  allQueued: boolean;
};

export type QueueRowModel = { kind: "run"; item: QueueItem } | QueueFeatureRow;

const STATE_RANK: Record<QueueRunState, number> = {
  running: 4,
  "blocked-on-you": 3,
  held: 2,
  paused: 1,
  queued: 0,
};

export function rollupQueueRows(items: QueueItem[]): QueueRowModel[] {
  const clusters = new Map<string, { first: number; members: QueueItem[] }>();
  items.forEach((item, idx) => {
    if (!item.parent) return;
    const c = clusters.get(item.parent.id);
    if (c) c.members.push(item);
    else clusters.set(item.parent.id, { first: idx, members: [item] });
  });

  const out: QueueRowModel[] = [];
  const emitted = new Set<string>();
  items.forEach((item, idx) => {
    const p = item.parent;
    if (!p) {
      out.push({ kind: "run", item });
      return;
    }
    if (emitted.has(p.id)) return;
    emitted.add(p.id);
    const { members } = clusters.get(p.id)!;
    void idx;
    const state = members.reduce<QueueRunState>(
      (acc, m) => (STATE_RANK[m.state] > STATE_RANK[acc] ? m.state : acc),
      "queued"
    );
    out.push({
      kind: "feature-rollup",
      feature: p,
      projectId: item.projectId,
      members,
      state,
      active:
        members.find(
          (m) => m.state === "running" || m.state === "blocked-on-you"
        ) ?? null,
      heldReason: members.find((m) => m.state === "held")?.heldReason ?? null,
      pausedCount: members.filter((m) => m.state === "paused").length,
      queuedCount: members.filter((m) => m.state === "queued").length,
      allQueued: members.every((m) => m.status === "queued"),
    });
  });
  return out;
}
