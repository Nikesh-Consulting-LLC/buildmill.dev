/** us-116.5: Start means start — the roster's action cluster.
 *
 * Run with `npm run test:web`. The roster is a client component the node test
 * runner cannot render, so these read the source off disk the way
 * `agent-roles.test.ts` reads the API's `ROUTE_KINDS`: what they pin is that
 * the membership ▶/⏸ (Suspend, which REVOKES an agent's token) is gated to
 * human rows, that agent rows carry Start/Stop on the agent's own endpoints,
 * and that suspending an agent goes through a confirm that says what it does.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (rel: string) =>
  readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf-8");

const roster = read("../app/(app)/team/team-view.tsx");
const detail = read("../app/(app)/team/[principalId]/member-detail.tsx");
const runner = read("../app/(app)/team/[principalId]/runner/page.tsx");
const machine = read("../app/(app)/servers/[id]/host-detail.tsx");
const wizard = read("../app/(app)/team/add-agent-wizard.tsx");

test("the membership Suspend/Reactivate button is rendered for humans only", () => {
  const at = roster.indexOf('title={suspended ? "Reactivate" : "Suspend"}');
  assert.ok(at > 0, "the membership button still exists for humans");
  // The nearest guard above it is the human-only one.
  const before = roster.slice(Math.max(0, at - 400), at);
  assert.ok(before.includes("{!isAgent && ("), "membership ▶/⏸ must be gated to !isAgent");
});

test("agent rows carry Start/Stop on the agent's own endpoints", () => {
  assert.ok(roster.includes('data-testid={agentStopped ? "agent-start" : "agent-stop"}'));
  assert.ok(roster.includes("/api/v1/agents/${principalId}/${action}"));
  // and the answer is a toast, never swallowed
  assert.ok(roster.includes("toastError(") && roster.includes("toastSuccess("));
});

test("Start/Stop appear only for an agent with a seat, gated on manage_org like the machine page", () => {
  assert.ok(roster.includes("{isAgent && canManageOrg && seat && ("));
});

test("suspending an agent from its detail panel names the token revocation", () => {
  assert.ok(detail.includes("Suspend — revoke its token"));
  assert.ok(detail.includes("revokes") && detail.includes("worker token"));
  assert.ok(detail.includes("use Stop on the roster instead"));
});

test("the runner page and the machine page call the same two endpoints, and show errors", () => {
  assert.ok(runner.includes("/api/v1/agents/${principalId}/${action}"));
  assert.ok(!runner.includes("/* the console keeps rendering; the fleet page reports why */"));
  assert.ok(!runner.includes("/* ditto */"));
  assert.ok(machine.includes("/api/v1/agents/${slot.principal_id}/${slot.paused ? \"start\" : \"stop\"}"));
  assert.ok(wizard.includes("/api/v1/agents/${created.principalId}/start"));
});

test("Stop is not systemctl stop: no surface calls a stop job", () => {
  for (const src of [roster, runner, machine, wizard]) {
    assert.ok(!/kind:\s*"stop"/.test(src));
    assert.ok(!src.includes("systemctl stop"));
  }
});
