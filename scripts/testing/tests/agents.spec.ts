/**
 * The agent fleet — managed hosts, slots, pools, agent identities and CLI
 * sessions (agent-servers, agent-pools, agents, agent-sessions).
 *
 * Provisioning reaches real machines over SSH and reissuing a slot token
 * invalidates a running agent's credential, so every write here is opt-in. The
 * unconditional half covers the reads a dashboard depends on, plus the one
 * secret rule this surface has: a slot's worker token is shown once at issue
 * and never again — no listing may return it.
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

test.describe("agents", () => {
  describeAuthBoundary("agent-servers", "agent-pools", "agents", "agent-sessions");

  whenConfigured(needsUser(), "signed in", () => {
    test("GET /api/v1/agent-servers/current-version returns the bundle hash", async ({
      request,
    }) => {
      // The fleet-drift comparison point: a content hash of the runner tree, so
      // two deploys of the same commit agree.
      const response = await request.get("/api/v1/agent-servers/current-version", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const body = (await response.json()) as { bundle_hash?: string };
      expect(typeof body.bundle_hash).toBe("string");
      expect(body.bundle_hash!.length).toBeGreaterThan(8);
    });

    test("idle reasons require an org and answer per worker", async ({
      request,
    }) => {
      const missing = await request.get("/api/v1/agents/idle-reasons", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      expect(missing.status()).toBe(422);

      const response = await request.get(
        `/api/v1/agents/idle-reasons?org=${NONEXISTENT_UUID}`,
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
      if (response.status() === 200) {
        const body = (await response.json()) as Record<string, unknown>;
        // Both keyings, always — the dashboard addresses agents by worker and
        // Team addresses them by principal.
        expect(body).toHaveProperty("reasons");
        expect(body).toHaveProperty("by_principal");
        // Org scoping is RLS: a phantom org must resolve to no agents at all.
        expect(Object.keys(body.reasons as object)).toEqual([]);
      }
    });

    test("an unknown host refuses provisioning and teardown alike", async ({
      request,
    }) => {
      for (const suffix of ["preflight", "provision", "probe", "update", "teardown"]) {
        const response = await request.post(
          `/api/v1/agent-servers/${NONEXISTENT_UUID}/${suffix}`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(
          response.status(),
          `${suffix} answered ${response.status()}`,
        ).toBeGreaterThanOrEqual(400);
        expect(response.status()).toBeLessThan(500);
      }
    });

    test("renaming an agent that does not exist is refused", async ({
      request,
    }) => {
      const response = await request.patch(
        `/api/v1/agents/${NONEXISTENT_UUID}/name`,
        {
          headers: userHeaders(),
          data: { name: "api-test-rename" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
      await expectDetail(response);
    });

    test("placing into an unknown pool is refused", async ({ request }) => {
      const response = await request.post(
        `/api/v1/agent-pools/${NONEXISTENT_UUID}/place`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });

    test("closing an unknown CLI session is refused", async ({ request }) => {
      const response = await request.post(
        `/api/v1/agent-sessions/${NONEXISTENT_UUID}/close`,
        { headers: userHeaders(), data: {}, failOnStatusCode: false },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.agentHostId, "TEST_AGENT_HOST_ID")),
    "against a real host",
    () => {
      test("slot idle-reasons answers for the host", async ({ request }) => {
        const response = await request.get(
          `/api/v1/agent-servers/${config.agentHostId}/slots/idle-reasons`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });

      test("no host read returns a slot's worker token or SSH credential", async ({
        request,
      }) => {
        // A slot token is shown once at issue; a listing that returned it would
        // hand any org member a live agent credential.
        const response = await request.get(
          `/api/v1/agent-servers/${config.agentHostId}/slots/idle-reasons`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        if (response.status() !== 200) return;
        const text = await response.text();
        for (const marker of ["-----BEGIN", "worker_token", "private_key"]) {
          expect(text.includes(marker), `host read contains '${marker}'`).toBe(
            false,
          );
        }
      });
    },
  );

  whenConfigured(
    all(
      needsUser(),
      needsId(config.agentHostId, "TEST_AGENT_HOST_ID"),
      needsMutations(),
    ),
    "state-changing (opt-in)",
    () => {
      test("preflight probes the host without provisioning it", async ({
        request,
      }) => {
        const response = await request.post(
          `/api/v1/agent-servers/${config.agentHostId}/preflight`,
          { headers: userHeaders(), data: {}, failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );
});
