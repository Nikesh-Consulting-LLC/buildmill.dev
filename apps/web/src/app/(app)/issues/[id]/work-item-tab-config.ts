/** US-15.20: each work-item type gets its own tab set.
 *
 * Kept in a plain module (not the "use client" tab shell) so the server page
 * can resolve the opening tab during render and the client shell can validate
 * navigation against the same list — one definition, no drift. */

export type WorkItemTab =
  | "overview"
  | "prd"
  | "stories"
  | "wireframe"
  | "plan"
  | "documents"
  | "discussion"
  | "history"
  | "release";

export const TAB_LABELS: Record<WorkItemTab, string> = {
  overview: "Overview",
  prd: "PRD",
  stories: "Stories",
  wireframe: "Wireframe",
  plan: "Plan",
  documents: "Documents",
  discussion: "Discussion",
  history: "History",
  release: "Release",
};

/** US-49.4: the first tab is named for the item, because that is what it
 * holds — the requirement a PRD is dispatched against, or the story an agent
 * is given. "Overview" named a summary of it.
 *
 * The *id* stays `overview`, so every `?tab=overview` link ever emitted still
 * resolves; only the label is a function of type. */
const FIRST_TAB_LABELS: Record<string, string> = {
  feature: "Feature",
  story: "Story",
  bug: "Bug",
  chore: "Chore",
};

export function tabLabel(tab: WorkItemTab, type: string): string {
  if (tab === "overview") return FIRST_TAB_LABELS[type] ?? TAB_LABELS.overview;
  return TAB_LABELS[tab];
}

/** A feature's PRD sits directly after its own tab and it has no plan; every
 * other type has a plan and no PRD of its own. Release is appended only when
 * the item actually has a release record.
 *
 * US-48.2: Wireframe sits BEFORE Plan, because that is the order the work
 * happens in — the screen is drawn, then planned against. A feature has no
 * wireframe of its own: its screens are its stories' screens.
 *
 * US-49.4: Stories follows PRD for the same reason — the requirement is
 * written, the PRD is approved, the stories come out of it. Only a feature
 * has children to list. */
export function tabsForType(
  type: string,
  { hasRelease }: { hasRelease: boolean }
): WorkItemTab[] {
  const middle: WorkItemTab[] =
    type === "feature" ? ["prd", "stories"] : ["wireframe", "plan"];
  return [
    "overview",
    ...middle,
    "documents",
    "discussion",
    "history",
    ...((hasRelease ? ["release"] : []) as WorkItemTab[]),
  ];
}

/** US-49.5/49.6: tab ids that no longer exist, and where their content went.
 * A deep link should reach the content rather than the fallback — `?tab=runs`
 * is still in the dashboard's links and in people's history, and the
 * instruction set is now edited beside the run it redirects. */
const RETIRED_TABS: Record<string, WorkItemTab> = {
  runs: "history",
  instructions: "history",
};

export function normalizeTab(value?: string | null): WorkItemTab | undefined {
  if (!value) return undefined;
  return (RETIRED_TABS[value] ?? value) as WorkItemTab;
}

/** US-15.19's retired `?panel=` deep links (the dashboard and the review page
 * still emit them) select a tab.
 *
 * US-49.4: `stories` pointed at `overview` only because the stories lived
 * there. They have a tab again, so the PRD review's post-approval redirect
 * lands on the list instead of leaving the manager to find it. */
export const LEGACY_PANEL_TO_TAB: Record<string, WorkItemTab> = {
  prd: "prd",
  wireframe: "wireframe",
  stories: "stories",
  instructions: "history",
  documents: "documents",
  plan: "plan",
  "run-log": "history",
  comments: "discussion",
  timeline: "history",
  decisions: "history",
  release: "release",
};

/** The tab a request opens on. Anything unavailable for this type — a stale
 * `?tab=plan` on a feature, say — falls back to Overview rather than rendering
 * an empty body. A merged item that has a release opens on Release. */
export function resolveDefaultTab({
  tab,
  panel,
  available,
  status,
  hasRelease,
}: {
  tab?: string;
  panel?: string;
  available: WorkItemTab[];
  status: string;
  hasRelease: boolean;
}): WorkItemTab {
  const asked =
    normalizeTab(tab) ?? (panel ? LEGACY_PANEL_TO_TAB[panel] : undefined);
  if (asked && available.includes(asked)) return asked;
  if (hasRelease && ["merged", "done"].includes(status)) return "release";
  return "overview";
}
