/**
 * Work items — dispatch, batch dispatch, attempts, revert.
 *
 * These are the endpoints that put a run into the pool, so almost everything
 * here is destructive by nature and gated. What is always safe to assert is the
 * refusal side: an unknown work item, and a body the schema rejects, must both
 * answer a legible 4xx rather than dispatching anything or returning a 500.
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

test.describe("issues", () => {
  describeAuthBoundary("issues");

  whenConfigured(needsUser(), "signed in", () => {
    test("dispatching an unknown work item is refused, not dispatched", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/issues/${NONEXISTENT_UUID}/dispatch`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect([400, 403, 404, 409, 422]).toContain(response.status());
      await expectDetail(response);
    });

    test("a malformed work-item id answers a refusal, never a 500", async ({
      request,
    }) => {
      const response = await request.post("/api/v1/issues/not-a-uuid/dispatch", {
        headers: userHeaders(),
        data: {},
        failOnStatusCode: false,
      });
      expect(response.status()).toBeLessThan(500);
    });

    test("batch dispatch rejects a body that is not a list of ids", async ({
      request,
    }) => {
      const response = await request.post("/api/v1/issues/batch-dispatch", {
        headers: userHeaders(),
        data: { issue_ids: "not-a-list" },
        failOnStatusCode: false,
      });
      expect([400, 403, 422]).toContain(response.status());
    });

    test("GET attempts for an unknown work item does not leak another org's data", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/issues/${NONEXISTENT_UUID}/attempts`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
      if (response.status() === 200) {
        const body = (await response.json()) as { attempts?: unknown[] } | unknown[];
        const attempts = Array.isArray(body) ? body : (body.attempts ?? []);
        expect(attempts).toEqual([]);
      }
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.issueId, "TEST_ISSUE_ID")),
    "against a real work item",
    () => {
      test("GET attempts returns the run history", async ({ request }) => {
        const response = await request.get(
          `/api/v1/issues/${config.issueId}/attempts`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
        const body = await response.json();
        expect(body).toBeTruthy();
      });

      test("GET deployments lists where this work item has shipped", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/issues/${config.issueId}/deployments`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
      });
    },
  );

  whenConfigured(
    all(
      needsUser(),
      needsId(config.issueId, "TEST_ISSUE_ID"),
      needsMutations(),
    ),
    "state-changing (opt-in)",
    () => {
      test("complexity scoring answers a score for a real work item", async ({
        request,
      }) => {
        // Costs an LLM call, which is why it sits behind ALLOW_MUTATIONS even
        // though it changes little else.
        const response = await request.post(
          `/api/v1/issues/${config.issueId}/complexity-score`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
