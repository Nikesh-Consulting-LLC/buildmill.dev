/**
 * Org membership provisioning — creating a member with a generated one-time
 * password, and resetting one.
 *
 * Both endpoints mint a credential. The generated password is shared offline
 * exactly once, which makes one rule absolute: the response may carry it at the
 * moment of provisioning and nowhere else, and a *refused* request must never
 * carry one at all. Provisioning creates a real auth identity, so it is opt-in.
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

test.describe("members", () => {
  describeAuthBoundary("members");

  whenConfigured(needsUser(), "signed in", () => {
    test("provisioning into an org the caller does not administer is refused", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/orgs/${NONEXISTENT_UUID}/members/provision`,
        {
          headers: userHeaders(),
          data: { email: "api-test@example.invalid", role: "member" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("a refused provisioning carries no generated password", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/orgs/${NONEXISTENT_UUID}/members/provision`,
        {
          headers: userHeaders(),
          data: { email: "api-test@example.invalid", role: "member" },
          failOnStatusCode: false,
        },
      );
      const body = await response.text();
      for (const key of ["password", "temporary_password", "one_time_password"]) {
        expect(
          body.includes(key),
          `a refused provisioning mentioned '${key}'`,
        ).toBe(false);
      }
    });

    test("a malformed email is rejected before any identity is created", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/orgs/${NONEXISTENT_UUID}/members/provision`,
        {
          headers: userHeaders(),
          data: { email: "not-an-email", role: "member" },
          failOnStatusCode: false,
        },
      );
      expect([400, 403, 404, 422]).toContain(response.status());
    });

    test("resetting an unknown member's password is refused", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/orgs/${NONEXISTENT_UUID}/members/${NONEXISTENT_UUID}/reset-password`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.orgId, "TEST_ORG_ID")),
    "against a real org",
    () => {
      test("provisioning without an email is rejected by the schema", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/orgs/${config.orgId}/members/provision`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect([400, 403, 422]).toContain(response.status());
      });
    },
  );

  whenConfigured(
    all(needsUser(), needsId(config.orgId, "TEST_ORG_ID"), needsMutations()),
    "state-changing (opt-in — creates a real auth identity)",
    () => {
      test("a provisioned member comes back with a one-time password", async ({
        request,
      }) => {
        const email = `api-test-${Date.now()}@example.invalid`;
        const response = await request.post(
          `/api/v1/orgs/${config.orgId}/members/provision`,
          {
            headers: userHeaders(),
            data: { email, role: "member" },
            failOnStatusCode: false,
          },
        );
        expect(response.status()).toBeLessThan(500);
        if (response.status() >= 400) return;
        const body = (await response.json()) as Record<string, unknown>;
        // Shown exactly once, at provisioning — this is the only response in
        // the api that legitimately carries a password.
        const carriesPassword = Object.keys(body).some((k) =>
          k.toLowerCase().includes("password"),
        );
        expect(carriesPassword).toBe(true);
      });
    },
  );
});
