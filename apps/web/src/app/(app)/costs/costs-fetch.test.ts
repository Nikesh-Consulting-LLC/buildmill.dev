// us-119.3 AC2/AC4: one request per slice, and a late response never paints
// over a newer one. Both rules are pure, so they are pinned here.

import assert from "node:assert/strict";
import test from "node:test";

import { costsPath, createLatestGate } from "./costs-fetch.ts";

const ORG = "11111111-1111-1111-1111-111111111111";

test("the path carries every parameter explicitly, filters only when set", () => {
  assert.equal(
    costsPath(ORG, {
      groupBy: "project",
      days: 7,
      projectId: null,
      workerId: null,
      itemType: null,
    }),
    `/api/v1/llm/orgs/${ORG}/costs?group_by=project&days=7`,
  );
  const url = new URL(
    "http://x" +
      costsPath(ORG, {
        groupBy: "epic",
        days: 30,
        projectId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        workerId: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        itemType: "bug",
      }),
  );
  assert.equal(url.pathname, `/api/v1/llm/orgs/${ORG}/costs`);
  assert.deepEqual(Object.fromEntries(url.searchParams), {
    group_by: "epic",
    days: "30",
    project_id: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    worker_id: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    item_type: "bug",
  });
});

test("a slow first response is dropped once a second request has been issued", () => {
  const gate = createLatestGate();
  const slow = gate.next();
  const fast = gate.next();
  // the fast one lands first and is applied
  assert.equal(gate.isLatest(fast), true);
  // then the slow one arrives: it is stale, and must not update state
  assert.equal(gate.isLatest(slow), false);
  // the fast one stays the latest until another request is issued
  assert.equal(gate.isLatest(fast), true);
  gate.next();
  assert.equal(gate.isLatest(fast), false);
});

test("a fresh gate has issued nothing, so no ticket is latest", () => {
  const gate = createLatestGate();
  assert.equal(gate.isLatest(0), false);
  assert.equal(gate.isLatest(1), false);
});
