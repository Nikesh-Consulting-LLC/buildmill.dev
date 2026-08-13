/** Shared formatting for run change metrics (US-1.17) — the area heuristic
 * itself lives server-side (apps/api/app/metrics.py); this only reads the
 * `area` already stored per file in change_breakdown. */

export type ChangeBreakdownEntry = {
  path: string;
  added: number;
  removed: number;
  area: "frontend" | "backend" | "other";
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
  const counts: Record<string, number> = { frontend: 0, backend: 0, other: 0 };
  for (const f of breakdown) counts[f.area] = (counts[f.area] ?? 0) + 1;
  return (["frontend", "backend", "other"] as const)
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
