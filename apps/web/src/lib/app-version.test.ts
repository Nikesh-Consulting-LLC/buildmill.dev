/** Unit tests for the build stamp shown in the footer (US-51.1, US-91.16).
 * Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  appVersion,
  formatVersion,
  parseStamp,
  versionDetail,
} from "./app-version.ts";

const STAMP = [
  "version=2026.08.14.1-7-g3147da2",
  "commit=3147da2c0ffee1234567890abcdef1234567890a",
  "ref=prod",
  "built_at=2026-08-14T09:12:00Z",
].join("\n");

// --- the git-describe transform, unchanged from US-51.1 ------------------

test("a build sitting exactly on a release tag shows the bare version", () => {
  assert.equal(formatVersion("2026.07.29.1"), "2026.07.29.1");
});

test("drift from the release compresses to a count, dropping the hash", () => {
  assert.equal(formatVersion("2026.07.29.1-7-g3147da2"), "2026.07.29.1 +7");
});

test("the bare-SHA fallback (no reachable tag) passes through", () => {
  assert.equal(formatVersion("3147da2"), "3147da2");
});

// --- the stamp ------------------------------------------------------------

test("a tagged stamp carries version, drift, commit, branch and time", () => {
  const s = parseStamp(STAMP);
  assert.equal(s.version, "2026.08.14.1");
  assert.equal(s.drift, 7);
  assert.equal(s.commit, "3147da2c0ffee1234567890abcdef1234567890a");
  assert.equal(s.ref, "prod");
  assert.equal(s.builtAt, "2026-08-14T09:12:00Z");
  assert.match(appVersion(STAMP), /^2026\.08\.14\.1 \+7 · /);
});

test("a build sitting on the tag shows no drift", () => {
  const s = parseStamp("version=2026.08.14.1\ncommit=abc1234");
  assert.equal(s.drift, 0);
  assert.equal(appVersion("version=2026.08.14.1\ncommit=abc1234"), "2026.08.14.1");
});

test("an untagged build shows only when it was built", () => {
  // `git describe --always` with no matching tag — the state this repository
  // is in today, so it is the case that matters most. UAT: a bare sha is
  // noise in a footer; the time is what a reader can act on, and the sha
  // stays one hover away.
  const raw = [
    "version=0230b43",
    "commit=0230b43727fed5c0bfd3ab4c40e98d4c9ce2b6b8",
    "ref=prod",
    "built_at=2026-08-14T09:12:00Z",
  ].join("\n");
  const stamp = parseStamp(raw);
  assert.equal(stamp.version, null);
  assert.equal(stamp.commit, "0230b43727fed5c0bfd3ab4c40e98d4c9ce2b6b8");

  const shown = appVersion(raw);
  assert.ok(!/commit/.test(shown), `showed a sha: ${shown}`);
  assert.ok(!/\d{4}\.\d{2}\.\d{2}\.\d/.test(shown), "claimed a version");
  assert.match(
    versionDetail(raw) ?? "",
    /0230b43727fed5c0bfd3ab4c40e98d4c9ce2b6b8/
  );
});

test("a tagged build keeps its version beside the time", () => {
  assert.match(appVersion(STAMP), /^2026\.08\.14\.1 \+7 · /);
});

test("with no timestamp at all, the sha beats saying nothing", () => {
  assert.equal(appVersion("commit=deadbeefdeadbeef"), "commit deadbee");
});

test("a missing stamp is a dev build", () => {
  assert.equal(appVersion(null), "dev");
  assert.equal(appVersion(""), "dev");
  assert.equal(appVersion("   "), "dev");
});

test("a malformed stamp degrades to dev rather than a broken string", () => {
  assert.equal(appVersion("=\n=\n"), "dev");
  assert.equal(versionDetail("=\n=\n"), undefined);
});

test("a legacy bare-describe stamp still reads correctly", () => {
  // What builds deployed before this story carry.
  assert.equal(appVersion("2026.07.29.1-463-gabc1234"), "2026.07.29.1 +463");
  assert.equal(appVersion("3147da2"), "commit 3147da2");
});

test("the detail line spells out sha, branch and exact time", () => {
  const detail = versionDetail(STAMP) ?? "";
  assert.match(detail, /7 commits since/);
  assert.match(detail, /commit 3147da2c0ffee1234567890abcdef1234567890a/);
  assert.match(detail, /branch prod/);
  assert.match(detail, /built 2026-08-14T09:12:00Z/);
});
