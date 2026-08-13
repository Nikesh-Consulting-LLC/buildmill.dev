/** Unit tests for `deriveBatchGate` — a pure projection, so it needs no DOM,
 * no network and no test framework. Run with `npm run test:web`.
 *
 * US-84.1 is what these cover: a feature header row in Waiting on you offers
 * a batch action only when EVERY child sits at the same gate and there is
 * more than one. Excluding abandoned children is the caller's job (the
 * sibling query filters them); this function judges exactly what it is fed.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { batchGateLabel, deriveBatchGate } from "./batch-gate.ts";

test("each gate maps to its batch mechanism", () => {
  assert.deepEqual(deriveBatchGate(["draft", "draft", "draft"]), {
    kind: "curate",
    count: 3,
  });
  assert.deepEqual(deriveBatchGate(["ready", "ready"]), {
    kind: "plan",
    count: 2,
  });
  assert.deepEqual(deriveBatchGate(["planned", "planned", "planned", "planned"]), {
    kind: "code",
    count: 4,
  });
  assert.deepEqual(deriveBatchGate(["plan-review", "plan-review"]), {
    kind: "approve",
    count: 2,
  });
});

test("mixed statuses offer nothing — a partial batch invites acting on unseen work", () => {
  assert.equal(deriveBatchGate(["draft", "ready"]), null);
  assert.equal(deriveBatchGate(["planned", "planned", "in-review"]), null);
  assert.equal(deriveBatchGate(["plan-review", "planned"]), null);
});

test("unanimous-of-one offers nothing — that is the row's own action wearing a hat", () => {
  assert.equal(deriveBatchGate(["draft"]), null);
  assert.equal(deriveBatchGate(["plan-review"]), null);
  assert.equal(deriveBatchGate([]), null);
});

test("unanimous statuses without a batch mechanism offer nothing", () => {
  // N in-review diffs are N distinct decisions about code (us-84.1 out of scope).
  assert.equal(deriveBatchGate(["in-review", "in-review"]), null);
  assert.equal(deriveBatchGate(["running", "running"]), null);
  assert.equal(deriveBatchGate(["done", "done"]), null);
});

test("labels say the count and the gate", () => {
  assert.equal(
    batchGateLabel({ kind: "curate", count: 8 }),
    "Curate all 8 stories"
  );
  assert.equal(batchGateLabel({ kind: "plan", count: 2 }), "Plan all 2 stories");
  assert.equal(batchGateLabel({ kind: "code", count: 5 }), "Code all 5 stories");
  assert.equal(
    batchGateLabel({ kind: "approve", count: 3 }),
    "Approve all 3 plans"
  );
});
