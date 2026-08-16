/**
 * us-107.1: when "Mark fixed" is offered on a work item.
 *
 * These are migration 278's guards, restated for the client so a surface never
 * shows a button the RPC will refuse — the shown-vs-enforced divergence us-14.6
 * fixed for dispatch, and us-106.1 avoided again for held drafts. The RPC stays
 * the authority; this only decides what to render.
 *
 * Its own module, with no React in it, so the node test runner can pin the
 * rules without dragging a component (or Supabase) in — the same reason
 * `triage-action.ts` sits apart from `data.ts`.
 */

/** Complete already — nothing left to mark. */
const TERMINAL = ["merged", "done"];

/** A worker is mid-flight. Completing the item underneath them would discard
 *  work still being written, so Abandon blocks here and so does this. */
const IN_FLIGHT = ["queued", "running"];

export function canMarkFixed(
  type: string,
  status: string,
  abandonedAt?: string | null,
): boolean {
  // A feature completes when its last story does. Marking one by hand would
  // strand its open stories under a completed parent.
  if (type === "feature") return false;
  if (TERMINAL.includes(status)) return false;
  if (IN_FLIGHT.includes(status)) return false;
  if (abandonedAt) return false;
  return true;
}

/**
 * Why it is not offered, in the words the surface should use. Null when it *is*
 * offered. Kept beside the predicate so the two cannot drift into disagreeing
 * about the same item.
 */
export function markFixedBlockedReason(
  type: string,
  status: string,
  abandonedAt?: string | null,
): string | null {
  if (type === "feature")
    return "A feature completes when its last story does — mark the stories instead.";
  if (TERMINAL.includes(status)) return "This work item is already complete.";
  if (IN_FLIGHT.includes(status))
    return "A run is queued or running — stop it before marking this fixed.";
  if (abandonedAt)
    return "This work item is abandoned — restore it before marking it fixed.";
  return null;
}
