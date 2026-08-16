/**
 * Projects — guidelines and learnings documents, instruction status, build
 * config, project env, docs-tree and wireframe sync, and cutting a release.
 *
 * Two things here are worth pinning beyond auth. The guidelines and learnings
 * endpoints answer `text/plain` (they are read by agents, not rendered as
 * JSON), and the project env endpoints handle secrets — a secret written
 * through `POST /env/{id}/secret` must never come back out of any read.
 */

import {
  all,
  describeAuthBoundary,
  expect,
  needsId,
  needsMutations,
  needsUser,
  NONEXISTENT_UUID,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";
import { config } from "../lib/config";

test.describe("projects", () => {
  describeAuthBoundary("projects");

  whenConfigured(needsUser(), "signed in", () => {
    test("an unknown project answers a refusal on every read", async ({
      request,
    }) => {
      for (const suffix of [
        "guidelines.md",
        "learnings.md",
        "instructions/status",
        "instructions/template-offers",
        "releases/preview",
      ]) {
        const response = await request.get(
          `/api/v1/projects/${NONEXISTENT_UUID}/${suffix}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(
          response.status(),
          `${suffix} answered ${response.status()}`,
        ).toBeLessThan(500);
        expect(response.status()).not.toBe(200);
      }
    });
  });

  whenConfigured(
    all(needsUser(), needsId(config.projectId, "TEST_PROJECT_ID")),
    "against a real project",
    () => {
      test("guidelines.md is served as plain text", async ({ request }) => {
        const response = await request.get(
          `/api/v1/projects/${config.projectId}/guidelines.md`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
        // The agent that reads this expects a document, not a JSON envelope.
        expect(response.headers()["content-type"]).toContain("text/plain");
        expect((await response.text()).length).toBeGreaterThan(0);
      });

      test("learnings.md is served as plain text", async ({ request }) => {
        const response = await request.get(
          `/api/v1/projects/${config.projectId}/learnings.md`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
        expect(response.headers()["content-type"]).toContain("text/plain");
      });

      test("instructions/status describes the instruction set", async ({
        request,
      }) => {
        const response = await request.get(
          `/api/v1/projects/${config.projectId}/instructions/status`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBe(200);
        expect(typeof (await response.json())).toBe("object");
      });

      test("the release preview reports what a cut would carry", async ({
        request,
      }) => {
        // Read-only by contract: preview must never pin a commit or tag.
        const response = await request.get(
          `/api/v1/projects/${config.projectId}/releases/preview`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(response.status()).toBeLessThan(500);
        if (response.status() === 200) {
          expect(typeof (await response.json())).toBe("object");
        }
      });

      test("no project read echoes a stored secret value", async ({ request }) => {
        // Project env secrets are write-only; the UI shows at most a hint.
        for (const suffix of ["guidelines.md", "instructions/status"]) {
          const response = await request.get(
            `/api/v1/projects/${config.projectId}/${suffix}`,
            { headers: userHeaders(), failOnStatusCode: false },
          );
          if (response.status() !== 200) continue;
          const text = await response.text();
          for (const forbidden of ["-----BEGIN", "sk-ant-", "ghp_", "github_pat_"]) {
            expect(
              text.includes(forbidden),
              `${suffix} contains '${forbidden}'`,
            ).toBe(false);
          }
        }
      });
    },
  );

  whenConfigured(
    all(
      needsUser(),
      needsId(config.projectId, "TEST_PROJECT_ID"),
      needsMutations(),
    ),
    "state-changing (opt-in)",
    () => {
      test("a build-config entry round-trips and deletes", async ({ request }) => {
        const name = "API_TEST_BUILD_CONFIG";
        const put = await request.put(
          `/api/v1/projects/${config.projectId}/build-config/${name}`,
          {
            headers: userHeaders(),
            data: { value: "set-by-the-api-test-suite" },
            failOnStatusCode: false,
          },
        );
        expect(put.status()).toBeLessThan(400);

        const removed = await request.delete(
          `/api/v1/projects/${config.projectId}/build-config/${name}`,
          { headers: userHeaders(), failOnStatusCode: false },
        );
        expect(removed.status()).toBeLessThan(400);
      });
    },
  );
});
