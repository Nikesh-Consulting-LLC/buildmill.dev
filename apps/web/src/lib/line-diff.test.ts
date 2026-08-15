/** US-100.5: the per-file diff a manager reads before accepting a refresh.
 * Run with `npm run test:web`. */

import { test } from "node:test";
import assert from "node:assert/strict";

import { diffStats, unifiedDiff } from "./line-diff.ts";

test("identical texts produce no diff", () => {
  assert.equal(unifiedDiff("AGENTS.md", "a\nb\n", "a\nb\n"), "");
  assert.deepEqual(diffStats("x", "x"), { added: 0, removed: 0 });
});

test("a one-line change is a single hunk with context", () => {
  const before = ["l1", "l2", "l3", "l4", "l5", "l6", "l7", "l8"].join("\n");
  const after = ["l1", "l2", "l3", "l4", "L5", "l6", "l7", "l8"].join("\n");
  const d = unifiedDiff(".buildmill/Code.md", before, after);
  const lines = d.split("\n");
  assert.equal(lines[0], "diff --git a/.buildmill/Code.md b/.buildmill/Code.md");
  assert.ok(lines.includes("-l5"));
  assert.ok(lines.includes("+L5"));
  // three lines of context either side, and nothing else
  assert.ok(lines.includes(" l2") && lines.includes(" l8"));
  assert.ok(!lines.includes(" l1"));
  assert.equal(lines.filter((l) => l.startsWith("@@")).length, 1);
  assert.deepEqual(diffStats(before, after), { added: 1, removed: 1 });
});

test("hunk headers carry correct line numbers", () => {
  const before = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj";
  const after = "a\nb\nc\nd\ne\nf\ng\nh\ni\nj\nk";
  const d = unifiedDiff("f", before, after);
  assert.ok(d.includes("@@ -8,3 +8,4 @@"), d);
  assert.ok(d.endsWith("+k"));
});

test("an empty current file is all additions; an emptied file all removals", () => {
  const add = unifiedDiff("f", "", "one\ntwo");
  assert.ok(add.includes("+one") && add.includes("+two"));
  assert.ok(!add.split("\n").some((l) => l.startsWith("-") && !l.startsWith("---")));
  const rm = unifiedDiff("f", "one\ntwo", "");
  assert.ok(rm.includes("-one") && rm.includes("-two"));
  assert.deepEqual(diffStats("", "one\ntwo"), { added: 2, removed: 0 });
});

test("distant changes become separate hunks", () => {
  const before = Array.from({ length: 30 }, (_, i) => `line ${i}`).join("\n");
  const after = before.replace("line 2", "LINE 2").replace("line 27", "LINE 27");
  const d = unifiedDiff("f", before, after);
  assert.equal(d.split("\n").filter((l) => l.startsWith("@@")).length, 2);
});
