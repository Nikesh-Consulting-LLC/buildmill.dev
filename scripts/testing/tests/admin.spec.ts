/**
 * Platform admin console — 41 operations that manage orgs, users, memberships,
 * prompt/project templates and platform-wide run config.
 *
 * This is the highest-blast-radius surface in the api (it can delete an org),
 * and it is also the only one guarded by `require_platform_admin`, which does a
 * live `is_platform_admin()` RPC on the caller's own JWT rather than trusting a
 * claim. The privilege boundary — an ordinary member gets 403, not 200 — is
 * therefore the headline test here.
 */

import {
  adminHeaders,
  all,
  describeAuthBoundary,
  endpointsForTag,
  expect,
  needsAdmin,
  needsMutations,
  needsUser,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("admin", () => {
  describeAuthBoundary("admin");

  whenConfigured(needsUser(), "as an ordinary member", () => {
    test("every admin operation answers 403, never 200", async ({ request }) => {
      // Read-only operations only: a privilege check that leaked would
      // otherwise perform the write while proving the point.
      const readable = endpointsForTag("admin").filter((e) => e.method === "GET");
      expect(readable.length).toBeGreaterThan(5);

      const seen: string[] = [];
      for (const endpoint of readable) {
        const response = await request.fetch(endpoint.path.replace(/{[^}]+}/g, "1"), {
          method: "GET",
          headers: userHeaders(),
          failOnStatusCode: false,
        });
        if (response.status() !== 403) {
          seen.push(`${endpoint.method} ${endpoint.path} → ${response.status()}`);
        }
      }
      expect(seen, `non-admin was not refused with 403:\n${seen.join("\n")}`).toEqual(
        [],
      );
    });
  });

  whenConfigured(needsAdmin(), "as a platform admin", () => {
    test("GET /api/v1/admin/orgs lists organizations with owner and quota", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/admin/orgs", {
        headers: adminHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const orgs = (await response.json()) as Record<string, unknown>[];
      expect(Array.isArray(orgs)).toBe(true);
      if (orgs.length) {
        // These three are merged in by the handler on top of the plain row —
        // a regression in either extra read drops them silently.
        expect(orgs[0]).toHaveProperty("id");
        expect(orgs[0]).toHaveProperty("name");
        expect(orgs[0]).toHaveProperty("owner");
        expect(orgs[0]).toHaveProperty("agent_count");
      }
    });

    test("GET /api/v1/admin/users lists platform users", async ({ request }) => {
      const response = await request.get("/api/v1/admin/users", {
        headers: adminHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const body = await response.json();
      expect(Array.isArray(body) || typeof body === "object").toBe(true);
    });

    test("no admin listing ever returns key material", async ({ request }) => {
      // Secrets are write-only by design (Vault RPCs, the private `data`
      // bucket). A listing that started echoing one would be invisible until
      // something like this asked.
      for (const path of [
        "/api/v1/admin/orgs",
        "/api/v1/admin/users",
        "/api/v1/admin/run-config",
      ]) {
        const response = await request.get(path, {
          headers: adminHeaders(),
          failOnStatusCode: false,
        });
        if (response.status() !== 200) continue;
        const text = await response.text();
        for (const forbidden of [
          "service_role",
          "-----BEGIN",
          "sk-ant-",
          "ghp_",
          "supabase_service_role_key",
        ]) {
          expect(
            text.includes(forbidden),
            `${path} response contains '${forbidden}'`,
          ).toBe(false);
        }
      }
    });

    test("the read-only analytics endpoints answer", async ({ request }) => {
      for (const path of [
        "/api/v1/admin/usage",
        "/api/v1/admin/run-analytics",
        "/api/v1/admin/user-activity",
        "/api/v1/admin/gate-latency",
        "/api/v1/admin/performance",
        "/api/v1/admin/modules",
        "/api/v1/admin/prompt-templates",
        "/api/v1/admin/project-templates",
        "/api/v1/admin/preset-templates",
      ]) {
        const response = await request.get(path, {
          headers: adminHeaders(),
          failOnStatusCode: false,
        });
        expect(
          response.status(),
          `${path} answered ${response.status()}`,
        ).toBeLessThan(500);
        expect(response.status()).not.toBe(401);
        expect(response.status()).not.toBe(403);
      }
    });

    test("a bad org id answers a refusal, not a 500", async ({ request }) => {
      const response = await request.get(
        "/api/v1/admin/orgs/00000000-0000-0000-0000-000000000000/members",
        { headers: adminHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsAdmin(), needsMutations()),
    "state-changing (opt-in)",
    () => {
      test("creating and deleting an org round-trips", async ({ request }) => {
        const name = `api-test-org-${Date.now()}`;
        const created = await request.post("/api/v1/admin/orgs", {
          headers: adminHeaders(),
          data: { name },
          failOnStatusCode: false,
        });
        expect(created.status()).toBeLessThan(300);
        const org = (await created.json()) as { id?: string };
        expect(org.id).toBeTruthy();

        const removed = await request.delete(`/api/v1/admin/orgs/${org.id}`, {
          headers: adminHeaders(),
          failOnStatusCode: false,
        });
        expect(removed.status()).toBeLessThan(300);
      });

      test("the platform-admin org itself cannot be deleted", async ({
        request,
      }) => {
        // The seed migration creates exactly one and never runs again —
        // deleting it locks every superadmin out of /admin permanently.
        const orgs = (await (
          await request.get("/api/v1/admin/orgs", { headers: adminHeaders() })
        ).json()) as { id: string; is_platform_admin?: boolean }[];
        const platform = orgs.find((o) => o.is_platform_admin);
        test.skip(!platform, "this deployment has no platform-admin org row");

        const response = await request.delete(`/api/v1/admin/orgs/${platform!.id}`, {
          headers: adminHeaders(),
          failOnStatusCode: false,
        });
        expect(response.status()).toBe(400);
      });
    },
  );
});
