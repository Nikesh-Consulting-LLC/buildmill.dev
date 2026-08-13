// apps/web/src/lib/recent-changes.ts
//
// US-87.12: which rows just changed, so the Work Items hub can say so.
//
// A live update currently arrives silently — `refreshSilently()` exists
// precisely so a realtime-fed page does not strobe the global progress bar
// forever, and us-87.11 keeps that exemption. But "do not shout" is not "say
// nothing": the right signal for a live change is local to the row that
// changed.
//
// The set logic lives here, apart from the hook, because the interesting rule
// is not "add an id" — it is what happens when twenty ids arrive at once.

/** How long a row stays marked before fading back. */
export const HIGHLIGHT_MS = 1500;

/**
 * Above this many rows marked at once, the highlight stands down.
 *
 * A batch dispatch changes every story under a feature in one go. Twenty rows
 * pulsing simultaneously is not a signal that something changed — it is the
 * whole list flashing, which is harder to read than no highlight at all, and
 * is the sort of thing that gets a feature turned off rather than tuned.
 */
export const HIGHLIGHT_STORM_LIMIT = 8;

/**
 * The set after marking `id` as just-changed.
 *
 * Returns `null` when the burst has grown past `HIGHLIGHT_STORM_LIMIT`, which
 * the caller reads as "clear everything and highlight nothing for this
 * burst". Returning a sentinel rather than an empty set keeps the two cases
 * distinguishable: "nothing is highlighted right now" and "stand down, this
 * is a storm" want different handling of the pending timers.
 */
export function nextChangedSet(
  current: ReadonlySet<string>,
  id: string
): Set<string> | null {
  if (current.size >= HIGHLIGHT_STORM_LIMIT) return null;
  const next = new Set(current);
  next.add(id);
  return next;
}
