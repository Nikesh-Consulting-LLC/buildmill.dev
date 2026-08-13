import { cookies } from "next/headers";

// One filter, persisted once, read by every page — the alternative (each
// page remembering its own selection in localStorage, as Work Items and
// Reports used to) meant picking a project on one page said nothing about
// any other. A cookie (not localStorage) is what lets server components
// resolve the selection during their own data fetch, same as any other
// page, with no client-side flash of unfiltered data.
export const GLOBAL_PROJECTS_COOKIE = "sf_projects";

/** `null` means "all" — the unset default, not an empty selection. */
export async function readGlobalProjectIds(): Promise<string[] | null> {
  const store = await cookies();
  const raw = store.get(GLOBAL_PROJECTS_COOKIE)?.value;
  if (!raw) return null;
  return raw.split(",").filter(Boolean);
}

/** Resolves the stored ids against a page's own project list — a project
 * deleted, or not visible on this page's org, silently drops out rather
 * than leaving the filter pointed at nothing. An empty valid selection
 * (everything deselected) is respected: that is "show nothing", not "show
 * all". */
export function resolveGlobalSelection(
  all: { id: string }[],
  stored: string[] | null
): Set<string> {
  if (stored === null) return new Set(all.map((p) => p.id));
  const known = new Set(all.map((p) => p.id));
  return new Set(stored.filter((id) => known.has(id)));
}
