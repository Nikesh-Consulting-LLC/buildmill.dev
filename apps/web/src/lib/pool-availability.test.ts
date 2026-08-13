/** Unit tests for `poolAvailability` — a pure projection, so it needs no DOM,
 * no network and no test framework.
 *
 * Run with `npm run test:web` (node --test with native type stripping).
 *
 * US-57.10 is what these cover. The bug was not a wrong sentence, it was one
 * sentence for three different situations: the RPC filtered on
 * `status = 'ready'`, so "no pool exists", "the pool is broken" and "the pool
 * is full" all arrived as an empty list. On 2026-07-31 the wizard told a
 * manager to provision or resize a pool that had 31 of 32 slots free and was
 * sitting at `error` — the one remedy that could not have worked.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  poolAvailability,
  selectablePools,
  type PoolOption,
} from "./pool-availability.ts";

function pool(over: Partial<PoolOption> = {}): PoolOption {
  return {
    poolId: "p1",
    poolName: "Pod-001",
    status: "ready",
    freeSlots: 4,
    ...over,
  };
}

test("a ready pool with room is available", () => {
  const out = poolAvailability([pool()]);
  assert.equal(out.state, "available");
  assert.equal(out.state === "available" && out.pools.length, 1);
});

test("no pools at all asks for one to be provisioned", () => {
  const out = poolAvailability([]);
  assert.equal(out.state, "none");
  assert.match(out.state === "none" ? out.message : "", /provision/i);
});

// --- the exact production incident -----------------------------------------

test("a pool with free slots but not ready is not a capacity problem", () => {
  const out = poolAvailability([
    pool({ status: "error", freeSlots: 31 }),
  ]);
  assert.equal(out.state, "not-ready");
  const message = out.state === "not-ready" ? out.message : "";
  assert.match(message, /Pod-001/); // names it, so the superadmin knows which
  assert.match(message, /not ready/i);
  // The advice that was wrong on 2026-07-31 must not come back.
  assert.doesNotMatch(message, /provision/i);
  assert.match(message, /Resizing will not help/i);
});

test("every ready pool full does ask for a resize", () => {
  const out = poolAvailability([pool({ freeSlots: 0 })]);
  assert.equal(out.state, "full");
  const message = out.state === "full" ? out.message : "";
  assert.match(message, /full/i);
  assert.match(message, /resize/i);
});

test("a full ready pool beside a broken one reads as full, not broken", () => {
  // Room is the binding constraint here: fixing the broken pool is not what
  // unblocks this manager, adding capacity is.
  const out = poolAvailability([
    pool({ poolId: "p1", poolName: "Pod-001", freeSlots: 0 }),
    pool({ poolId: "p2", poolName: "Pod-002", status: "error", freeSlots: 8 }),
  ]);
  assert.equal(out.state, "full");
  assert.match(out.state === "full" ? out.message : "", /Pod-001/);
});

test("one usable pool beside a broken one is simply available", () => {
  const out = poolAvailability([
    pool({ poolId: "p1", poolName: "Pod-001", status: "error", freeSlots: 31 }),
    pool({ poolId: "p2", poolName: "Pod-002", freeSlots: 2 }),
  ]);
  assert.equal(out.state, "available");
  assert.deepEqual(
    out.state === "available" ? out.pools.map((p) => p.poolId) : [],
    ["p2"],
  );
});

test("several unready pools are all named", () => {
  const out = poolAvailability([
    pool({ poolId: "p1", poolName: "Pod-001", status: "error" }),
    pool({ poolId: "p2", poolName: "Pod-002", status: "provisioning" }),
  ]);
  assert.equal(out.state, "not-ready");
  const message = out.state === "not-ready" ? out.message : "";
  assert.match(message, /Pod-001 and Pod-002/);
});

// --- placement is unchanged by any of this ---------------------------------

test("only ready pools with room are ever selectable", () => {
  const selectable = selectablePools([
    pool({ poolId: "ready-room", freeSlots: 3 }),
    pool({ poolId: "ready-full", freeSlots: 0 }),
    pool({ poolId: "broken-room", status: "error", freeSlots: 31 }),
    pool({ poolId: "provisioning", status: "provisioning", freeSlots: 5 }),
    pool({ poolId: "degraded", status: "degraded", freeSlots: 5 }),
  ]);
  assert.deepEqual(
    selectable.map((p) => p.poolId),
    ["ready-room"],
  );
});
