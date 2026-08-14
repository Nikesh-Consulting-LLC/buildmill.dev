/** US-91.15: the bell's copy and destinations. The bug these pin is that
 * every produced type fell through to the raw enum plus "a work item", and
 * clicking went nowhere. Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  KNOWN_NOTIFICATION_TYPES,
  describeNotification,
  groupNotifications,
  humanizeType,
  notificationHref,
  type NotificationLike,
} from "./notification-copy.ts";

function notif(
  type: string,
  payload: Record<string, unknown>,
  over: Partial<NotificationLike> = {}
): NotificationLike {
  return {
    id: Math.random().toString(36).slice(2),
    type,
    payload,
    read_at: null,
    created_at: "2026-08-14T09:00:00Z",
    ...over,
  };
}

test("a runner fault names the agent and shows its message", () => {
  const v = describeNotification(
    notif("runner_fault", {
      worker: "Chip",
      run_id: "11111111-1111-1111-1111-111111111111",
      message: "workspace prep failed: no usable shell",
    })
  );
  assert.equal(v.subject, "Chip");
  assert.equal(v.summary, "hit a runner fault");
  assert.equal(v.detail, "workspace prep failed: no usable shell");
});

test("an unhealthy deploy names the deployment", () => {
  const v = describeNotification(
    notif("deploy_unhealthy", {
      deployment: "app-uat",
      run_id: "22222222-2222-2222-2222-222222222222",
      message: "health check returned 502",
    })
  );
  assert.equal(v.subject, "app-uat");
  assert.equal(v.summary, "failed its health check");
});

test("no row ever says 'a work item' for something that is not one", () => {
  for (const type of KNOWN_NOTIFICATION_TYPES) {
    const v = describeNotification(notif(type, {}));
    assert.ok(!/a work item/.test(v.summary), `${type} claimed a work item`);
    assert.ok(!/_/.test(v.subject), `${type} leaked machine text`);
  }
});

test("an unknown future type degrades to humanised words, not the raw enum", () => {
  const v = describeNotification(
    notif("budget_exhausted", { message: "project is over budget" })
  );
  assert.equal(v.subject, "Budget exhausted");
  assert.equal(v.detail, "project is over budget");
  assert.equal(humanizeType("runner_fault"), "Runner fault");
});

test("a payload with a blank message shows no detail rather than empty space", () => {
  const v = describeNotification(notif("runner_fault", { message: "   " }));
  assert.equal(v.detail, null);
  assert.equal(v.subject, "An agent");
});

test("a run id sends the row to that run", () => {
  assert.equal(
    notificationHref(notif("runner_fault", { run_id: "abc" })),
    "/runs/abc"
  );
});

test("without a run, a fault falls back to the agent's runner page", () => {
  assert.equal(
    notificationHref(notif("runner_fault", { principal_id: "p1" })),
    "/team/p1/runner"
  );
});

test("a payload naming no destination resolves to null, not a broken link", () => {
  assert.equal(notificationHref(notif("runner_fault", { worker: "Chip" })), null);
});

test("repeats from the same agent collapse into one row", () => {
  const groups = groupNotifications([
    notif("runner_fault", { worker: "Chip" }),
    notif("runner_fault", { worker: "Chip" }),
    notif("runner_fault", { worker: "Chip" }, { read_at: "2026-08-14T09:00:00Z" }),
    notif("deploy_unhealthy", { deployment: "app-uat" }),
    notif("runner_fault", { worker: "Chip" }),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].all.length, 4);
  assert.equal(groups[0].unread, 3);
  assert.equal(groups[1].all.length, 1);
});

test("an interleaved sweep still collapses — the case that motivated this", () => {
  // The real feed: one notification per agent per round, so no two
  // neighbours are alike. Consecutive-only grouping collapsed nothing here.
  const agents = ["pod-001-5", "Architect.001", "Build.001"];
  const rounds = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
  const feed = rounds.flatMap(() =>
    agents.map((worker) => notif("runner_fault", { worker }))
  );
  const groups = groupNotifications(feed);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].all.length, 10);
  assert.deepEqual(
    groups.map((g) => describeNotification(g.head).subject),
    agents
  );
});

test("grouping keeps first-appearance order, which is newest-first", () => {
  const groups = groupNotifications([
    notif("runner_fault", { worker: "Dale" }),
    notif("runner_fault", { worker: "Chip" }),
    notif("runner_fault", { worker: "Dale" }),
  ]);
  assert.deepEqual(groups.map((g) => g.all.length), [2, 1]);
  assert.equal(describeNotification(groups[0].head).subject, "Dale");
});

test("faults from different agents do not collapse together", () => {
  const groups = groupNotifications([
    notif("runner_fault", { worker: "Chip" }),
    notif("runner_fault", { worker: "Dale" }),
  ]);
  assert.equal(groups.length, 2);
});
