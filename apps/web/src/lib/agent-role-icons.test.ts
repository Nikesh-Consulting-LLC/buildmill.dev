import { test } from "node:test";
import assert from "node:assert/strict";
import {
  AGENT_ROLES,
  ALL_ROLE_KEYS,
  roleOfKind,
  rolesForKinds,
  ROLE_KINDS,
} from "./agent-roles.ts";

// us-107.3: the icon vocabulary is a mapping from ROLE, and the component that
// owns the glyphs cannot be imported here (JSX). What is pinned instead is the
// contract that mapping depends on: exactly four roles, and every dispatchable
// kind resolving to one of them — because `iconForKind` resolves a run kind
// through `roleOfKind`, and a kind belonging to no role silently renders no
// icon on a routing button.

test("there are exactly four capabilities", () => {
  // The team page draws all four and greys the absent ones. A fifth role added
  // without a glyph would render as a gap rather than a capability.
  assert.equal(AGENT_ROLES.length, 4);
  assert.deepEqual(ALL_ROLE_KEYS, [
    "planning",
    "programming",
    "testing",
    "deployment",
  ]);
});

test("every dispatchable kind resolves to a role", () => {
  // This is what `iconForKind` relies on. A kind with no role gets no icon,
  // and the routing button silently falls back to a generic rocket.
  for (const { key } of ROLE_KINDS) {
    assert.ok(roleOfKind(key), `run kind '${key}' belongs to no role`);
  }
});

test("the kinds the Workbench routes all have a role", () => {
  // The four `triageAction` / Dispatch-group kinds specifically — these are
  // the ones that reach an Action button.
  for (const kind of ["plan", "code", "prd", "deploy", "test"]) {
    assert.ok(roleOfKind(kind), `routed kind '${kind}' has no role`);
  }
});

test("plan is planning, code is programming, test is testing, deploy is deployment", () => {
  // The manager's own mapping: clipboard/code/bug/rocket. If these four ever
  // move roles the icons would quietly start lying.
  assert.equal(roleOfKind("plan")?.key, "planning");
  assert.equal(roleOfKind("code")?.key, "programming");
  assert.equal(roleOfKind("test")?.key, "testing");
  assert.equal(roleOfKind("deploy")?.key, "deployment");
});

test("null kinds means every capability, not none", () => {
  // The team row draws from this. Reading a never-saved agent as benched would
  // grey out all four icons for an agent that in fact does everything.
  assert.deepEqual(rolesForKinds(null), [...ALL_ROLE_KEYS]);
  assert.deepEqual(rolesForKinds(undefined), [...ALL_ROLE_KEYS]);
  assert.deepEqual(rolesForKinds([]), []);
});

test("a partial role still lights its icon", () => {
  // A legacy config with `plan` but not `elaborate` is an agent that plans.
  assert.deepEqual(rolesForKinds(["plan"]), ["planning"]);
  assert.deepEqual(rolesForKinds(["code"]), ["programming"]);
});
