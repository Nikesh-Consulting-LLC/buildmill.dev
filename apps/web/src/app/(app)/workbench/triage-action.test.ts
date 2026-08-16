import { test } from "node:test";
import assert from "node:assert/strict";
import { triageAction } from "./triage-action.ts";

// us-106.1: the Triage row's one click must name the run the factory would
// actually start. The mapping is pinned here because it mirrors
// `dispatch_kind_for` (migration 255) — if that changes, this should fail.

test("a draft story dispatches planning", () => {
  const a = triageAction("story");
  assert.equal(a.mode, "dispatch");
  assert.equal(a.action, "Dispatch planning");
});

test("a draft bug dispatches an RCA, not 'planning'", () => {
  const a = triageAction("bug");
  assert.equal(a.mode, "dispatch");
  assert.equal(a.action, "Dispatch RCA");
});

test("a draft chore dispatches the build — it has no planning phase", () => {
  const a = triageAction("chore");
  assert.equal(a.mode, "dispatch");
  assert.equal(a.action, "Dispatch build");
});

test("a draft feature drafts its PRD — dispatch would be refused", () => {
  const a = triageAction("feature");
  assert.equal(a.mode, "draft-prd");
  assert.equal(a.action, "Draft PRD");
});

test("an unknown type falls back to planning rather than to nothing", () => {
  const a = triageAction("something-new");
  assert.equal(a.mode, "dispatch");
  assert.equal(a.action, "Dispatch planning");
});

test("every type explains itself in the row's reason", () => {
  for (const t of ["story", "bug", "chore", "feature"]) {
    assert.ok(triageAction(t).reason.startsWith("Draft — "));
  }
});
