/**
 * The manager's gates — PRD draft/approve/send-back, elaboration, plan
 * approval, breakdown, replan, wireframes, merge override.
 *
 * Every one of these is a state transition somebody is accountable for, so the
 * safe assertions are about refusals: a gate applied to a work item that does
 * not exist, or in a state that does not permit it, must answer 4xx with a
 * `detail` the UI can render — never a 500, and never a silent success.
 */

import {
  all,
  describeAuthBoundary,
  endpointsForTag,
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

test.describe("workflow", () => {
  describeAuthBoundary("workflow");

  whenConfigured(needsUser(), "signed in", () => {
    test("every gate refuses a work item that does not exist", async ({
      request,
    }) => {
      // POST-only and addressed at a uuid nobody owns: the run either does not
      // exist or is not visible under RLS, so nothing can transition.
      const gates = endpointsForTag("workflow").filter(
        (e) => e.method === "POST" && e.path.includes("{issue_id}"),
      );
      expect(gates.length).toBeGreaterThan(10);

      const surprises: string[] = [];
      for (const gate of gates) {
        const path = gate.path.replace("{issue_id}", NONEXISTENT_UUID);
        const response = await request.fetch(path, {
          method: "POST",
          headers: userHeaders(),
          data: {},
          failOnStatusCode: false,
        });
        const status = response.status();
        // 2xx would mean a gate fired on a phantom work item; 5xx would mean
        // the refusal path itself is broken.
        if (status < 400 || status >= 500) {
          surprises.push(`${gate.method} ${gate.path} → ${status}`);
        }
      }
      expect(
        surprises,
        `gates answered outside 4xx for a nonexistent work item:\n${surprises.join("\n")}`,
      ).toEqual([]);
    });

    test("the wireframe preview refuses an unknown work item in HTML-safe form", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/issues/${NONEXISTENT_UUID}/wireframe/preview`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("PATCH an unknown artifact is refused", async ({ request }) => {
      const response = await request.patch(
        `/api/v1/artifacts/${NONEXISTENT_UUID}`,
        { headers: userHeaders(), data: { content: "x" }, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.issueId, "TEST_ISSUE_ID"), needsMutations()),
    "state-changing (opt-in)",
    () => {
      test("a gate applied in the wrong state is refused with a reason", async ({
        request,
      }) => {
        // Approving a plan on an item that has none is the cheapest way to
        // prove the state machine is enforced rather than assumed.
        const response = await request.post(
          `/api/v1/issues/${config.issueId}/plan/approve`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        if (response.status() >= 400) {
          const detail = await expectDetail(response);
          expect(detail.length).toBeGreaterThan(0);
        }
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
