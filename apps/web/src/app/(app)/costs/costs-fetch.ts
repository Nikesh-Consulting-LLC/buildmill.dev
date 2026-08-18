// us-119.3: the one request the Costs page makes, and the rule that keeps a
// slow answer from painting over a fast one. Pure — no fetch, no React — so
// both halves are testable in Node.

import type { CostsInitial } from "./costs-url";

/** The API path for a slice: the same four parameters `/spend`,
 * `/spend-trend` and `/work-summary` took, sent once. Unlike the page URL
 * (`costsParamsFor`), nothing is omitted here — the API's defaults are not
 * the page's (`/costs` defaults `days` to 30; the page to 7), so every value
 * is explicit. */
export function costsPath(orgId: string, state: CostsInitial): string {
  const qs = new URLSearchParams({
    group_by: state.groupBy,
    days: String(state.days),
  });
  if (state.projectId) qs.set("project_id", state.projectId);
  if (state.workerId) qs.set("worker_id", state.workerId);
  if (state.itemType) qs.set("item_type", state.itemType);
  return `/api/v1/llm/orgs/${orgId}/costs?${qs}`;
}

/** Latest-wins. Every request takes a ticket; a response is applied only if
 * its ticket is still the newest one issued. Two clicks — a slow 30-day
 * slice, then a fast 7-day one — would otherwise land in the wrong order
 * and leave the 30-day figures on a screen whose controls say 7. Aborting
 * the earlier fetch is the courtesy; this gate is the guarantee, because an
 * abort is best-effort and a response already in flight can still arrive. */
export function createLatestGate(): {
  next: () => number;
  isLatest: (ticket: number) => boolean;
} {
  let latest = 0;
  return {
    next: () => ++latest,
    // Tickets start at 1; nothing is "latest" before the first is issued.
    isLatest: (ticket: number) => ticket > 0 && ticket === latest,
  };
}
