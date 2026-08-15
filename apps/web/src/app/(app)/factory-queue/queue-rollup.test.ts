import { test } from "node:test";
import assert from "node:assert/strict";
import { rollupQueueRows } from "./queue-rollup.ts";
import type { QueueItem } from "./data.ts";

function run(over: Partial<QueueItem>): QueueItem {
  return {
    id: over.id ?? "r1",
    kind: "plan",
    status: "queued",
    projectId: "p1",
    projectName: "Proj",
    issueId: "i1",
    issueTitle: "story",
    displayId: null,
    epic: null,
    epicNumber: null,
    itemNo: null,
    subNo: null,
    state: "queued",
    heldReason: null,
    parent: null,
    createdAt: "2026-08-15T00:00:00Z",
    workerName: null,
    activity: null,
    silentMinutes: null,
    elapsedMinutes: null,
    ...over,
  } as QueueItem;
}

const parent = { id: "f1", displayId: "FEAT-1.2", title: "the feature" };

test("members collapse into one feature unit in first position", () => {
  const rows = rollupQueueRows([
    run({ id: "a", parent }),
    run({ id: "s", parent: null }),
    run({ id: "b", parent }),
  ]);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].kind, "feature-rollup");
  if (rows[0].kind === "feature-rollup") {
    assert.deepEqual(
      rows[0].members.map((m) => m.id),
      ["a", "b"]
    );
    assert.equal(rows[0].allQueued, true);
    assert.equal(rows[0].state, "queued");
  }
  assert.equal(rows[1].kind, "run");
});

test("state rolls up by precedence and surfaces the active member", () => {
  const rows = rollupQueueRows([
    run({ id: "a", parent, state: "held", heldReason: "waiting: US-1 ahead" }),
    run({
      id: "b",
      parent,
      state: "running",
      status: "running",
      workerName: "agent-3",
      activity: "writing tests",
    }),
  ]);
  const f = rows[0];
  assert.equal(f.kind, "feature-rollup");
  if (f.kind === "feature-rollup") {
    assert.equal(f.state, "running");
    assert.equal(f.active?.id, "b");
    assert.equal(f.active?.workerName, "agent-3");
    // A claimed member pins the block.
    assert.equal(f.allQueued, false);
  }
});

test("held reason surfaces when nothing runs", () => {
  const rows = rollupQueueRows([
    run({ id: "a", parent, state: "held", heldReason: "waiting: the law" }),
    run({ id: "b", parent }),
  ]);
  const f = rows[0];
  if (f.kind === "feature-rollup") {
    assert.equal(f.state, "held");
    assert.equal(f.heldReason, "waiting: the law");
    assert.equal(f.active, null);
  } else {
    assert.fail("expected a feature rollup");
  }
});

test("standalone runs pass through untouched", () => {
  const rows = rollupQueueRows([run({ id: "a" }), run({ id: "b" })]);
  assert.deepEqual(
    rows.map((r) => (r.kind === "run" ? r.item.id : "?")),
    ["a", "b"]
  );
});

test("paused counts feed the mixed-state line", () => {
  const rows = rollupQueueRows([
    run({ id: "a", parent, state: "paused" }),
    run({ id: "b", parent }),
    run({ id: "c", parent }),
  ]);
  const f = rows[0];
  if (f.kind === "feature-rollup") {
    assert.equal(f.pausedCount, 1);
    assert.equal(f.queuedCount, 2);
    assert.equal(f.state, "paused");
  } else {
    assert.fail("expected a feature rollup");
  }
});
