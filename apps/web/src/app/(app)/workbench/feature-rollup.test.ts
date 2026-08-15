import { test } from "node:test";
import assert from "node:assert/strict";
import { rollupFeatureRows } from "./feature-rollup.ts";
import type { TodoGroup, TodoItem } from "./data.ts";

function item(over: Partial<TodoItem>): TodoItem {
  return {
    id: over.id ?? "i1",
    title: over.title ?? "a story",
    type: over.type ?? "story",
    displayId: over.displayId ?? null,
    project: "Proj",
    projectId: "p1",
    reason: over.reason ?? "why",
    age: over.age ?? "2h",
    ageLevel: over.ageLevel ?? "normal",
    action: over.action ?? "Open",
    href: over.href ?? "/issues/i1",
    mode: over.mode ?? "navigate",
    ...over,
  } as TodoItem;
}

const feat = (id: string, batchGate: TodoItem["parent"] extends infer _ ? unknown : never = null) => ({
  id,
  displayId: "FEAT-1.2",
  title: "the feature",
  batchGate: batchGate as never,
});

test("children across groups collapse into one feature row", () => {
  const groups: TodoGroup[] = [
    {
      title: "Reviews",
      items: [
        item({ id: "c1", parent: feat("f1"), ageLevel: "normal", age: "1h" }),
        item({ id: "s1" }),
      ],
    },
    {
      title: "Fix & retry",
      items: [item({ id: "c2", parent: feat("f1"), ageLevel: "bad", age: "3d", displayId: "US-1.2.3" })],
    },
  ];
  rollupFeatureRows(groups);

  assert.equal(groups[0].items.length, 2);
  const row = groups[0].items[0];
  assert.equal(row.id, "f1");
  assert.equal(row.type, "feature");
  assert.equal(row.rollup?.count, 2);
  // The row wears the worst wait in the batch.
  assert.equal(row.ageLevel, "bad");
  assert.equal(row.age, "3d");
  // Trouble is named, not expanded into rows.
  assert.deepEqual(row.rollup?.attention, [
    { id: "c2", label: "US-1.2.3" },
  ]);
  assert.match(row.reason, /2 stories waiting/);
  assert.match(row.reason, /1 awaits your review/);
  assert.match(row.reason, /1 needs attention/);
  // The second group's only row was carried into the feature row.
  assert.equal(groups[1].items.length, 0);
  // The standalone row survives untouched.
  assert.equal(groups[0].items[1].id, "s1");
});

test("a feature's own flat row is replaced, not duplicated", () => {
  const groups: TodoGroup[] = [
    {
      title: "Triage",
      items: [
        item({ id: "f1", type: "feature", title: "the feature" }),
        item({ id: "c1", parent: feat("f1") }),
      ],
    },
  ];
  rollupFeatureRows(groups);
  const f1Rows = groups[0].items.filter((i) => i.id === "f1");
  assert.equal(f1Rows.length, 1);
  assert.ok(f1Rows[0].rollup);
});

test("no parents means no change", () => {
  const groups: TodoGroup[] = [
    { title: "Dispatch", items: [item({ id: "a" }), item({ id: "b" })] },
  ];
  rollupFeatureRows(groups);
  assert.deepEqual(
    groups[0].items.map((i) => i.id),
    ["a", "b"]
  );
  assert.equal(groups[0].items[0].rollup, undefined);
});

test("the batch gate rides the parent ref onto the row", () => {
  const groups: TodoGroup[] = [
    {
      title: "Reviews",
      items: [
        item({
          id: "c1",
          parent: { id: "f1", displayId: "FEAT-1.2", title: "t", batchGate: "approve" as never },
        }),
      ],
    },
  ];
  rollupFeatureRows(groups);
  assert.equal(groups[0].items[0].rollup?.batchGate, "approve");
});
