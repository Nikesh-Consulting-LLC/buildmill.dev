/** Unit tests for `nextChangedSet` — the rule behind US-87.12's live row
 * highlight. Pure, so it needs no DOM. Run with `npm run test:web`.
 *
 * The rule worth pinning is AC4: a batch dispatch changes every story under a
 * feature at once, and twenty rows pulsing together is the whole list
 * flashing, not a signal. These lock in that the effect stands down rather
 * than degrading into noise.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  HIGHLIGHT_STORM_LIMIT,
  nextChangedSet,
} from "./recent-changes.ts";

test("a single change is marked", () => {
  const next = nextChangedSet(new Set(), "a");
  assert.deepEqual([...(next ?? [])], ["a"]);
});

test("marking does not mutate the set it was given", () => {
  const current = new Set(["a"]);
  const next = nextChangedSet(current, "b");
  assert.deepEqual([...current], ["a"], "the input set was mutated");
  assert.equal(next?.size, 2);
});

test("marking the same row twice keeps one entry", () => {
  const next = nextChangedSet(new Set(["a"]), "a");
  assert.deepEqual([...(next ?? [])], ["a"]);
});

test("a handful of changes all mark", () => {
  let set: ReadonlySet<string> = new Set();
  for (let i = 0; i < HIGHLIGHT_STORM_LIMIT - 1; i++) {
    const next = nextChangedSet(set, `id-${i}`);
    assert.notEqual(next, null, `stood down early at ${i}`);
    set = next as Set<string>;
  }
  assert.equal(set.size, HIGHLIGHT_STORM_LIMIT - 1);
});

test("a storm stands down instead of flashing the whole list", () => {
  // AC4: the batch-dispatch case.
  let set: ReadonlySet<string> = new Set();
  let stoodDown = false;
  for (let i = 0; i < 20; i++) {
    const next = nextChangedSet(set, `id-${i}`);
    if (next === null) {
      stoodDown = true;
      break;
    }
    set = next;
  }
  assert.equal(stoodDown, true, "twenty simultaneous changes never stood down");
});

test("standing down is signalled distinctly from an empty set", () => {
  // `null` (stand down) and an empty set (nothing highlighted) need different
  // handling of the pending timers, so they must not be conflated.
  const atLimit = new Set(
    Array.from({ length: HIGHLIGHT_STORM_LIMIT }, (_, i) => `id-${i}`)
  );
  assert.equal(nextChangedSet(atLimit, "one-more"), null);
  assert.notEqual(nextChangedSet(new Set(), "first"), null);
});
