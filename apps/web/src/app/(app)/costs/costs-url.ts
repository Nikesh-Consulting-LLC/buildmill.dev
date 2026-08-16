// us-95.4: the Costs view lives in the URL — group, window and filters as
// query params, so a slice can be bookmarked or pasted to the other admin.
// This module is the pure half: the vocabulary and the parse, importable by
// the server page, the client view, and the test runner alike.

export type CostsInitial = {
  groupBy: string;
  days: number;
  projectId: string | null;
  workerId: string | null;
  itemType: string | null;
};

export const COST_DIMENSIONS = [
  { key: "project", label: "By project" },
  { key: "agent", label: "By agent" },
  { key: "provider", label: "By provider" },
  { key: "model", label: "By model" },
  // us-95.3: the work-shaped axes.
  { key: "type", label: "By type" },
  { key: "epic", label: "By epic" },
  { key: "item", label: "By work item" },
];

export const COST_WINDOWS = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

export const ITEM_TYPES = [
  { key: "feature", label: "Features" },
  { key: "bug", label: "Bugs" },
  { key: "chore", label: "Chores" },
  { key: "story", label: "Stories" },
];

const DEFAULT_GROUP = "project";
// us-102.1: seven days, not thirty. The question this page is opened to answer
// is "what has this week cost me" — a month flattens that week into a bar four
// pixels wide and compares it against a month already stopped being thought
// about. This constant is the only place the default lives, so the parse, the
// URL round-trip and the view cannot drift apart.
const DEFAULT_DAYS = 7;

/** Landing on a URL restores the exact view (us-95.4 AC4); junk params fall
 * back to defaults rather than erroring or leaking into requests. */
export function parseCostsParams(
  params: Record<string, string | string[] | undefined>,
): CostsInitial {
  const one = (v: string | string[] | undefined) =>
    Array.isArray(v) ? v[0] : v;
  const group = one(params.group);
  const days = Number(one(params.days));
  const type = one(params.type);
  return {
    groupBy: COST_DIMENSIONS.some((d) => d.key === group)
      ? (group as string)
      : DEFAULT_GROUP,
    days: COST_WINDOWS.some((w) => w.days === days) ? days : DEFAULT_DAYS,
    projectId: one(params.project) || null,
    workerId: one(params.agent) || null,
    itemType: ITEM_TYPES.some((t) => t.key === type) ? (type as string) : null,
  };
}

/** The inverse: state back to query params, omitting defaults so the bare
 * /costs stays bare and a copied URL carries only what was chosen. */
export function costsParamsFor(state: CostsInitial): string {
  const params = new URLSearchParams();
  if (state.groupBy !== DEFAULT_GROUP) params.set("group", state.groupBy);
  if (state.days !== DEFAULT_DAYS) params.set("days", String(state.days));
  if (state.projectId) params.set("project", state.projectId);
  if (state.workerId) params.set("agent", state.workerId);
  if (state.itemType) params.set("type", state.itemType);
  return params.toString();
}
