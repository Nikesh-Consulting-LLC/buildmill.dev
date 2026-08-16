/**
 * The endpoint catalog — the suite's map of the api.
 *
 * `endpoints.json` is generated from the running FastAPI app by
 * `tools/generate-catalog.py`, so it cannot drift from the routers by hand.
 * Every category spec asks this module for the operations carrying its tag and
 * asserts the auth boundary of each one, which means a router added tomorrow
 * shows up as an untested tag in `security.spec.ts` rather than as silence.
 */

import fs from "node:fs";
import path from "node:path";

import { SUITE_ROOT } from "./config";

export type AuthKind =
  | "none"
  | "user_jwt"
  | "platform_admin"
  | "worker_token"
  | "basic_worker_token"
  | "report_key"
  | "scoped_key";

export type Endpoint = {
  method: string;
  path: string;
  tag: string;
  auth: AuthKind;
  /** True only for the handful of endpoints meant to answer with no credential. */
  public: boolean;
  op: string;
  params: Record<string, string>;
  body: boolean;
  mutating: boolean;
};

export const endpoints: Endpoint[] = JSON.parse(
  fs.readFileSync(path.join(SUITE_ROOT, "endpoints.json"), "utf8"),
);

export const tags: string[] = [...new Set(endpoints.map((e) => e.tag))].sort();

/** Operations belonging to one or more router tags — how specs select theirs. */
export function endpointsFor(...wanted: string[]): Endpoint[] {
  const set = new Set(wanted);
  return endpoints.filter((e) => set.has(e.tag));
}

/**
 * A path parameter value that survives FastAPI's own validation.
 *
 * This matters more than it looks: a parameter that fails to parse answers 422
 * *before* the auth dependency ever runs, so a boundary test fed "x" for an
 * integer id would pass while proving nothing about authentication. Ints get a
 * number, everything else gets a well-formed uuid, which is what the id-shaped
 * majority of these routes expect.
 */
export const NONEXISTENT_UUID = "00000000-0000-0000-0000-000000000000";

export function sampleValue(name: string, type: string): string {
  if (type === "integer" || type === "number") return "1";
  // Path-converter parameters (`{key:path}`, `{path:path}`) accept slashes and
  // must not be empty; a uuid would read oddly in a template key.
  if (name === "key" || name === "path" || name === "section_key") {
    return "api-test-placeholder";
  }
  if (name === "slug") return "api-test-placeholder";
  if (name === "model") return "api-test/placeholder-model";
  if (name === "name") return "API_TEST_PLACEHOLDER";
  if (name === "org_shortname") return "api-test-org";
  if (name === "project_spec") return "api-test-project";
  if (name === "owner") return "api-test-owner";
  if (name === "repo") return "api-test-repo";
  if (name === "section_type") return "section";
  return NONEXISTENT_UUID;
}

/** Fill `{param}` placeholders with values that reach the auth check. */
export function fillPath(endpoint: Endpoint): string {
  return endpoint.path.replace(/{([^}]+)}/g, (_match, name: string) =>
    encodeURIComponent(sampleValue(name, endpoint.params[name] ?? "string")).replace(
      /%2F/g,
      "/",
    ),
  );
}

/** A stable, readable test title. */
export function label(endpoint: Endpoint): string {
  return `${endpoint.method} ${endpoint.path}`;
}
