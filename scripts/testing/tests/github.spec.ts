/**
 * GitHub integration — the App install callback, PAT connections, repo and
 * branch listing, and pulling issues.
 *
 * The install callback is one of exactly two endpoints in the api that answer
 * with no credential, because GitHub sends the browser there with no Supabase
 * session attached. Its safety rests entirely on the signed `state`, so the two
 * cases below — an unsigned state, and a tampered one — are the ones that
 * matter. A PAT submitted here must also never come back out of any response.
 */

import {
  describeAuthBoundary,
  expect,
  needsUser,
  test,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("github", () => {
  describeAuthBoundary("github");

  test.describe("install callback (public by necessity)", () => {
    test("a missing installation_id is rejected by the schema", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/github/install/callback", {
        failOnStatusCode: false,
        maxRedirects: 0,
      });
      expect(response.status()).toBe(422);
    });

    test("an unsigned state records nothing and redirects to an error", async ({
      request,
    }) => {
      // The signature is what proves the factory minted this state; without it
      // the callback must not reach `record_github_app_installation`.
      const response = await request.get(
        "/api/v1/github/install/callback?installation_id=1&state=forged-state",
        { failOnStatusCode: false, maxRedirects: 0 },
      );
      expect([302, 303, 307]).toContain(response.status());
      expect(response.headers()["location"] ?? "").toContain("github=error");
    });

    test("a tampered state is refused the same way", async ({ request }) => {
      const response = await request.get(
        "/api/v1/github/install/callback?installation_id=99999&state=" +
          encodeURIComponent("eyJvcmciOiJhbnkifQ.tampered"),
        { failOnStatusCode: false, maxRedirects: 0 },
      );
      expect([302, 303, 307]).toContain(response.status());
      expect(response.headers()["location"] ?? "").toContain("github=error");
    });
  });

  whenConfigured(needsUser(), "signed in", () => {
    test("GET /api/v1/github/connect-url answers an install URL or a clear refusal", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/github/connect-url", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      // A deployment with no GitHub App configured refuses — that is correct,
      // and it must say so rather than 500.
      expect(response.status()).toBeLessThan(500);
      if (response.status() === 200) {
        const body = (await response.json()) as { url?: string };
        expect(typeof body.url).toBe("string");
        expect(body.url).toContain("github.com");
      }
    });

    test("GET /api/v1/github/repos answers without leaking a token", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/github/repos", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      expect(response.status()).toBeLessThan(500);
      const text = await response.text();
      for (const marker of ["ghp_", "ghs_", "github_pat_", "-----BEGIN"]) {
        expect(text.includes(marker), `repos response contains '${marker}'`).toBe(
          false,
        );
      }
    });

    test("a submitted PAT is never echoed back", async ({ request }) => {
      // Deliberately invalid so nothing is stored; what is asserted is that the
      // refusal does not quote the credential (US-3.15 redaction).
      const pat = `ghp_apitestsuite${"0".repeat(20)}`;
      const response = await request.post("/api/v1/github/connections/pat", {
        headers: userHeaders(),
        data: { token: pat },
        failOnStatusCode: false,
      });
      expect(response.status()).toBeLessThan(500);
      expect(
        (await response.text()).includes(pat),
        "the response echoed the submitted PAT",
      ).toBe(false);
    });

    test("branch listing for a repo the org cannot see is refused", async ({
      request,
    }) => {
      const response = await request.get(
        "/api/v1/github/repos/api-test-owner/api-test-repo/branches",
        { headers: userHeaders(), failOnStatusCode: false },
      );
      expect(response.status()).not.toBe(200);
      expect(response.status()).toBeLessThan(500);
    });
  });
});
