/** us-116.6: a new agent starts ready — the wizard's contract, pinned off
 * disk the way `agent-roles.test.ts` reads the API's `ROUTE_KINDS`.
 *
 * Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const wizard = readFileSync(
  fileURLToPath(new URL("../app/(app)/team/add-agent-wizard.tsx", import.meta.url)),
  "utf-8",
);

test("the config PATCH names no platform-owned field", () => {
  // The guard fires on presence, not value: `run_routes: {}` 403'd every org
  // owner who was not a platform admin.
  const patchAt = wizard.indexOf("/api/v1/runner/${who.workerId}/config");
  const body = wizard.slice(patchAt, wizard.indexOf("});", patchAt));
  for (const field of [
    "run_routes",
    "model_routes",
    "autonomy_policy",
    "max_run_minutes",
    "max_total_run_minutes",
    "max_item_attempts",
  ]) {
    assert.ok(!body.includes(`${field}:`), `wizard PATCH must not send ${field}`);
  }
  assert.ok(!wizard.includes("buildRunRoutes("));
});

test("both placements ask for an enabled slot", () => {
  const machineAt = wizard.indexOf("/api/v1/agent-servers/${machineId}/slots");
  const machineBody = wizard.slice(machineAt, wizard.indexOf("});", machineAt));
  assert.ok(machineBody.includes('desired_state: "enabled"'));
  const poolAt = wizard.indexOf("/api/v1/agent-pools/${poolId}/place");
  const poolBody = wizard.slice(poolAt, wizard.indexOf("});", poolAt));
  assert.ok(poolBody.includes('desired_state: "enabled"'));
});

test("the Done step offers Start only for a Stopped agent, and never says it starts paused", () => {
  assert.ok(!wizard.includes("It starts paused"));
  assert.ok(wizard.includes('stateFor(online, status) === "stopped"'));
  assert.ok(wizard.includes("Start the agent"));
  assert.ok(wizard.includes("/api/v1/agents/${created.principalId}/start"));
});

test("the Roles step asks the resolver before the agent exists, and does not block", () => {
  assert.ok(wizard.includes("/api/v1/agents/model-check?org="));
  assert.ok(wizard.includes('data-testid="no-model-warning"'));
  assert.ok(wizard.includes("Settings → LLM providers"));
});
