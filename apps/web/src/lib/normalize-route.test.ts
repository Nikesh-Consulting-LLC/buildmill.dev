/** Unit tests for `normalizeRoute` — the route-template normalization
 * US-62.7's Web Vitals reporter uses so rows aggregate by page instead of
 * fragmenting one-per-record. Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { normalizeRoute } from "./normalize-route.ts";

test("a bare UUID path segment is replaced", () => {
  assert.equal(
    normalizeRoute("/issues/3f2a9c1e-1234-4abc-9def-0123456789ab"),
    "/issues/:id",
  );
});

test("a path with no UUID segment passes through unchanged", () => {
  assert.equal(normalizeRoute("/admin/analytics/runs"), "/admin/analytics/runs");
});

test("multiple UUID segments each normalize", () => {
  assert.equal(
    normalizeRoute(
      "/projects/11111111-1111-1111-1111-111111111111/deployments/22222222-2222-2222-2222-222222222222",
    ),
    "/projects/:id/deployments/:id",
  );
});

test("the root path stays the root path", () => {
  assert.equal(normalizeRoute("/"), "/");
});

test("an uppercase UUID still matches", () => {
  assert.equal(
    normalizeRoute("/runs/3F2A9C1E-1234-4ABC-9DEF-0123456789AB"),
    "/runs/:id",
  );
});
