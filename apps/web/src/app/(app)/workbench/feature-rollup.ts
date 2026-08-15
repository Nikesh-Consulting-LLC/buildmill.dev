import type { BatchGate } from "@/lib/batch-gate";
import type { TodoGroup, TodoItem } from "./data";

/** us-96.7: the Workbench triages the feature, not its stories.
 *
 * After us-96.4 the feature is the only steering wheel for its children, so
 * listing five child rows offers five decisions where there is one. This
 * collapses every parented item across ALL waiting groups into a single
 * synthesized feature row, placed where the first child sat. The children
 * never render; what needs the manager inside the batch is *named* on the
 * row (`rollup.attention`) rather than expanded into rows.
 *
 * Pure and in-place: called once by `loadWaiting` after parents attach and
 * BEFORE `pendingCount` is computed, so the badge, the header and the list
 * count the same units — the pact `org_pending_count` (migrations 249/259)
 * mirrors in SQL.
 */

const AGE_RANK: Record<TodoItem["ageLevel"], number> = {
  normal: 0,
  warn: 1,
  bad: 2,
};

/** What N children sitting in a given group are waiting for, singular and
 * plural. Groups this map doesn't know fall back to their own lowercased
 * title, so a new group degrades to legible rather than to wrong. */
const GROUP_PHRASE: Record<string, [string, string]> = {
  Reviews: ["awaits your review", "await your review"],
  Dispatch: ["to dispatch", "to dispatch"],
  "Fix & retry": ["needs attention", "need attention"],
  Triage: ["to curate", "to curate"],
};

export type FeatureRollup = {
  /** Non-abandoned children currently waiting, across every group. */
  count: number;
  /** Children that need the manager by name — trouble and hard blocks. */
  attention: { id: string; label: string }[];
  /** US-84.1's unanimous one-click gate, when every child agrees. */
  batchGate: BatchGate | null;
};

export function rollupFeatureRows(groups: TodoGroup[]): void {
  type Child = { item: TodoItem; group: string };
  type Cluster = {
    parent: NonNullable<TodoItem["parent"]>;
    children: Child[];
    firstGroup: number;
    firstIndex: number;
  };

  const clusters = new Map<string, Cluster>();
  groups.forEach((g, gi) => {
    g.items.forEach((item, ii) => {
      const p = item.parent;
      if (!p) return;
      let c = clusters.get(p.id);
      if (!c) {
        c = { parent: p, children: [], firstGroup: gi, firstIndex: ii };
        clusters.set(p.id, c);
      }
      c.children.push({ item, group: g.title });
    });
  });
  if (clusters.size === 0) return;

  // Children collapse; and a feature's own flat row goes too — the
  // synthesized row replaces it, the way US-24.1's header did (the same
  // FEAT-x.y twice in one list was a live bug on 2026-08-13).
  const featureIds = new Set(clusters.keys());
  for (const g of groups) {
    g.items = g.items.filter((i) => !i.parent && !featureIds.has(i.id));
  }

  for (const c of clusters.values()) {
    const byGroup = new Map<string, number>();
    for (const ch of c.children) {
      byGroup.set(ch.group, (byGroup.get(ch.group) ?? 0) + 1);
    }
    const line = [...byGroup]
      .map(([title, n]) => {
        const phrase = GROUP_PHRASE[title] ?? [
          title.toLowerCase(),
          title.toLowerCase(),
        ];
        return `${n} ${n === 1 ? phrase[0] : phrase[1]}`;
      })
      .join(" · ");

    // The row wears the WORST wait in the batch — a fresh review must not
    // repaint three days of a stuck sibling as new.
    const oldest = c.children.reduce((a, b) =>
      AGE_RANK[b.item.ageLevel] > AGE_RANK[a.item.ageLevel] ? b : a
    );

    const attention = c.children
      .filter((ch) => ch.group === "Fix & retry" || ch.item.blocked?.hard)
      .map((ch) => ({
        id: ch.item.id,
        label: ch.item.displayId ?? ch.item.title,
      }));

    const first = c.children[0].item;
    const count = c.children.length;
    const row: TodoItem = {
      id: c.parent.id,
      title: c.parent.title,
      type: "feature",
      displayId: c.parent.displayId,
      project: first.project,
      projectId: first.projectId,
      reason: `${count} stor${count === 1 ? "y" : "ies"} waiting — ${line}`,
      age: oldest.item.age,
      ageLevel: oldest.item.ageLevel,
      action: "Open feature",
      href: `/issues/${c.parent.id}?from=workbench`,
      mode: "navigate",
      rollup: {
        count,
        attention,
        batchGate: c.parent.batchGate ?? null,
      },
    };
    const target = groups[c.firstGroup].items;
    target.splice(Math.min(c.firstIndex, target.length), 0, row);
  }
}
