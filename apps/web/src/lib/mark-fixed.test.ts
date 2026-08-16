import assert from "node:assert/strict";
import { test } from "node:test";
import { canMarkFixed, markFixedBlockedReason } from "./mark-fixed.ts";

test("a draft bug can be marked fixed", () => {
  assert.equal(canMarkFixed("bug", "draft"), true);
  assert.equal(markFixedBlockedReason("bug", "draft"), null);
});

test("chores and stories can too — any non-feature type", () => {
  assert.equal(canMarkFixed("chore", "ready"), true);
  assert.equal(canMarkFixed("story", "needs-fixes"), true);
  assert.equal(canMarkFixed("story", "planned"), true);
});

test("a feature never can — it completes when its last story does", () => {
  assert.equal(canMarkFixed("feature", "ready"), false);
  assert.match(
    markFixedBlockedReason("feature", "ready") ?? "",
    /last story/,
  );
});

test("an already-complete item is not offered it", () => {
  assert.equal(canMarkFixed("bug", "merged"), false);
  assert.equal(canMarkFixed("bug", "done"), false);
  assert.match(markFixedBlockedReason("bug", "done") ?? "", /already complete/);
});

test("an in-flight run blocks it, exactly as Abandon is blocked", () => {
  assert.equal(canMarkFixed("bug", "queued"), false);
  assert.equal(canMarkFixed("bug", "running"), false);
  assert.match(
    markFixedBlockedReason("bug", "running") ?? "",
    /queued or running/,
  );
});

test("an abandoned item must be restored first", () => {
  assert.equal(canMarkFixed("bug", "ready", "2026-08-16T00:00:00Z"), false);
  assert.match(
    markFixedBlockedReason("bug", "ready", "2026-08-16T00:00:00Z") ?? "",
    /restore/,
  );
});

test("a null abandoned_at is not treated as abandoned", () => {
  assert.equal(canMarkFixed("bug", "ready", null), true);
  assert.equal(canMarkFixed("bug", "ready", undefined), true);
});

test("the predicate and the reason always agree", () => {
  const types = ["bug", "chore", "story", "feature"];
  const statuses = ["draft", "ready", "planned", "queued", "running", "done"];
  for (const type of types) {
    for (const status of statuses) {
      assert.equal(
        canMarkFixed(type, status),
        markFixedBlockedReason(type, status) === null,
        `${type}/${status} disagreed`,
      );
    }
  }
});
