/** Unit tests for `parseStageDurations`/`withoutStageLines` — US-62.10's
 * within-run stage timing. Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { parseStageDurations, withoutStageLines } from "./stage-durations.ts";

test("four stage lines parse into four stage durations", () => {
  const rows = [
    { kind: "step", content: "stage:checkout 2400ms" },
    { kind: "step", content: "stage:invoke_cli 480000ms" },
    { kind: "step", content: "stage:collect_output 120ms" },
    { kind: "step", content: "stage:commit_and_push 3100ms" },
  ];
  const out = parseStageDurations(rows);
  assert.equal(out.length, 4);
  assert.deepEqual(out[0], { stage: "checkout", totalMs: 2400, occurrences: 1 });
});

test("a repair retry running the same stage twice is summed, not overwritten", () => {
  const rows = [
    { kind: "step", content: "stage:invoke_cli 100000ms" },
    { kind: "step", content: "stage:invoke_cli 50000ms" },
  ];
  const out = parseStageDurations(rows);
  assert.equal(out.length, 1);
  assert.equal(out[0].totalMs, 150000);
  assert.equal(out[0].occurrences, 2);
});

test("non-stage trace lines are ignored", () => {
  const rows = [
    { kind: "progress", content: "reading the repo now" },
    { kind: "step", content: "not a stage line" },
    { kind: "tool", content: "stage:checkout 100ms" }, // wrong kind
  ];
  assert.deepEqual(parseStageDurations(rows), []);
});

test("a run predating this change has no stage lines and returns empty", () => {
  assert.deepEqual(parseStageDurations([]), []);
});

test("withoutStageLines drops only the bookkeeping lines", () => {
  const rows = [
    { kind: "step", content: "stage:checkout 2400ms" },
    { kind: "progress", content: "reading the repo now" },
    { kind: "step", content: "a normal step line" },
  ];
  const out = withoutStageLines(rows);
  assert.equal(out.length, 2);
  assert.deepEqual(out[0], { kind: "progress", content: "reading the repo now" });
});
