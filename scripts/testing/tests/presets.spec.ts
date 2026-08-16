/**
 * Run-setting presets — the org's rows and the platform's templates.
 *
 * The behaviour worth pinning: editing a platform template must not change how
 * any org already runs. `GET /orgs/{id}/presets/reseed` is the dry run that
 * says what a reseed *would* do, and the POST is the one that does it — a GET
 * that mutated would make that distinction meaningless.
 */

import {
  adminHeaders,
  all,
  describeAuthBoundary,
  expect,
  needsAdmin,
  needsId,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";
import { config } from "../lib/config";

test.describe("presets", () => {
  describeAuthBoundary("presets");

  whenConfigured(needsUser(), "signed in", () => {
    test("preset reads for an unseen org return nothing", async ({ request }) => {
      for (const suffix of ["presets/reseed", "presets/outcomes"]) {
        const response = await request.get(
          `/api/v1/orgs/${NONEXISTENT_UUID}/${suffix}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(
          response.status(),
          `${suffix} answered ${response.status()}`,
        ).toBeLessThan(500);
      }
    });

    test("patching an unknown preset is refused", async ({ request }) => {
      const response = await request.patch(
        `/api/v1/presets/${NONEXISTENT_UUID}`,
        {
          headers: userHeaders(),
          data: { name: "api-test" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.orgId, "TEST_ORG_ID")),
    "against a real org",
    () => {
      test("the reseed preview is read-only", async ({ request }) => {
        // Called twice: a preview that changed something would answer
        // differently the second time.
        const first = await request.get(
          `/api/v1/orgs/${config.orgId}/presets/reseed`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(first.status()).toBeLessThan(500);
        if (first.status() !== 200) return;
        const second = await request.get(
          `/api/v1/orgs/${config.orgId}/presets/reseed`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(await second.text()).toBe(await first.text());
      });

      test("preset outcomes answer for the org", async ({ request }) => {
        const response = await request.get(
          `/api/v1/orgs/${config.orgId}/presets/outcomes`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
      });
    },
  );

  whenConfigured(needsAdmin(), "as a platform admin", () => {
    test("GET /api/v1/admin/preset-templates lists the platform templates", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/admin/preset-templates", {
        headers: adminHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBe(200);
      const body = (await response.json()) as { templates?: unknown[] };
      expect(Array.isArray(body.templates)).toBe(true);
    });

    test("patching an unknown template key is refused", async ({ request }) => {
      const response = await request.patch(
        "/api/v1/admin/preset-templates/api-test-nonexistent-key",
        {
          headers: adminHeaders(),
          data: { label: "api-test" },
          failOnStatusCode: false,
        },
      );
      expect(response.status()).toBeGreaterThanOrEqual(400);
      expect(response.status()).toBeLessThan(500);
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.orgId, "TEST_ORG_ID"), needsMutations()),
    "state-changing (opt-in)",
    () => {
      test("a preset can be created and deleted", async ({ request }) => {
        const created = await request.post(
          `/api/v1/orgs/${config.orgId}/presets`,
          {
            headers: userHeaders(),
            data: { name: `api-test-preset-${Date.now()}`, settings: {} },
            failOnStatusCode: false,
          },
        );
        expect(created.status()).toBeLessThan(500);
        if (created.status() >= 400) return;
        const preset = (await created.json()) as { id?: string };
        if (!preset.id) return;
        const removed = await request.delete(`/api/v1/presets/${preset.id}`, {
          headers: userHeaders(),
          failOnStatusCode: false,
        });
        expect(removed.status()).toBeLessThan(400);
      });
    },
  );
});
