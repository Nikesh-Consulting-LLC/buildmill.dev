/** US-77.1: the four roles, and the promise that they cover the pipeline.
 *
 * Run with `npm run test:web` (node --test with native type stripping).
 *
 * The interesting test here is not the mapping — it is the last one, which
 * reads the API's own `ROUTE_KINDS` off disk. A run kind that exists in the
 * dispatcher but in no role would vanish from the manager's UI without
 * erroring: the work would be dispatched and no agent's checkboxes could ever
 * include it, so it would sit `queued` forever. That is the same failure mode
 * `apps/api/tests/test_runner_kind_coverage.py` guards on the runner's side.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  AGENT_ROLES,
  ROLE_KINDS,
  kindsForRoles,
  roleLabelsForKinds,
  roleOfKind,
  rolesArePartial,
  rolesForKinds,
} from "./agent-roles.ts";

test("there are exactly four roles, in the manager's order", () => {
  assert.deepEqual(
    AGENT_ROLES.map((r) => r.label),
    ["Planning", "Programming", "Testing", "Deployment"]
  );
});

test("a kind belongs to exactly one role", () => {
  const keys = ROLE_KINDS.map((k) => k.key);
  assert.equal(new Set(keys).size, keys.length);
});

test("wireframes plan, they do not program", () => {
  assert.equal(roleOfKind("wireframe")?.key, "planning");
  assert.equal(roleOfKind("code")?.key, "programming");
});

test("checked roles expand to the kinds actually stored", () => {
  assert.deepEqual(kindsForRoles(["testing"]), ["test"]);
  assert.deepEqual(kindsForRoles(["deployment"]), ["release", "deploy"]);
  assert.deepEqual(kindsForRoles(["planning"]), [
    "prd",
    "breakdown",
    "plan",
    "guidelines",
    "elaborate",
    "wireframe",
  ]);
  assert.deepEqual(kindsForRoles([]), []);
});

test("a never-saved agent (null) reads as every role", () => {
  assert.deepEqual(rolesForKinds(null), [
    "planning",
    "programming",
    "testing",
    "deployment",
  ]);
  assert.deepEqual(rolesForKinds(undefined), [
    "planning",
    "programming",
    "testing",
    "deployment",
  ]);
});

test("an explicitly benched agent ([]) reads as no role", () => {
  assert.deepEqual(rolesForKinds([]), []);
  assert.deepEqual(roleLabelsForKinds([]), []);
});

test("a partial legacy role still reads as checked", () => {
  // An agent granted `plan` but not `elaborate` was an agent that plans;
  // reading it as "not Planning" would bench work it has been claiming.
  assert.deepEqual(rolesForKinds(["plan"]), ["planning"]);
  assert.equal(rolesArePartial(["plan"]), true);
  assert.equal(rolesArePartial(kindsForRoles(["planning"])), false);
  assert.equal(rolesArePartial(null), false);
});

test("roles read out loud in role order, not storage order", () => {
  assert.deepEqual(roleLabelsForKinds(["test", "prd"]), ["Planning", "Testing"]);
});

test("every kind the API can route has a role", () => {
  // Parsed rather than imported: apps/api is a separate program, and this
  // contract must hold without a Python toolchain.
  const src = readFileSync(
    fileURLToPath(new URL("../../../api/app/routers/runner_socket.py", import.meta.url)),
    "utf8"
  );
  const at = src.indexOf("ROUTE_KINDS = (");
  const block = at === -1 ? "" : src.slice(at, src.indexOf(")", at));
  const apiKinds = new Set(
    [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1])
  );
  assert.ok(apiKinds.size > 0, "could not read ROUTE_KINDS from runner_socket.py");
  const mine = new Set(ROLE_KINDS.map((k) => k.key));
  assert.deepEqual(
    [...apiKinds].filter((k) => !mine.has(k)),
    [],
    "a dispatchable run kind has no role — it would be invisible in the agent UI, " +
      "so no agent could ever be configured to claim it. Add it to AGENT_ROLES."
  );
  assert.deepEqual(
    [...mine].filter((k) => !apiKinds.has(k)),
    [],
    "a role names a run kind the API refuses — saving those checkboxes would 422."
  );
});
