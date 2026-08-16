/**
 * Shared building blocks for the category specs.
 *
 * Two ideas carry most of the suite:
 *
 *  - **The auth boundary is generated, not written.** `describeAuthBoundary`
 *    turns the catalog into one test per operation, so coverage of a router is
 *    a property of the router existing rather than of somebody remembering.
 *    These tests are also the only ones safe to point at any environment: every
 *    request is refused before the handler runs, so nothing is dispatched,
 *    deployed or deleted.
 *
 *  - **A missing credential skips, it never fails.** Read tests announce which
 *    variable would enable them, so a green run with skips is legible instead
 *    of being mistaken for full coverage.
 */

import { expect, test, type APIRequestContext } from "@playwright/test";

import { config, readTokens } from "./config";
import {
  endpointsFor,
  fillPath,
  label,
  NONEXISTENT_UUID,
  type Endpoint,
} from "./catalog";

export { expect, test };
export { config, NONEXISTENT_UUID };
export { endpointsFor as endpointsForTag, fillPath, label };
export type { Endpoint };

export const tokens = readTokens();

/** A syntactically well-formed JWT that no Supabase project ever signed. */
export const FORGED_JWT = [
  "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
  "eyJzdWIiOiIwMDAwMDAwMC0wMDAwLTAwMDAtMDAwMC0wMDAwMDAwMDAwMDAiLCJhdWQiOiJhdXRoZW50aWNhdGVkIiwiZXhwIjo0MTAyNDQ0ODAwfQ",
  "bm90LWEtcmVhbC1zaWduYXR1cmU",
].join(".");

export const bearer = (token: string) => ({ Authorization: `Bearer ${token}` });

export const userHeaders = () => bearer(tokens.user);
export const adminHeaders = () => bearer(tokens.admin);
export const workerHeaders = () => ({ "X-Worker-Token": tokens.worker });

/**
 * Send one catalogued operation. `failOnStatusCode` stays off everywhere in
 * this suite — a 4xx is frequently the assertion, not an accident.
 */
export async function call(
  request: APIRequestContext,
  endpoint: Endpoint,
  options: { headers?: Record<string, string>; data?: unknown } = {},
) {
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  const init: Parameters<APIRequestContext["fetch"]>[1] = {
    method: endpoint.method,
    headers,
    failOnStatusCode: false,
  };
  // Send a body only where the schema declares one; an unexpected body on a
  // GET is a different test than the one being run here.
  if (endpoint.body && endpoint.method !== "GET") {
    init.data = options.data ?? {};
  } else if (options.data !== undefined) {
    init.data = options.data;
  }
  return request.fetch(fillPath(endpoint), init);
}

/** The credential shape that should NOT get past a given guard. */
function forgedCredential(endpoint: Endpoint): Record<string, string> {
  switch (endpoint.auth) {
    case "worker_token":
      return { "X-Worker-Token": "forged-worker-token-000" };
    case "basic_worker_token":
      return {
        Authorization: `Basic ${Buffer.from("x:forged-worker-token-000").toString("base64")}`,
      };
    case "report_key":
      return { "X-Report-Key": "forged-report-key-000" };
    case "scoped_key":
      return { "X-Factory-Mcp-Key": "forged-scoped-key-000", "X-Api-Key": "forged-scoped-key-000" };
    default:
      return bearer(FORGED_JWT);
  }
}

/**
 * One test per operation in the named tags: no credential is refused, and a
 * forged credential of the right *shape* is refused too.
 *
 * The second half is the one that earns its keep — a guard that only checks a
 * header is present, or that trusts an unverified JWT, passes the first check
 * and fails this one.
 */
export function describeAuthBoundary(...tags: string[]): void {
  const guarded = endpointsFor(...tags).filter((e) => !e.public);

  test.describe("auth boundary", () => {
    for (const endpoint of guarded) {
      test(`${label(endpoint)} refuses an unauthenticated call`, async ({
        request,
      }) => {
        const anonymous = await call(request, endpoint);
        expect(
          anonymous.status(),
          `${label(endpoint)} answered ${anonymous.status()} with no credential`,
        ).toBe(401);

        const forged = await call(request, endpoint, {
          headers: forgedCredential(endpoint),
        });
        expect(
          forged.status(),
          `${label(endpoint)} answered ${forged.status()} to a forged ${endpoint.auth}`,
        ).toBe(401);
      });
    }
  });
}

/**
 * A block that runs only when its credential exists. The skip carries the
 * variable name, so the summary tells you how to turn the tests on.
 */
export function whenConfigured(
  requirement: { ok: boolean; hint: string },
  title: string,
  body: () => void,
): void {
  test.describe(title, () => {
    test.skip(!requirement.ok, requirement.hint);
    body();
  });
}

export const needsUser = () => ({
  ok: Boolean(tokens.user),
  hint: "set TEST_USER_EMAIL/TEST_USER_PASSWORD (or TEST_ACCESS_TOKEN)",
});
export const needsAdmin = () => ({
  ok: Boolean(tokens.admin),
  hint: "set TEST_ADMIN_EMAIL/TEST_ADMIN_PASSWORD (or TEST_ADMIN_ACCESS_TOKEN)",
});
export const needsWorker = () => ({
  ok: Boolean(tokens.worker),
  hint: "set TEST_WORKER_TOKEN",
});
export const needsId = (value: string, variable: string) => ({
  ok: Boolean(value),
  hint: `set ${variable}`,
});
export const needsMutations = () => ({
  ok: config.allowMutations,
  hint: "state-changing test — set ALLOW_MUTATIONS=1 against a disposable environment",
});

/** Combine requirements; the first unmet one supplies the skip reason. */
export function all(
  ...requirements: { ok: boolean; hint: string }[]
): { ok: boolean; hint: string } {
  const unmet = requirements.find((r) => !r.ok);
  return unmet ?? { ok: true, hint: "" };
}

/**
 * Assert a response is JSON-shaped and carries FastAPI's `detail` string.
 * Every refusal in this api answers that way, and the web app renders it —
 * a refusal that answers with a bare body is a UI regression waiting to happen.
 */
export async function expectDetail(response: {
  status(): number;
  json(): Promise<unknown>;
}): Promise<string> {
  const body = (await response.json()) as { detail?: unknown };
  expect(body, `${response.status()} answered without a body`).toBeTruthy();
  expect(
    typeof body.detail === "string" || Array.isArray(body.detail),
    `${response.status()} answered without a 'detail' field`,
  ).toBe(true);
  return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
}
