/** Unit tests for the work-item identity helpers — pure functions, run with
 * `npm run test:web` (node --test with native type stripping). */

import { test } from "node:test";
import assert from "node:assert/strict";

import { compareWorkItemSequence, workItemDisplayId } from "./work-items.ts";

const item = (
  item_no: number | null,
  sub_no: number | null,
  title = "t"
) => ({ item_no, sub_no, title });

test("compareWorkItemSequence orders sub-numbers numerically, not as text", () => {
  // US-74.1: the bug this guards — a string compare puts "10" above "2".
  const sorted = [item(1, 10), item(1, 2), item(1, 1)]
    .sort(compareWorkItemSequence)
    .map((i) => i.sub_no);
  assert.deepEqual(sorted, [1, 2, 10]);
});

test("compareWorkItemSequence orders by item_no before sub_no", () => {
  const sorted = [item(2, 1), item(1, 9), item(1, 3)]
    .sort(compareWorkItemSequence)
    .map((i) => `${i.item_no}.${i.sub_no}`);
  assert.deepEqual(sorted, ["1.3", "1.9", "2.1"]);
});

test("compareWorkItemSequence sorts unnumbered items last", () => {
  const sorted = [
    item(null, null, "no numbering"),
    item(3, 1, "third"),
    item(1, null, "feature-level"),
  ]
    .sort(compareWorkItemSequence)
    .map((i) => i.title);
  assert.deepEqual(sorted, ["feature-level", "third", "no numbering"]);
});

test("compareWorkItemSequence falls back to title when numbering ties", () => {
  const sorted = [item(1, 1, "beta"), item(1, 1, "alpha")]
    .sort(compareWorkItemSequence)
    .map((i) => i.title);
  assert.deepEqual(sorted, ["alpha", "beta"]);
});

test("compareWorkItemSequence is stable for equal items", () => {
  assert.equal(compareWorkItemSequence(item(1, 1), item(1, 1)), 0);
});

test("workItemDisplayId builds the dotted id the comparator orders", () => {
  assert.equal(
    workItemDisplayId({ type: "story", epicNumber: 3, itemNo: 1, subNo: 2 }),
    "US-3.1.2"
  );
  assert.equal(
    workItemDisplayId({ type: "feature", epicNumber: 3, itemNo: 1 }),
    "FEAT-3.1"
  );
  assert.equal(
    workItemDisplayId({ type: "story", epicNumber: null, itemNo: 1 }),
    null
  );
});
