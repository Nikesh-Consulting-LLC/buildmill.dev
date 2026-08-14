/** US-91.5: the Work Items status filter's pure logic — the default that
 * hides finished work, what the closed pill says, and the empty-set rule.
 * Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  HIDDEN_BY_DEFAULT,
  defaultStatusSelection,
  matchesStatusFilter,
  parseStoredSelection,
  statusFilterLabel,
} from "./issue-status-filter.ts";

/** Mirrors the badge's wording for the few statuses these cases assert on. */
const LABEL: Record<string, string> = {
  merged: "Merged",
  done: "Done",
  running: "Running",
  "in-review": "In review",
};
const labelOf = (s: string) => LABEL[s] ?? s;

const ALL = [
  "draft",
  "prd-review",
  "ready",
  "planning",
  "plan-review",
  "planned",
  "queued",
  "running",
  "needs-fixes",
  "in-review",
  "merged",
  "failed",
  "done",
];

test("the default hides merged and done, and nothing else", () => {
  const sel = defaultStatusSelection(ALL);
  assert.equal(sel.has("merged"), false);
  assert.equal(sel.has("done"), false);
  assert.equal(sel.size, ALL.length - 2);
  for (const s of ALL) {
    if (!HIDDEN_BY_DEFAULT.includes(s)) assert.ok(sel.has(s), `${s} missing`);
  }
});

test("everything checked is the same as no filtering", () => {
  const all = new Set(ALL);
  for (const s of ALL) assert.ok(matchesStatusFilter(s, all));
  assert.equal(statusFilterLabel(all, ALL, labelOf), "All statuses");
});

test("an empty selection shows nothing, and says so", () => {
  const none = new Set<string>();
  for (const s of ALL) assert.equal(matchesStatusFilter(s, none), false);
  assert.equal(statusFilterLabel(none, ALL, labelOf), "No statuses");
});

test("the default selection's label names what is hidden", () => {
  assert.equal(
    statusFilterLabel(defaultStatusSelection(ALL), ALL, labelOf),
    "All but merged, done"
  );
});

test("a small selection names the statuses it kept", () => {
  assert.equal(
    statusFilterLabel(new Set(["running", "in-review"]), ALL, labelOf),
    "Running, In review"
  );
});

test("a mid-sized selection falls back to a count", () => {
  assert.equal(
    statusFilterLabel(new Set(["draft", "ready", "running", "failed"]), ALL, labelOf),
    "4 statuses"
  );
});

test("a stored selection round-trips, and junk is ignored", () => {
  const stored = JSON.stringify(["running", "merged", "not-a-status"]);
  const parsed = parseStoredSelection(stored, ALL);
  assert.deepEqual([...(parsed ?? [])].sort(), ["merged", "running"]);
  assert.equal(parseStoredSelection("{oops", ALL), null);
  assert.equal(parseStoredSelection(null, ALL), null);
});

test("an empty stored selection is honoured, not treated as absent", () => {
  const parsed = parseStoredSelection("[]", ALL);
  assert.ok(parsed instanceof Set);
  assert.equal(parsed?.size, 0);
});
