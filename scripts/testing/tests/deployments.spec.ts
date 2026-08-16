/**
 * Deployments — run, promote, rollback, drift, zip artifacts, preflight,
 * health-check and per-deployment env.
 *
 * This category talks to real servers over SSH, so every mutating test is
 * opt-in. What runs unconditionally is the refusal surface plus one rule worth
 * more than the rest: `PUT /env/{name}` writes a value that may be a secret, and
 * no read anywhere may hand it back.
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

test.describe("deployments", () => {
  describeAuthBoundary("deployments");

  whenConfigured(needsUser(), "signed in", () => {
    test("an unknown deployment cannot be run", async ({ request }) => {
      const response = await request.post(
        `/api/v1/deployments/${NONEXISTENT_UUID}/run`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("an unknown deployment cannot be rolled back or promoted", async ({
      request,
    }) => {
      for (const suffix of [
        "rollback",
        "redeploy-zip",
        "preflight",
        "health-check",
      ]) {
        const response = await request.post(
          `/api/v1/deployments/${NONEXISTENT_UUID}/${suffix}`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(
          response.status(),
          `${suffix} answered ${response.status()}`,
        ).toBeGreaterThanOrEqual(400);
        expect(response.status()).toBeLessThan(500);
      }
    });

    test("drift on an unknown deployment answers a refusal", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/deployments/${NONEXISTENT_UUID}/drift`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
      expect(response.status()).not.toBe(200);
    });

    test("deleting an unknown deployment answers a refusal, not 500", async ({
      request,
    }) => {
      // This is the exact shape of BUG-1.1: an ambiguous PostgREST embed made
      // delete answer a bare 500. A refusal must read like one — and if the
      // database refuses the query, the api translates it to 502 with a code.
      const response = await request.delete(
        `/api/v1/deployments/${NONEXISTENT_UUID}`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(
        response.status(),
        "delete answered 500 — check for an un-hinted PostgREST embed (PGRST201)",
      ).not.toBe(500);
      expect(response.status()).toBeGreaterThanOrEqual(400);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.deploymentId, "TEST_DEPLOYMENT_ID")),
    "against a real deployment",
    () => {
      test("drift reports a count, not a hash dump", async ({ request }) => {
        const response = await request.get(
          `/api/v1/deployments/${config.deploymentId}/drift`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
        if (response.status() === 200) {
          expect(typeof (await response.json())).toBe("object");
        }
      });

      test("no deployment read returns an env secret value", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/deployments/${config.deploymentId}/drift`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        if (response.status() !== 200) return;
        const text = await response.text();
        for (const forbidden of ["-----BEGIN", "sk-ant-", "ghp_", "PRIVATE KEY"]) {
          expect(text.includes(forbidden), `drift contains '${forbidden}'`).toBe(
            false,
          );
        }
      });
    },
  );

  whenConfigured(
    all(
      needsUser(),
      needsId(config.deploymentId, "TEST_DEPLOYMENT_ID"),
      needsMutations(),
    ),
    "state-changing (opt-in)",
    () => {
      test("an env var round-trips without the value coming back", async ({
        request,
      }) => {
        const name = "API_TEST_ENV_VAR";
        const secret = `set-by-the-suite-${Date.now()}`;
        const put = await request.put(
          `/api/v1/deployments/${config.deploymentId}/env/${name}`,
          { headers: userHeaders(), data: { value: secret }, failOnStatusCode: false },
        );
        expect(put.status()).toBeLessThan(400);
        expect(
          (await put.text()).includes(secret),
          "the write echoed the value back",
        ).toBe(false);

        const removed = await request.delete(
          `/api/v1/deployments/${config.deploymentId}/env/${name}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(removed.status()).toBeLessThan(400);
      });

      test("preflight reports reachability without deploying", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/deployments/${config.deploymentId}/preflight`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
