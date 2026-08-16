import assert from "node:assert/strict";
import { test } from "node:test";

import {
  PREP_LEASE_MINUTES,
  duration,
  prepLiveness,
  type PrepRow,
} from "./release-liveness.ts";

const NOW = Date.parse("2026-08-16T16:12:00Z");
const ago = (minutes: number) => new Date(NOW - minutes * 60_000).toISOString();
const ahead = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

function prep(over: Partial<PrepRow> = {}): PrepRow {
  return {
    workerName: "Architect",
    workerPrincipalId: "p-1",
    claimedAt: ago(12),
    claimExpiresAt: ahead(PREP_LEASE_MINUTES),
    ...over,
  };
}

test("a heartbeating prep reads as working", () => {
  const l = prepLiveness(prep(), null, NOW);
  assert.equal(l.reading, "working");
  assert.equal(l.workerName, "Architect");
  assert.equal(l.heldMinutes, 12);
  assert.equal(l.silentMinutes, 0);
});

test("silence is recovered exactly from the lease, not guessed", () => {
  // Last beat 45 minutes ago ⇒ the lease was pushed to (then + 2h), which is
  // 75 minutes from now.
  const l = prepLiveness(
    prep({ claimedAt: ago(90), claimExpiresAt: ahead(PREP_LEASE_MINUTES - 45) }),
    null,
    NOW
  );
  assert.equal(l.silentMinutes, 45);
  assert.equal(l.reading, "silent");
  assert.equal(l.heldMinutes, 90);
});

test("a lapsed lease reads as abandoned — the 2026-08-16 state", () => {
  // Claimed 13:36, lease expired 15:46, read at 16:12: held 156 minutes,
  // last heard 146 minutes ago. The card said "being prepared".
  const l = prepLiveness(
    prep({ claimedAt: ago(156), claimExpiresAt: ago(26) }),
    null,
    NOW
  );
  assert.equal(l.reading, "abandoned");
  assert.equal(l.heldMinutes, 156);
  assert.equal(l.silentMinutes, PREP_LEASE_MINUTES + 26);
});

test("the boundary between working and silent is the threshold, not near it", () => {
  const at19 = prepLiveness(
    prep({ claimExpiresAt: ahead(PREP_LEASE_MINUTES - 19) }),
    null,
    NOW
  );
  const at20 = prepLiveness(
    prep({ claimExpiresAt: ahead(PREP_LEASE_MINUTES - 20) }),
    null,
    NOW
  );
  assert.equal(at19.reading, "working");
  assert.equal(at20.reading, "silent");
});

test("the boundary between silent and abandoned is lease expiry", () => {
  const alive = prepLiveness(prep({ claimExpiresAt: ahead(1) }), null, NOW);
  const dead = prepLiveness(prep({ claimExpiresAt: ago(1) }), null, NOW);
  assert.equal(alive.reading, "silent");
  assert.equal(dead.reading, "abandoned");
});

test("no prep row means nobody has taken it — and how long it has waited", () => {
  const l = prepLiveness(null, ago(7), NOW);
  assert.equal(l.reading, "unclaimed");
  assert.equal(l.heldMinutes, 7);
  assert.equal(l.workerName, "");
});

test("a queued prep row is also unclaimed", () => {
  const l = prepLiveness(prep({ claimedAt: null }), ago(3), NOW);
  assert.equal(l.reading, "unclaimed");
  assert.equal(l.heldMinutes, 3);
});

test("a claim with no recorded expiry falls back to the claim itself", () => {
  const l = prepLiveness(
    prep({ claimedAt: ago(30), claimExpiresAt: null }),
    null,
    NOW
  );
  assert.equal(l.silentMinutes, 30);
  assert.equal(l.reading, "silent");
});

test("durations read as words", () => {
  assert.equal(duration(0), "just now");
  assert.equal(duration(12), "12 min");
  assert.equal(duration(60), "1h");
  assert.equal(duration(156), "2h 36m");
  assert.equal(duration(60 * 30), "1d 6h");
});
