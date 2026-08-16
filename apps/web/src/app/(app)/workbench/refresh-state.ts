/** us-107.2: what an open Instructions Refresh is actually doing.
 *
 * The card had two states — ready, or "An agent is reading the repository" —
 * and a refresh is `pending` from the moment it is *dispatched*, so the second
 * one was shown whether an agent had picked the job up or not. On 2026-08-16 a
 * card read "An agent is reading the repository · 6d ago" about a run
 * (`f483ee01`) that no worker had ever claimed: `worker_id`, `claimed_at` and
 * `claim_expires_at` all null since 2026-08-10.
 *
 * That wording is why it survived six days. "An agent is reading" is progress,
 * and progress is a thing you wait for — six days of it looked exactly like six
 * seconds of it. Nothing in the factory was going to catch it either:
 * `requeue_expired_claims` only matches `running` runs with an *expired* claim,
 * and a lease can only expire if it was ever taken.
 *
 * So this splits "waiting" from "working". Its own module, no React, so the
 * node runner can pin it — the same reason `triage-action.ts` sits apart.
 */

/** How long an unclaimed refresh waits before the card stops being calm about
 *  it. Deliberately short: the honest label shows immediately, and this only
 *  governs the amber treatment and whether a way out is offered. */
export const STALE_AFTER_HOURS = 1;

export type RefreshState = "ready" | "working" | "waiting" | "stalled";

export function refreshState(input: {
  /** The agent handed back and proposed sections. */
  ready: boolean;
  /** A worker holds the run — `claimed_at` is set. */
  claimed: boolean;
  /** How long since the refresh was dispatched. */
  hoursWaiting: number;
}): RefreshState {
  if (input.ready) return "ready";
  // Claimed and still working is the state the old card assumed was the only
  // one. It is legitimate, and it stays calm.
  if (input.claimed) return "working";
  return input.hoursWaiting >= STALE_AFTER_HOURS ? "stalled" : "waiting";
}

/** What the card says. Never "an agent is reading" unless one demonstrably is. */
export function refreshLabel(state: RefreshState): string {
  switch (state) {
    case "working":
      return "An agent is reading the repository";
    case "waiting":
      return "Queued — waiting for a worker to pick it up";
    case "stalled":
      return "No worker has picked this up";
    default:
      return "";
  }
}
