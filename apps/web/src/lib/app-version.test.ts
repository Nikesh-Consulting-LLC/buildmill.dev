/** Unit tests for `formatVersion` — the git-describe → display transform
 * shown under the logo (US-51.1). Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { formatVersion } from "./app-version.ts";

test("a build sitting exactly on a release tag shows the bare version", () => {
  assert.equal(formatVersion("2026.07.29.1"), "2026.07.29.1");
});

test("drift from the release compresses to a count, dropping the hash", () => {
  assert.equal(formatVersion("2026.07.29.1-7-g3147da2"), "2026.07.29.1 +7");
});

test("the bare-SHA fallback (no reachable tag) passes through", () => {
  assert.equal(formatVersion("3147da2"), "3147da2");
});

test("a same-day second release keeps its counter intact", () => {
  assert.equal(formatVersion("2026.07.29.2-12-gdeadbee"), "2026.07.29.2 +12");
});
