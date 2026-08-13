// apps/web/src/lib/progress-timing.ts
//
// US-87.11: how long the global progress bar stays up.
//
// Phase 87 made navigation fast enough that the bar stopped being readable —
// it appeared and vanished inside ~150 ms, which the eye registers as nothing
// happening at all. The fix is a MINIMUM visible duration rather than the
// usual "delay before showing": a delay would suppress the signal on exactly
// the fast operations the manager said felt unacknowledged.
//
// Pure timing arithmetic, kept out of the component so it can be tested
// without a browser or a fake clock — it is the only part of this that a unit
// test can prove.

/** How long the bar must remain visible once it has appeared. Long enough to
 * read as a deliberate signal, short enough not to feel like latency. */
export const PROGRESS_MIN_VISIBLE_MS = 400;

/** How long the completed (full-width) bar takes to fade out. */
export const PROGRESS_FADE_MS = 300;

/**
 * How much longer the bar must stay up after the work finished.
 *
 * `0` means the work outlived the minimum and the bar can complete
 * immediately. Never negative, and never confused by a clock that appears to
 * go backwards (a paused tab, a system clock adjustment) — that yields the
 * full hold rather than a negative wait, because showing the signal slightly
 * too long is harmless and skipping it is the bug being fixed.
 */
export function remainingHoldMs(
  startedAt: number,
  finishedAt: number,
  minVisibleMs: number = PROGRESS_MIN_VISIBLE_MS
): number {
  const shown = finishedAt - startedAt;
  if (!Number.isFinite(shown) || shown < 0) return minVisibleMs;
  return Math.max(0, minVisibleMs - shown);
}
