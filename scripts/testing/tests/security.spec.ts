/**
 * Cross-cutting rules — the ones that belong to no single router.
 *
 * Four things are asserted here that would otherwise have no home:
 *
 *  1. **Nothing is reachable without a credential.** A sweep of the whole
 *     catalog, so a route added without a guard is caught the day it lands.
 *  2. **A 422 never echoes what was sent.** A request body can carry a pasted
 *     PAT or an SSH key, and pydantic's default error serialization includes
 *     the offending `input` — the api strips it, and this proves it still does.
 *  3. **CORS stays an allow-list.** Everything except the public report
 *     endpoint must refuse an unknown origin.
 *  4. **Every router tag has an owning spec.** The meta-test that keeps this
 *     suite honest as the api grows.
 */

import fs from "node:fs";
import path from "node:path";

import { endpoints, tags } from "../lib/catalog";
import { expect, fillPath, label, test } from "../lib/suite";
import { SUITE_ROOT } from "../lib/config";

test.describe("security", () => {
  test("the whole surface refuses an anonymous caller, and none of it crashes", async ({
    request,
  }) => {
    // The suite's backstop. Category specs assert this per endpoint too, but
    // this one sweep is what makes "an unguarded route cannot land unnoticed"
    // true rather than aspirational. Both conditions ride one pass over the
    // catalog: a 2xx means the guard is missing, and a 5xx means the refusal
    // path itself is broken — which is a free denial-of-service, since it costs
    // the caller nothing and the server a crash report.
    // Two hundred-odd sequential requests; against a remote deployment that is
    // minutes, not seconds, so it gets its own budget rather than the default.
    test.setTimeout(300_000);

    const guarded = endpoints.filter((e) => !e.public);
    expect(guarded.length).toBeGreaterThan(200);

    const reachable: string[] = [];
    const crashed: string[] = [];
    for (const endpoint of guarded) {
      const response = await request.fetch(fillPath(endpoint), {
        method: endpoint.method,
        ...(endpoint.body && endpoint.method !== "GET" ? { data: {} } : {}),
        failOnStatusCode: false,
      });
      const status = response.status();
      if (status < 400) reachable.push(`${label(endpoint)} → ${status}`);
      else if (status >= 500) crashed.push(`${label(endpoint)} → ${status}`);
    }
    expect(
      reachable,
      `these operations answered without a credential:\n${reachable.join("\n")}`,
    ).toEqual([]);
    expect(
      crashed,
      `these operations crashed on an unauthenticated call:\n${crashed.join("\n")}`,
    ).toEqual([]);
  });

  test("a 422 body never echoes the input it rejected", async ({ request }) => {
    // The endpoint is public and needs `installation_id` as an int, so a
    // string is a guaranteed validation failure with a distinctive value.
    const canary = "api-test-canary-value-9f3c";
    const response = await request.get(
      `/api/v1/github/install/callback?installation_id=${canary}`,
      { failOnStatusCode: false, maxRedirects: 0 },
    );
    expect(response.status()).toBe(422);

    const text = await response.text();
    expect(
      text.includes(canary),
      "the 422 body echoed the rejected input — a pasted key would leak the same way",
    ).toBe(false);

    const body = (await response.json()) as { detail: Record<string, unknown>[] };
    expect(Array.isArray(body.detail)).toBe(true);
    for (const error of body.detail) {
      expect(
        "input" in error,
        "a validation error carried an 'input' field",
      ).toBe(false);
      // The rest of the entry must survive, or the message stops being useful.
      expect(error).toHaveProperty("loc");
      expect(error).toHaveProperty("msg");
    }
  });

  test("CORS stays an allow-list outside the report endpoint", async ({
    request,
  }) => {
    for (const target of [
      "/api/v1/health",
      "/api/v1/auth/me",
      "/api/v1/worker/pool",
    ]) {
      const response = await request.get(target, {
        headers: { Origin: "https://not-an-allowed-origin.example" },
        failOnStatusCode: false,
      });
      expect(
        response.headers()["access-control-allow-origin"],
        `${target} allowed an unlisted origin`,
      ).not.toBe("*");
    }
  });

  test("a preflight from an unlisted origin is not granted", async ({
    request,
  }) => {
    const response = await request.fetch("/api/v1/auth/me", {
      method: "OPTIONS",
      headers: {
        Origin: "https://not-an-allowed-origin.example",
        "Access-Control-Request-Method": "GET",
      },
      failOnStatusCode: false,
    });
    expect(response.headers()["access-control-allow-origin"]).not.toBe("*");
  });

  test("refusals answer JSON with a detail, not HTML", async ({ request }) => {
    // The web app renders `detail` verbatim; an HTML error page shows up there
    // as a wall of markup.
    for (const target of ["/api/v1/auth/me", "/api/v1/worker/pool"]) {
      const response = await request.get(target, { failOnStatusCode: false });
      expect(response.headers()["content-type"] ?? "").toContain("application/json");
      expect(await response.json()).toHaveProperty("detail");
    }
  });

  test("the server does not advertise its stack in response headers", async ({
    request,
  }) => {
    const headers = (await request.get("/api/v1/health")).headers();
    for (const header of ["x-powered-by", "x-aspnet-version"]) {
      expect(headers[header], `response carries ${header}`).toBeUndefined();
    }
  });

  test("every router tag is owned by a category spec", async () => {
    // The meta-test. A new router picks up a new tag, and unless some spec
    // claims it in `describeAuthBoundary(...)`, its operations would be tested
    // only by the two sweeps above — which is coverage, but not ownership.
    const testDir = path.join(SUITE_ROOT, "tests");
    const claimed = new Set<string>();
    for (const file of fs.readdirSync(testDir).filter((f) => f.endsWith(".spec.ts"))) {
      const source = fs.readFileSync(path.join(testDir, file), "utf8");
      for (const match of source.matchAll(/describeAuthBoundary\(([^)]*)\)/g)) {
        for (const quoted of match[1].matchAll(/["']([^"']+)["']/g)) {
          claimed.add(quoted[1]);
        }
      }
    }
    // `untagged` is the health endpoint, which health.spec.ts covers by hand.
    const unowned = tags.filter((t) => t !== "untagged" && !claimed.has(t));
    expect(
      unowned,
      `these router tags have no owning spec: ${unowned.join(", ")}`,
    ).toEqual([]);
  });

  test("the catalog matches the api the tests are pointed at", async ({
    request,
  }) => {
    // If someone edits a router without regenerating endpoints.json, the suite
    // would keep testing yesterday's api and pass. This says so instead.
    const response = await request.get("/openapi.json", {
      failOnStatusCode: false,
    });
    test.skip(
      response.status() !== 200,
      "this deployment does not serve /openapi.json",
    );
    const spec = (await response.json()) as {
      paths: Record<string, Record<string, unknown>>;
    };

    const live = new Set<string>();
    for (const [p, operations] of Object.entries(spec.paths)) {
      for (const method of Object.keys(operations)) {
        live.add(`${method.toUpperCase()} ${p}`);
      }
    }
    const known = new Set(endpoints.map((e) => `${e.method} ${e.path}`));
    const missing = [...live].filter((op) => !known.has(op));
    expect(
      missing,
      `the api serves operations the catalog does not know:\n${missing.join("\n")}\n` +
        "Regenerate with: apps/api/.venv/Scripts/python scripts/testing/tools/generate-catalog.py",
    ).toEqual([]);
  });
});
