// us-95.4 AC6: URL state restore, where the logic is pure. parseCostsParams
// is what the server page hands the view; costsParamsFor is what the view
// writes back — a round trip must land on the same slice, and junk must fall
// to defaults rather than error or leak into requests.

import assert from "node:assert/strict";
import test from "node:test";

import { costsParamsFor, parseCostsParams } from "./costs-url.ts";

test("a bare URL is the default view — seven days (us-102.1)", () => {
  assert.deepEqual(parseCostsParams({}), {
    groupBy: "project",
    days: 7,
    projectId: null,
    workerId: null,
    itemType: null,
  });
});

test("a full slice round-trips through the URL", () => {
  const state = {
    groupBy: "epic",
    days: 30,
    projectId: "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    workerId: "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    itemType: "bug",
  };
  const qs = costsParamsFor(state);
  const parsed = parseCostsParams(
    Object.fromEntries(new URLSearchParams(qs).entries()),
  );
  assert.deepEqual(parsed, state);
});

test("defaults are omitted so the bare view stays bare", () => {
  assert.equal(
    costsParamsFor({
      groupBy: "project",
      days: 7,
      projectId: null,
      workerId: null,
      itemType: null,
    }),
    "",
  );
});

test("junk params fall back to defaults, not errors", () => {
  const parsed = parseCostsParams({
    group: "'; drop table runs; --",
    days: "9999",
    type: "banana",
  });
  assert.equal(parsed.groupBy, "project");
  assert.equal(parsed.days, 7);
  assert.equal(parsed.itemType, null);
});

test("every dimension in the vocabulary parses back to itself", () => {
  for (const key of ["project", "agent", "provider", "model", "type", "epic", "item"]) {
    assert.equal(parseCostsParams({ group: key }).groupBy, key);
  }
});

test("repeated params take the first value, like the server would", () => {
  const parsed = parseCostsParams({ group: ["type", "epic"], days: ["30", "90"] });
  assert.equal(parsed.groupBy, "type");
  assert.equal(parsed.days, 30);
});

// us-102.1 AC2: changing the DEFAULT must not change what an EXPLICIT window
// means. Every value already sitting in somebody's bookmark parses to itself.
test("every window in the vocabulary still parses to itself", () => {
  for (const days of [1, 7, 30, 90]) {
    assert.equal(parseCostsParams({ days: String(days) }).days, days);
  }
});

test("the new default is the one omitted from the URL", () => {
  const base = { groupBy: "project", projectId: null, workerId: null, itemType: null };
  assert.equal(costsParamsFor({ ...base, days: 7 }), "");
  assert.equal(costsParamsFor({ ...base, days: 30 }), "days=30");
});
