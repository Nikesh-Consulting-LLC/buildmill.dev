// US-19.1: one duration format behind both the "In the factory" Elapsed column
// and the "Completed" Duration column, so the two can never drift.

/** Days/hours/minutes, coarsest two units only: "2d 4h", "1h 04m", "41m".
 * Anything under a minute reads "< 1m" rather than "0m" — a real run that
 * finished in four seconds should not look like it never ran. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  const totalMinutes = Math.floor(ms / 60_000);
  if (totalMinutes < 1) return "< 1m";

  const days = Math.floor(totalMinutes / 1440);
  const hours = Math.floor((totalMinutes % 1440) / 60);
  const minutes = totalMinutes % 60;

  if (days > 0) return hours > 0 ? `${days}d ${hours}h` : `${days}d`;
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${minutes}m`;
}

/** Same shape, for a value already counted in minutes (the liveness readings
 * in data.ts are computed that way). */
export function formatMinutes(minutes: number | null | undefined): string {
  if (minutes == null || !Number.isFinite(minutes) || minutes < 0) return "—";
  return formatDuration(minutes * 60_000);
}
