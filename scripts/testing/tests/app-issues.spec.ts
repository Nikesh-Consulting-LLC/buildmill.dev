/**
 * App issue ingestion — `POST /api/v1/report/{deployment_id}/issues`.
 *
 * The only endpoint called by *other people's* running applications, so it is
 * the most exposed surface in the api. Its design says two things that this
 * spec exists to hold:
 *
 *  - **One generic 401.** A wrong key, an unknown deployment id, a malformed
 *    one and reporting-switched-off must be indistinguishable from outside, or
 *    a stranger can walk the endpoint to enumerate which deployments exist.
 *  - **A wildcard CORS envelope, deliberately without credentials.** The caller
 *    runs on an origin the factory has never heard of, and the endpoint
 *    authenticates on a header rather than a cookie — so the response must
 *    carry `Allow-Origin: *` and must NOT carry `Allow-Credentials`, which a
 *    browser rejects alongside a wildcard.
 */

import {
  describeAuthBoundary,
  expect,
  NONEXISTENT_UUID,
  test,
} from "../lib/suite";

const REPORT_PATH = `/api/v1/report/${NONEXISTENT_UUID}/issues`;

test.describe("app-issues", () => {
  describeAuthBoundary("app-issues");

  test("a report with no key is refused", async ({ request }) => {
    const response = await request.post(REPORT_PATH, {
      data: { source: "automated", message: "api test" },
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(401);
  });

  test("every failure mode answers the same 401 body", async ({ request }) => {
    const attempts = [
      { id: NONEXISTENT_UUID, key: "" },
      { id: NONEXISTENT_UUID, key: "forged-report-key" },
      { id: "not-a-uuid", key: "forged-report-key" },
      { id: "11111111-1111-1111-1111-111111111111", key: "another-forged-key" },
    ];
    const bodies = new Set<string>();
    for (const attempt of attempts) {
      const response = await request.post(
        `/api/v1/report/${attempt.id}/issues`,
        {
          headers: { "X-Report-Key": attempt.key },
          data: { source: "automated", message: "api test" },
          failOnStatusCode: false,
        },
      );
      expect(
        response.status(),
        `${attempt.id} / '${attempt.key}' answered ${response.status()}`,
      ).toBe(401);
      bodies.add(await response.text());
    }
    expect(
      bodies.size,
      `the 401 body differs between failure modes: ${[...bodies].join(" | ")}`,
    ).toBe(1);
  });

  test("the preflight is answered for any origin", async ({ request }) => {
    const response = await request.fetch(REPORT_PATH, {
      method: "OPTIONS",
      headers: {
        Origin: "https://some-customers-app.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type,x-report-key",
      },
      failOnStatusCode: false,
    });
    expect(response.status()).toBe(204);
    const headers = response.headers();
    expect(headers["access-control-allow-origin"]).toBe("*");
    expect(headers["access-control-allow-methods"] ?? "").toContain("POST");
    expect((headers["access-control-allow-headers"] ?? "").toLowerCase()).toContain(
      "x-report-key",
    );
  });

  test("the wildcard is never paired with Allow-Credentials", async ({
    request,
  }) => {
    // A browser rejects that combination outright, which would block every
    // report the endpoint is meant to accept.
    for (const method of ["OPTIONS", "POST"] as const) {
      const response = await request.fetch(REPORT_PATH, {
        method,
        headers: {
          Origin: "https://some-customers-app.example",
          "Access-Control-Request-Method": "POST",
          "X-Report-Key": "forged-report-key",
        },
        ...(method === "POST" ? { data: { message: "api test" } } : {}),
        failOnStatusCode: false,
      });
      // A 500 escapes through Starlette's outermost error middleware, which
      // sits OUTSIDE the CORS layer — so the headers below would be missing for
      // a reason that has nothing to do with CORS. Say which one it is.
      expect(
        response.status(),
        `${method} crashed (${response.status()}); the CORS envelope cannot be judged`,
      ).toBeLessThan(500);
      const headers = response.headers();
      expect(headers["access-control-allow-origin"]).toBe("*");
      expect(
        headers["access-control-allow-credentials"],
        `${method} carried Allow-Credentials alongside the wildcard`,
      ).toBeUndefined();
    }
  });

  test("the wildcard does not widen the rest of the api", async ({ request }) => {
    // The report envelope is added by path prefix; an unrelated endpoint must
    // still answer under the configured allow-list.
    const response = await request.get("/api/v1/health", {
      headers: { Origin: "https://some-customers-app.example" },
      failOnStatusCode: false,
    });
    expect(response.headers()["access-control-allow-origin"]).not.toBe("*");
  });

  test("a refused report tells the sender nothing about the deployment", async ({
    request,
  }) => {
    const response = await request.post(REPORT_PATH, {
      headers: { "X-Report-Key": "forged-report-key" },
      data: { source: "automated", message: "api test" },
      failOnStatusCode: false,
    });
    const text = (await response.text()).toLowerCase();
    for (const leak of ["deployment", "project", "org", "not found", "disabled"]) {
      expect(text.includes(leak), `the 401 body mentions '${leak}'`).toBe(false);
    }
  });
});
