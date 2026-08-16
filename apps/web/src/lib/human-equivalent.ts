// us-109.2: "how long would people have taken?" — an approximation, and the
// only honest way to describe it. It is derived from lines changed, because
// that is the one thing the factory measures that has a widely-cited human
// rate against it; agent wall-clock is not a comparison, it is the thing
// being compared to.
//
// The numbers are deliberately in one place, named, so a manager who
// disagrees with the rate can change it here and every surface moves
// together — rather than each page picking its own and the estimate meaning
// something different depending on where it is read.

/** Reviewed, merged lines a developer adds in an hour. Deliberately low: the
 *  well-known industry figures (~100 lines/day of production code) count the
 *  whole day — design, review, meetings, the rewrite — not just typing. */
export const HUMAN_LINES_PER_HOUR = 25;

/** A deleted line is not free — it has to be understood before it can go —
 *  but it is cheaper than a written one. */
export const REMOVED_LINE_WEIGHT = 0.5;

/**
 * Hours a person would plausibly have spent producing the same diff.
 *
 * Approximate by construction — it says nothing about whether the change was
 * hard, and a 10-line fix that took a week of thinking will read as 24
 * minutes. It answers one question only: at a normal human rate, how much
 * typing-and-reviewing has this workspace's agents got through.
 */
export function humanEquivalentHours(
  linesAdded: number,
  linesRemoved: number,
): number {
  const weighted =
    Math.max(0, linesAdded) + Math.max(0, linesRemoved) * REMOVED_LINE_WEIGHT;
  return weighted / HUMAN_LINES_PER_HOUR;
}

/** `312h`, `6.4h`, `0h` — hours, at the precision the estimate deserves.
 *  Never minutes: an approximation printed to the minute claims a precision
 *  it does not have. */
export function formatHumanHours(hours: number): string {
  if (!hours || hours <= 0) return "0h";
  if (hours < 10) return `${hours.toFixed(1)}h`;
  return `${Math.round(hours)}h`;
}

/** The same figure as working days, for the note under the tile — 300 hours
 *  is hard to feel, "38 days" is not. 8-hour days. */
export function humanEquivalentDays(hours: number): number {
  return hours / 8;
}
