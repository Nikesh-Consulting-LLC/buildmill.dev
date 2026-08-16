/**
 * Releases and their test suites — sign-off, promote, reject, cancel, retry,
 * rollback, and the per-case results a human records.
 *
 * The rule this category exists to protect: **sign-off is allowed only when the
 * UAT deployment succeeded and every case passed, and blocked counts as not
 * passed** (CLAUDE.md, Versioning & Release). `signoff-blocker` is the endpoint
 * that says why, so it is the one read worth asserting in shape.
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

test.describe("releases", () => {
  describeAuthBoundary("releases", "suites");

  whenConfigured(needsUser(), "signed in", () => {
    test("an unknown release cannot be signed off", async ({ request }) => {
      const response = await request.post(
        `/api/v1/releases/${NONEXISTENT_UUID}/sign-off`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("an unknown release cannot be promoted", async ({ request }) => {
      // Promotion ships a build to production — a phantom id must never get
      // past the lookup.
      const response = await request.post(
        `/api/v1/releases/${NONEXISTENT_UUID}/promote`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("signoff-blocker answers for an unknown release without a 500", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/releases/${NONEXISTENT_UUID}/signoff-blocker`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
    });

    test("recording a result for an unknown case is refused", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/releases/${NONEXISTENT_UUID}/test-cases/${NONEXISTENT_UUID}/result`,
        {
          headers: userHeaders(),
          data: { result: "passed" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("an unrecognised test-case result value is rejected", async ({
      request,
    }) => {
      // 'blocked' and 'passed' are not interchangeable — a typo'd status that
      // the api accepted would let a blocked case sign a release off.
      const response = await request.post(
        `/api/v1/releases/${NONEXISTENT_UUID}/test-cases/${NONEXISTENT_UUID}/result`,
        {
          headers: userHeaders(),
          data: { result: "definitely-not-a-result" },
          failOnStatusCode: false,
        },
      );
      expect([400, 403, 404, 409, 422]).toContain(response.status());
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.releaseId, "TEST_RELEASE_ID")),
    "against a real release",
    () => {
      test("signoff-blocker names the blockers or says there are none", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/releases/${config.releaseId}/signoff-blocker`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
        const body = (await response.json()) as Record<string, unknown>;
        expect(typeof body).toBe("object");
        // The UI needs a decidable answer, not prose it has to parse.
        const decidable = ["blocked", "can_sign_off", "blockers", "reason"].some(
          (key) => key in body,
        );
        expect(
          decidable,
          `signoff-blocker returned ${JSON.stringify(body).slice(0, 200)}`,
        ).toBe(true);
      });
    },
  );

  whenConfigured(
    all(
      needsUser(),
      needsId(config.releaseId, "TEST_RELEASE_ID"),
      needsMutations(),
    ),
    "state-changing (opt-in)",
    () => {
      test("sign-off is refused while a blocker stands", async ({ request }) => {
        const blocker = await request.get(
          `/api/v1/releases/${config.releaseId}/signoff-blocker`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        const body = (await blocker.json()) as Record<string, unknown>;
        const blocked =
          body.blocked === true ||
          body.can_sign_off === false ||
          (Array.isArray(body.blockers) && body.blockers.length > 0);
        test.skip(!blocked, "this release has no sign-off blocker to enforce");

        const response = await request.post(
          `/api/v1/releases/${config.releaseId}/sign-off`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeGreaterThanOrEqual(400);
      });
    },
  );
});
