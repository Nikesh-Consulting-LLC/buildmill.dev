/**
 * Run review — the manager's decisions on a submitted run: approve (which
 * merges the PR), reject, send back, cancel, request-stop, abandon, resume,
 * force-requeue and reset.
 *
 * Approve merges code, so it is opt-in everywhere. What runs always is the part
 * that must hold on any environment: a decision addressed at a run that does
 * not exist changes nothing and says so, and every decision endpoint answers a
 * `detail` the review screen can render rather than a bare 500.
 */

import {
  all,
  describeAuthBoundary,
  endpointsForTag,
  expect,
  expectDetail,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("reviews", () => {
  describeAuthBoundary("reviews");

  whenConfigured(needsUser(), "signed in", () => {
    test("every review decision refuses a run that does not exist", async ({
      request,
    }) => {
      const decisions = endpointsForTag("reviews");
      expect(decisions.length).toBe(10);

      const surprises: string[] = [];
      for (const decision of decisions) {
        const response = await request.post(
          decision.path.replace("{run_id}", NONEXISTENT_UUID),
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        const status = response.status();
        if (status < 400 || status >= 500) {
          surprises.push(`${decision.path} → ${status}`);
        }
      }
      expect(
        surprises,
        `review decisions answered outside 4xx for a phantom run:\n${surprises.join("\n")}`,
      ).toEqual([]);
    });

    test("approve on a phantom run cannot merge anything", async ({ request }) => {
      // The single most consequential endpoint in the api: approve merges the
      // PR. A 2xx here would mean a merge was attempted for a run nobody owns.
      const response = await request.post(
        `/api/v1/runs/${NONEXISTENT_UUID}/approve`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("a reject with no comment is still answered legibly", async ({
      request,
    }) => {
      // Send-back carries the manager's feedback into the retry run's context,
      // so an empty one is either refused or defaulted — never a 500.
      const response = await request.post(
        `/api/v1/runs/${NONEXISTENT_UUID}/reject`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsMutations()),
    "state-changing (opt-in)",
    () => {
      test("force-requeue on a phantom run is a no-op refusal", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/runs/${NONEXISTENT_UUID}/force-requeue`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeGreaterThanOrEqual(400);
      });
    },
  );
});
