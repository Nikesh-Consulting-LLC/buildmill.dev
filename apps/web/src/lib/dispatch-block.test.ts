/** US-74.5: which actions a build-order block applies to. Run with
 * `npm run test:web` (node --test with native type stripping). */

import { test } from "node:test";
import assert from "node:assert/strict";

import { heldReason } from "./dispatch-block.ts";

const block = { reason: "waiting on an earlier feature to finish", hard: false };

test("a blocked dispatch is held", () => {
  assert.equal(heldReason({ mode: "dispatch", blocked: block }), block.reason);
});

test("a blocked re-dispatch is held", () => {
  assert.equal(heldReason({ mode: "redispatch", blocked: block }), block.reason);
});

test("an approval is never held, even carrying a block", () => {
  // The gate the manager CAN clear must stay clickable, or the dependency
  // that blocks the code run also freezes the plan approval that unblocks it.
  assert.equal(heldReason({ mode: "approve", blocked: block }), null);
});

test("a navigate row is never held", () => {
  assert.equal(heldReason({ mode: "navigate", blocked: block }), null);
});

test("an unblocked dispatch is not held", () => {
  assert.equal(heldReason({ mode: "dispatch", blocked: null }), null);
  assert.equal(heldReason({ mode: "dispatch" }), null);
});

test("a hard refusal and a soft wait both read as held", () => {
  // The hub disables the button either way; the distinction matters to the
  // wording, not to whether the action can run.
  assert.equal(
    heldReason({ mode: "dispatch", blocked: { reason: "r", hard: true } }),
    "r"
  );
  assert.equal(
    heldReason({ mode: "dispatch", blocked: { reason: "r", hard: false } }),
    "r"
  );
});
