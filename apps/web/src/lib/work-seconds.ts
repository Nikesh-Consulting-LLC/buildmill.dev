// US-91.11 / US-91.12: seconds are what the database stores; hours are a
// rendering. Everything that shows "how long an agent worked" reads
// `runs.work_seconds` through this — nothing recomputes a duration from
// timestamps, so two surfaces can never disagree about the same run.

/** `3h 40m`, `12m`, `0h` — never `3.6667`. */
export function formatWorkSeconds(seconds: number): string {
  if (!seconds || seconds < 0) return "0h";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  if (!h) return `${m}m`;
  return m ? `${h}h ${m}m` : `${h}h`;
}

/** Token counts, at the precision a glance deserves. */
export function compactTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}k`;
  return `${n}`;
}
