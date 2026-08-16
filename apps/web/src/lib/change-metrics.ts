/** Shared formatting for run change metrics (US-1.17) — the area heuristic
 * itself lives server-side (apps/api/app/metrics.py); this only reads the
 * `area` already stored per file in change_breakdown. */

export type ChangeBreakdownEntry = {
  path: string;
  added: number;
  removed: number;
  /** us-109.3: `vendored` is a dependency tree, build output, lockfile or
   *  minified bundle the changeset carried. It stays in the breakdown — it
   *  really was in the changeset — but `lines_added`/`files_changed` already
   *  exclude it, so the totals here are authored work only. */
  area: "frontend" | "backend" | "other" | "vendored";
};

export type RunMetrics = {
  lines_added: number | null;
  lines_removed: number | null;
  files_changed: number | null;
  change_breakdown: unknown;
};

export function hasMetrics(run: RunMetrics): boolean {
  return (
    run.lines_added != null &&
    run.lines_removed != null &&
    run.files_changed != null
  );
}

function areaSplit(run: RunMetrics): string {
  const breakdown = (run.change_breakdown as ChangeBreakdownEntry[] | null) ?? [];
  const counts: Record<string, number> = {
    frontend: 0,
    backend: 0,
    other: 0,
    vendored: 0,
  };
  for (const f of breakdown) counts[f.area] = (counts[f.area] ?? 0) + 1;
  // us-109.3: vendored is listed LAST and named, rather than omitted. A run
  // that swept in 7,999 dependency files is a fact about that run worth
  // seeing — silently dropping it is how one came to be counted as 1.8M
  // lines of somebody's work for a week.
  return (["frontend", "backend", "other", "vendored"] as const)
    .filter((a) => counts[a] > 0)
    .map((a) => `${counts[a]} ${a}`)
    .join(", ");
}

/** "+N −M lines · F files · frontend/backend split", or "—" if uncomputed. */
export function formatChangeSummary(run: RunMetrics): string {
  if (!hasMetrics(run)) return "—";
  const split = areaSplit(run);
  const fileWord = run.files_changed === 1 ? "file" : "files";
  return `+${run.lines_added} −${run.lines_removed} lines · ${run.files_changed} ${fileWord}${
    split ? ` · ${split}` : ""
  }`;
}

export function sumMetrics(
  runs: RunMetrics[]
): { lines_added: number; lines_removed: number; files_changed: number } | null {
  const withMetrics = runs.filter(hasMetrics);
  if (!withMetrics.length) return null;
  return withMetrics.reduce(
    (acc, r) => ({
      lines_added: acc.lines_added + (r.lines_added ?? 0),
      lines_removed: acc.lines_removed + (r.lines_removed ?? 0),
      files_changed: acc.files_changed + (r.files_changed ?? 0),
    }),
    { lines_added: 0, lines_removed: 0, files_changed: 0 }
  );
}
