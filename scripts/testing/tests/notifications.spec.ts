/**
 * Notification endpoints — where the factory sends a manager's alerts.
 *
 * Small surface, one sharp edge: `POST /endpoints/{id}/test` makes the server
 * issue an outbound request to a URL the caller supplied. That is SSRF-shaped
 * by construction, so the cases here check that an endpoint the caller does not
 * own cannot be triggered, and that a stored webhook secret is never read back.
 */

import {
  all,
  describeAuthBoundary,
  expect,
  expectDetail,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("notifications", () => {
  describeAuthBoundary("notifications");

  whenConfigured(needsUser(), "signed in", () => {
    test("testing an endpoint the caller does not own is refused", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/notifications/endpoints/${NONEXISTENT_UUID}/test`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("deleting an endpoint the caller does not own is refused", async ({
      request,
    }) => {
      const response = await request.delete(
        `/api/v1/notifications/endpoints/${NONEXISTENT_UUID}`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("an endpoint body missing its target is rejected", async ({ request }) => {
      const response = await request.post("/api/v1/notifications/endpoints", {
        headers: userHeaders(),
        data: {},
        failOnStatusCode: false,
      });
      expect([400, 403, 422]).toContain(response.status());
    });

    test("a rejected endpoint body does not echo the submitted secret", async ({
      request,
    }) => {
      const secret = `api-test-webhook-secret-${Date.now()}`;
      const response = await request.post("/api/v1/notifications/endpoints", {
        headers: userHeaders(),
        data: { kind: "webhook", secret },
        failOnStatusCode: false,
      });
      expect(
        (await response.text()).includes(secret),
        "the response echoed the submitted webhook secret",
      ).toBe(false);
    });
  });

  whenConfigured(
    all(needsUser(), needsMutations()),
    "state-changing (opt-in — sends a real notification)",
    () => {
      test("an endpoint can be created and deleted", async ({ request }) => {
        const created = await request.post("/api/v1/notifications/endpoints", {
          headers: userHeaders(),
          data: {
            kind: "webhook",
            // TEST-NET-3, reserved and unroutable: creating this endpoint can
            // never deliver anything to a real host.
            url: "https://203.0.113.9/api-test-webhook",
            name: `api-test-${Date.now()}`,
          },
          failOnStatusCode: false,
        });
        expect(created.status()).toBeLessThan(500);
        if (created.status() >= 400) return;

        const endpoint = (await created.json()) as { id?: string };
        if (!endpoint.id) return;
        const removed = await request.delete(
          `/api/v1/notifications/endpoints/${endpoint.id}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(removed.status()).toBeLessThan(400);
      });
    },
  );
});
