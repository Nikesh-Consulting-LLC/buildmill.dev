/**
 * Health and build identity.
 *
 * `GET /api/v1/health` is the endpoint the prod deploy workflow polls before it
 * calls a release good, and the build stamp is what answers "are web and api
 * the same build" without SSH (US-91.16). Both are asserted here because both
 * are load-bearing for a release.
 */

import { expect, test } from "../lib/suite";

test.describe("health", () => {
  test("GET /api/v1/health answers ok without any credential", async ({
    request,
  }) => {
    const response = await request.get("/api/v1/health", {
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(200);
    expect(await response.json()).toMatchObject({ status: "ok" });
  });

  test("health carries the four build-stamp fields", async ({ request }) => {
    const body = (await (await request.get("/api/v1/health")).json()) as Record<
      string,
      unknown
    >;
    // Empty strings are correct for a dev checkout with no VERSION file; the
    // keys themselves must always be present, because the footer reads them.
    for (const key of ["build_version", "build_commit", "build_ref", "built_at"]) {
      expect(body, `health is missing ${key}`).toHaveProperty(key);
      expect(typeof body[key]).toBe("string");
    }
  });

  test("the OpenAPI document is served and describes the whole surface", async ({
    request,
  }) => {
    const response = await request.get("/openapi.json", {
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(200);
    const spec = (await response.json()) as { paths: Record<string, unknown> };
    // A sanity floor, not a pin: this api had 207 paths when the suite was
    // written, and a document that suddenly describes a handful means the app
    // booted with routers missing.
    expect(Object.keys(spec.paths).length).toBeGreaterThan(150);
  });

  test("an unknown path answers 404 with a detail body", async ({ request }) => {
    const response = await request.get("/api/v1/definitely-not-a-route", {
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(404);
    expect(await response.json()).toHaveProperty("detail");
  });

  test("a wrong method on a real route answers 405", async ({ request }) => {
    const response = await request.delete("/api/v1/health", {
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(405);
  });
});
