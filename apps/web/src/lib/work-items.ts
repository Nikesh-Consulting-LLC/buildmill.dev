// US-7.10: the Build-Mill-native work-item identity — an epic-scoped,
// type-prefixed id derived from the epic number and the item/sub sequence
// assigned at creation. Replaces the opaque UUID and the departed
// github_issue_number as the handle shown everywhere.

export type WorkItemType = "feature" | "story" | "bug" | "chore";

const PREFIX: Record<string, string> = {
  feature: "FEAT",
  story: "US",
  bug: "BUG",
  chore: "CHORE",
};

/** The display id, e.g. FEAT-1.4, US-1.4.1, BUG-1.5, CHORE-1.6, US-1.7.
 * Returns null when the numbering isn't available (nothing to show). */
export function workItemDisplayId(opts: {
  type: string;
  epicNumber: number | null | undefined;
  itemNo: number | null | undefined;
  subNo?: number | null | undefined;
}): string | null {
  const { type, epicNumber, itemNo, subNo } = opts;
  if (epicNumber == null || itemNo == null) return null;
  const prefix = PREFIX[type] ?? "US";
  const tail =
    subNo != null
      ? `${epicNumber}.${itemNo}.${subNo}`
      : `${epicNumber}.${itemNo}`;
  return `${prefix}-${tail}`;
}

/** US-74.1: the sequence order behind the display id — US-3.1.1, US-3.1.2,
 * … US-3.1.10. Compares the numbers, so it never falls into the lexicographic
 * trap that puts .10 above .2. Unnumbered items sort last, then by title.
 *
 * Every surface that nests a feature's stories sorts with this, so the list
 * reads the same in the Outline, the Table and the feature page. */
export function compareWorkItemSequence(
  a: { item_no?: number | null; sub_no?: number | null; title?: string | null },
  b: { item_no?: number | null; sub_no?: number | null; title?: string | null },
): number {
  const last = Number.MAX_SAFE_INTEGER;
  return (
    (a.item_no ?? last) - (b.item_no ?? last) ||
    (a.sub_no ?? last) - (b.sub_no ?? last) ||
    (a.title ?? "").localeCompare(b.title ?? "")
  );
}

/** "Epic <n> · <title>" — the epic's display label. */
export function epicLabel(
  epicNumber: number | null | undefined,
  title: string | null | undefined,
): string {
  const t = title ?? "";
  return epicNumber != null ? `Epic ${epicNumber} · ${t}` : t;
}

// US-8.1: a deterministic, theme-safe color per project id, reused by every
// Work Items lens (Outline spines, Board accents, Table spines) so a project
// reads the same everywhere. Tailwind can't emit dynamic classes, so callers
// apply this via inline `style`.
export function projectColor(projectId: string): string {
  let hash = 0;
  for (let i = 0; i < projectId.length; i++) {
    hash = (hash * 31 + projectId.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  // Mid saturation/lightness reads on both light and dark backgrounds.
  return `hsl(${hue} 58% 48%)`;
}
