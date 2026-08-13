/** Unit tests for `remainingHoldMs` — the global progress bar's
 * minimum-visible arithmetic. Pure, so it needs no DOM and no fake clock.
 * Run with `npm run test:web`.
 *
 * US-87.11 is what these cover. Phase 87 made navigation fast enough that the
 * bar appeared and vanished inside ~150 ms, which reads as nothing happening.
 * The regression this file guards against is someone "fixing" the flash by
 * adding a delay before showing the bar — that would suppress the signal on
 * exactly the fast operations that prompted the complaint. The minimum hold
 * is the opposite trade, and these pin it.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  PROGRESS_MIN_VISIBLE_MS,
  remainingHoldMs,
} from "./progress-timing.ts";

test("a fast navigation is held to the minimum", () => {
  // The case that prompted the story: ~150 ms of real work.
  assert.equal(remainingHoldMs(1000, 1150), PROGRESS_MIN_VISIBLE_MS - 150);
});

test("an instant navigation still shows the full minimum", () => {
  assert.equal(remainingHoldMs(1000, 1000), PROGRESS_MIN_VISIBLE_MS);
});

test("work that outlives the minimum completes immediately", () => {
  assert.equal(remainingHoldMs(1000, 5000), 0);
});

test("work exactly at the minimum completes immediately", () => {
  assert.equal(remainingHoldMs(1000, 1000 + PROGRESS_MIN_VISIBLE_MS), 0);
});

test("the hold is never negative", () => {
  assert.equal(remainingHoldMs(0, 10_000), 0);
});

test("a clock that goes backwards yields the full hold, not a negative wait", () => {
  // A paused tab or a system clock adjustment. Showing the signal slightly
  // too long is harmless; skipping it is the bug.
  assert.equal(remainingHoldMs(5000, 1000), PROGRESS_MIN_VISIBLE_MS);
});

test("a non-finite timestamp yields the full hold rather than NaN", () => {
  // Either end being non-finite means the elapsed time is not trustworthy, so
  // the bar holds rather than guessing. NaN would otherwise propagate into a
  // setTimeout delay, which coerces to 0 and skips the signal entirely — the
  // exact failure this story exists to fix.
  assert.equal(remainingHoldMs(Number.NaN, 1000), PROGRESS_MIN_VISIBLE_MS);
  assert.equal(
    remainingHoldMs(1000, Number.POSITIVE_INFINITY),
    PROGRESS_MIN_VISIBLE_MS
  );
});

test("the minimum is configurable for callers that want a different feel", () => {
  assert.equal(remainingHoldMs(1000, 1100, 1000), 900);
  assert.equal(remainingHoldMs(1000, 1100, 0), 0);
});
