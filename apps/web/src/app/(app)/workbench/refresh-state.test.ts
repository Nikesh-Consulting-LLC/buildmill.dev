import { test } from "node:test";
import assert from "node:assert/strict";
import {
  refreshLabel,
  refreshState,
  STALE_AFTER_HOURS,
} from "./refresh-state.ts";

// us-107.2: the 2026-08-16 incident in one assertion — an unclaimed run must
// never be described as an agent working, no matter how long it has sat.

test("a claimed, un-handed-back refresh is an agent working", () => {
  const s = refreshState({ ready: false, claimed: true, hoursWaiting: 0.1 });
  assert.equal(s, "working");
  assert.equal(refreshLabel(s), "An agent is reading the repository");
});

test("a claimed refresh stays 'working' however long it takes", () => {
  // A slow agent is not a stalled queue — that is the lease reaper's business.
  assert.equal(
    refreshState({ ready: false, claimed: true, hoursWaiting: 200 }),
    "working",
  );
});

test("an unclaimed refresh is waiting, not working", () => {
  const s = refreshState({ ready: false, claimed: false, hoursWaiting: 0.1 });
  assert.equal(s, "waiting");
  assert.notEqual(refreshLabel(s), "An agent is reading the repository");
});

test("an unclaimed refresh past the threshold is stalled", () => {
  const s = refreshState({
    ready: false,
    claimed: false,
    hoursWaiting: STALE_AFTER_HOURS,
  });
  assert.equal(s, "stalled");
  assert.equal(refreshLabel(s), "No worker has picked this up");
});

test("the f483ee01 case: 6 days unclaimed reads as stalled", () => {
  // The actual incident row — queued 2026-08-10, never claimed, still
  // rendering "An agent is reading the repository" on 2026-08-16.
  assert.equal(
    refreshState({ ready: false, claimed: false, hoursWaiting: 24 * 6 + 5 }),
    "stalled",
  );
});

test("handed back wins over everything — it is a review", () => {
  assert.equal(
    refreshState({ ready: true, claimed: false, hoursWaiting: 999 }),
    "ready",
  );
  assert.equal(
    refreshState({ ready: true, claimed: true, hoursWaiting: 0 }),
    "ready",
  );
});

test("no state below 'ready' ever claims an agent is reading unless claimed", () => {
  for (const hours of [0, 0.5, 1, 24, 500]) {
    const s = refreshState({ ready: false, claimed: false, hoursWaiting: hours });
    assert.notEqual(
      refreshLabel(s),
      "An agent is reading the repository",
      `unclaimed at ${hours}h claimed an agent was reading`,
    );
  }
});
