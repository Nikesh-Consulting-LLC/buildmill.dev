/**
 * The worker pool — the contract every AI worker speaks: claim, context,
 * heartbeat, comment, submit, release, plus the release-prep queue and the
 * per-run document store.
 *
 * Authentication here is the registry token in `X-Worker-Token`, not a session.
 * The two properties worth the most: a revoked or forged token gets nothing,
 * and a valid worker's context bundle carries no provider credentials — the
 * whole point of the gateway is that no provider key ever reaches the machine.
 */

import {
  all,
  describeAuthBoundary,
  expect,
  expectDetail,
  needsWorker,
  NONEXISTENT_UUID,
  test,
  whenConfigured,
  workerHeaders,
} from "../lib/suite";

test.describe("worker", () => {
  describeAuthBoundary("worker");

  test("an empty worker token is refused like a wrong one", async ({ request }) => {
    // Indistinguishable answers matter: a different code for "empty" than for
    // "revoked" would let anyone probe which tokens once existed.
    const empty = await request.get("/api/v1/worker/pool", {
      headers: { "X-Worker-Token": "" },
      failOnStatusCode: false,
    });
    const wrong = await request.get("/api/v1/worker/pool", {
      headers: { "X-Worker-Token": "definitely-not-a-registered-token" },
      failOnStatusCode: false,
    });
    expect(empty.status()).toBe(401);
    expect(wrong.status()).toBe(401);
    expect(await empty.text()).toBe(await wrong.text());
  });

  test("a session JWT does not stand in for a worker token", async ({ request }) => {
    // The two credential systems are separate on purpose; a manager's session
    // must not be able to claim work as if it were a worker.
    const response = await request.get("/api/v1/worker/pool", {
      headers: { Authorization: "Bearer some.session.token" },
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(401);
  });

  whenConfigured(needsWorker(), "as a registered worker", () => {
    test("GET /api/v1/worker/pool returns runs and resumable runs", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/worker/pool", {
        headers: workerHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const body = (await response.json()) as {
        runs?: unknown[];
        resumable?: unknown[];
      };
      // Both keys always, even when empty — the runner checks `resumable`
      // first, and a missing key there is a paused run left waiting forever.
      expect(Array.isArray(body.runs)).toBe(true);
      expect(Array.isArray(body.resumable)).toBe(true);
    });

    test("pool entries carry the fields a worker needs to start", async ({
      request,
    }) => {
      const body = (await (
        await request.get("/api/v1/worker/pool", { headers: workerHeaders() })
      ).json()) as { runs: Record<string, unknown>[] };
      test.skip(body.runs.length === 0, "this worker's pool is empty");
      for (const key of [
        "id",
        "kind",
        "issue_id",
        "issue_title",
        "issue_type",
        "project_name",
        "repo_full_name",
      ]) {
        expect(body.runs[0], `pool entry is missing ${key}`).toHaveProperty(key);
      }
    });

    test("GET /api/v1/worker/release-prep answers the prep queue", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/worker/release-prep", {
        headers: workerHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
    });

    test("a run this worker has not claimed cannot be heartbeated", async ({
      request,
    }) => {
      // The lease is what stops two workers holding the same run; heartbeating
      // an unclaimed run must not create one.
      const response = await request.post(
        `/api/v1/worker/runs/${NONEXISTENT_UUID}/heartbeat`,
        { headers: workerHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("a run this worker has not claimed cannot be submitted", async ({
      request,
    }) => {
      const response = await request.post(
        `/api/v1/worker/runs/${NONEXISTENT_UUID}/submit`,
        { headers: workerHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("the context bundle is unavailable for an unclaimed run", async ({
      request,
    }) => {
      const response = await request.get(
        `/api/v1/worker/runs/${NONEXISTENT_UUID}/context`,
        { headers: workerHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).not.toBe(200);
      expect(response.status()).toBeLessThan(500);
    });

    test("no worker response carries a provider API key", async ({ request }) => {
      // A gateway key (scoped, short-lived) is expected in a claimed run's
      // bundle; a real provider key never is.
      const text = await (
        await request.get("/api/v1/worker/pool", { headers: workerHeaders() })
      ).text();
      for (const marker of ["sk-ant-api", "sk-proj-", "xai-", "-----BEGIN"]) {
        expect(text.includes(marker), `pool response contains '${marker}'`).toBe(
          false,
        );
      }
    });
  });

  whenConfigured(all(needsWorker()), "claim contract", () => {
    test("claiming a run that does not exist is refused", async ({ request }) => {
      // Safe even outside ALLOW_MUTATIONS: there is nothing at this id to claim.
      const response = await request.post(
        `/api/v1/worker/runs/${NONEXISTENT_UUID}/claim`,
        { headers: workerHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });
});
