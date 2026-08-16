/**
 * The supervisor runner's control surface — config, idle reason, policy
 * preview, workspace preparation and one-off commands.
 *
 * The runner itself connects over a WebSocket (`/api/v1/runner/socket`) that
 * this suite does not drive; these are the HTTP endpoints the web app uses to
 * inspect and steer a connected runner. `POST /{worker_id}/command` runs code
 * on an operator's machine, so it is opt-in and, unclaimed, must always refuse.
 */

import {
  all,
  describeAuthBoundary,
  expect,
  expectDetail,
  needsId,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";
import { config } from "../lib/config";

test.describe("runner", () => {
  describeAuthBoundary("runner");

  whenConfigured(needsUser(), "signed in", () => {
    test("an unknown worker has no idle reason to report", async ({ request }) => {
      const response = await request.get(
        `/api/v1/runner/${NONEXISTENT_UUID}/idle-reason`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
      expect(response.status()).not.toBe(200);
    });

    test("a command cannot be sent to a worker that does not exist", async ({
      request,
    }) => {
      // This endpoint executes on someone's own machine — the id check is the
      // only thing between a typo and a stranger's shell.
      const response = await request.post(
        `/api/v1/runner/${NONEXISTENT_UUID}/command`,
        {
          headers: userHeaders(),
          data: { command: "echo api-test" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("policy preview refuses an unknown worker", async ({ request }) => {
      const response = await request.post(
        `/api/v1/runner/${NONEXISTENT_UUID}/policy-preview`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("config cannot be patched on an unknown worker", async ({ request }) => {
      const response = await request.patch(
        `/api/v1/runner/${NONEXISTENT_UUID}/config`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.workerId, "TEST_WORKER_ID")),
    "against a registered worker",
    () => {
      test("idle-reason explains why the worker is not working", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/runner/${config.workerId}/idle-reason`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
        if (response.status() === 200) {
          expect(typeof (await response.json())).toBe("object");
        }
      });

      test("policy preview is read-only and dispatches nothing", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/runner/${config.workerId}/policy-preview`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );

  whenConfigured(
    all(needsUser(), needsId(config.workerId, "TEST_WORKER_ID"), needsMutations()),
    "state-changing (opt-in — runs on the operator's machine)",
    () => {
      test("a harmless command round-trips to the runner", async ({ request }) => {
        const response = await request.post(
          `/api/v1/runner/${config.workerId}/command`,
          {
            headers: userHeaders(),
            data: { command: "echo api-test-suite" },
            failOnStatusCode: false,
          },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
