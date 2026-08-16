/**
 * Session authentication — the JWT gate every user-facing route sits behind.
 *
 * `verify_token` is one function used by 203 of the 227 operations, so the
 * cases below are the ones that matter most in the whole suite: a bearer token
 * this api did not verify against the project's JWKS must never be accepted.
 */

import {
  describeAuthBoundary,
  expect,
  expectDetail,
  FORGED_JWT,
  needsUser,
  test,
  tokens,
  userHeaders,
  whenConfigured,
} from "../lib/suite";

test.describe("auth", () => {
  describeAuthBoundary("auth");

  test("a missing Authorization header is refused by name", async ({ request }) => {
    const response = await request.get("/api/v1/auth/me", {
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(401);
    expect(await expectDetail(response)).toContain("Missing bearer token");
  });

  test("a non-bearer Authorization scheme is refused", async ({ request }) => {
    const response = await request.get("/api/v1/auth/me", {
      headers: { Authorization: "Basic dXNlcjpwYXNz" },
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(401);
    expect(await expectDetail(response)).toContain("Missing bearer token");
  });

  test("a well-formed but unsigned JWT is refused", async ({ request }) => {
    // The distinction the api must make: this parses as a JWT and claims
    // aud=authenticated with an expiry in 2100. Only signature verification
    // against the project JWKS separates it from a real session.
    const response = await request.get("/api/v1/auth/me", {
      headers: { Authorization: `Bearer ${FORGED_JWT}` },
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(401);
    expect(await expectDetail(response)).toContain("Invalid token");
  });

  test("garbage in the bearer position is refused, not crashed on", async ({
    request,
  }) => {
    for (const value of ["Bearer", "Bearer ", "Bearer ....", "Bearer null"]) {
      const response = await request.get("/api/v1/auth/me", {
        headers: { Authorization: value },
        failOnStatusCode: false,
      });
      expect(
        [401].includes(response.status()),
        `'${value}' answered ${response.status()}`,
      ).toBe(true);
    }
  });

  whenConfigured(needsUser(), "signed in", () => {
    test("GET /api/v1/auth/me returns the caller's identity and org", async ({
      request,
    }) => {
      const response = await request.get("/api/v1/auth/me", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      // 403 is a legitimate answer for a user with no membership — assert the
      // gate opened, then the shape only when it did.
      expect([200, 403]).toContain(response.status());
      if (response.status() === 200) {
        const body = (await response.json()) as Record<string, string>;
        expect(body).toHaveProperty("user_id");
        expect(body).toHaveProperty("email");
        expect(body).toHaveProperty("org_id");
        expect(body.user_id).toMatch(
          /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
        );
      }
    });

    test("the session token is not echoed back to the caller", async ({
      request,
    }) => {
      // A response that contained the access token would put a credential into
      // browser logs and any proxy in the path.
      const response = await request.get("/api/v1/auth/me", {
        headers: userHeaders(),
        failOnStatusCode: false,
      });
      expect(await response.text()).not.toContain(tokens.user);
    });
  });
});
