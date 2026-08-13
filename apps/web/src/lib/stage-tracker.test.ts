/** Unit tests for `deriveTracker` — a pure projection, so it needs no DOM,
 * no network and no test framework.
 *
 * Run with `npm run test:web` (node --test with native type stripping, Node
 * 22+). Deliberately zero-dependency: stage-tracker.ts imports nothing, and
 * adding a whole JS test toolchain to assert four branches of one pure
 * function would cost more than it protects.
 *
 * US-22.10 is what these cover: in feature/epic build mode the FEATURE owns
 * the code build, so a healthy `planned` story's Dispatch code defers — and
 * a story in trouble does not, because that is the only way out of a stuck
 * batch.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  deriveTracker,
  featureOwnsBuildReason,
  routedPresetIds,
  type TrackerInput,
} from "./stage-tracker.ts";

// ------------------------------------------------------------------ US-55.5

test("routedPresetIds collects and dedupes across workers", () => {
  const rows = [
    { run_routes: { plan: { preset_id: "a" }, code: { preset_id: "b" } } },
    { run_routes: { plan: { preset_id: "a" }, test: { preset_id: "c" } } },
  ];
  assert.deepEqual(routedPresetIds(rows).sort(), ["a", "b", "c"]);
});

test("routedPresetIds answers empty for unrouted agents", () => {
  assert.deepEqual(routedPresetIds([{ run_routes: {} }]), []);
  assert.deepEqual(routedPresetIds([]), []);
});

test("routedPresetIds skips null and malformed routes", () => {
  const rows = [
    { run_routes: null },
    { run_routes: "not-an-object" },
    { run_routes: { plan: null, code: { preset_id: 7 }, test: { preset_id: "" } } },
    { run_routes: { deploy: { preset_id: "ok" } } },
  ];
  assert.deepEqual(routedPresetIds(rows as { run_routes: unknown }[]), ["ok"]);
});

const PARENT = { id: "feat-1", label: "FEAT-1.4", storyCount: 5 };

function story(over: Partial<TrackerInput> = {}): TrackerInput {
  return {
    issueId: "story-1",
    type: "story",
    status: "planned",
    latestRunKind: "plan",
    hasApprovedPlan: true,
    hasApprovedPrd: true,
    hasPrd: true,
    hasChildren: false,
    buildMode: "feature",
    parent: PARENT,
    ...over,
  };
}

test("feature mode + planned: dispatch is offered but disabled, and says why", () => {
  const { action, context } = deriveTracker(story());
  assert.equal(action?.kind, "dispatch");
  assert.equal(action?.label, "Dispatch code");
  // Shown, not hidden — an absent button reads as a missing capability.
  assert.equal(action?.disabled, true);
  assert.equal(action?.reason, featureOwnsBuildReason("FEAT-1.4", 5));
  assert.match(action?.reason ?? "", /FEAT-1\.4 owns the build/);
  assert.match(action?.reason ?? "", /all 5 stories/);
  assert.equal(action?.reasonHref, "/issues/feat-1");
  assert.match(context, /built with its feature/);
});

test("epic mode + planned: same deferral — epic mode batches per feature too", () => {
  const { action } = deriveTracker(story({ buildMode: "epic" }));
  assert.equal(action?.disabled, true);
});

test("feature mode + needs-fixes: the dispatch stays LIVE", () => {
  // The whole subtlety of us-22.10. us-20.5 exempts a troubled story from
  // rule (d) so its fix run can be dispatched from the story page; greying
  // this would leave a broken story held by its own breakage and deadlock
  // the feature.
  const { action } = deriveTracker(
    story({ status: "needs-fixes", latestRunKind: "code" })
  );
  assert.equal(action?.kind, "dispatch");
  assert.equal(action?.label, "Dispatch fix");
  assert.notEqual(action?.disabled, true);
});

test("feature mode + failed: the re-dispatch stays LIVE", () => {
  const { action } = deriveTracker(
    story({ status: "failed", latestRunKind: "code" })
  );
  assert.equal(action?.kind, "dispatch");
  assert.notEqual(action?.disabled, true);
});

test("story mode + planned: the normal, enabled dispatch", () => {
  const { action, context } = deriveTracker(story({ buildMode: "story" }));
  assert.equal(action?.kind, "dispatch");
  assert.equal(action?.label, "Dispatch code");
  assert.notEqual(action?.disabled, true);
  assert.match(context, /plan approved/);
});

test("no parent feature: unaffected in every mode", () => {
  for (const buildMode of ["story", "feature", "epic"] as const) {
    const { action } = deriveTracker(story({ buildMode, parent: null }));
    assert.notEqual(
      action?.disabled,
      true,
      `a parentless story should dispatch in ${buildMode} mode`
    );
  }
});

test("plan-phase actions are unchanged in feature mode", () => {
  // Planning stays per story: us-22.10 changes exactly one branch of the rail.
  const { action } = deriveTracker(
    story({ status: "ready", hasApprovedPlan: false, latestRunKind: null })
  );
  assert.equal(action?.kind, "dispatch");
  assert.equal(action?.label, "Dispatch planning");
  assert.notEqual(action?.disabled, true);
});

test("the reason reads correctly for a one-story feature", () => {
  assert.match(featureOwnsBuildReason("FEAT-1.4", 1), /all 1 story$/);
});

/* US-41.1: the bulk action is no longer gated on build mode.
 *
 * The database and the API stopped refusing `story`-mode batches, but the
 * client still hid the button — a feature's rail dead-ended at "Stories
 * created — the work happens on them" with no action, which is precisely the
 * click-each-story friction the story set out to remove. */

const ROLLUP = {
  total: 6,
  curated: 6,
  planApproved: 0,
  inPlanReview: 0,
  inCodeReview: 0,
  merged: 0,
  planRunActive: false,
  codeRunActive: false,
  inFlightPosition: null,
  troubled: null,
};

function feature(over: Partial<TrackerInput> = {}): TrackerInput {
  return {
    issueId: "feat-1",
    type: "feature",
    status: "ready",
    latestRunKind: null,
    hasApprovedPlan: false,
    hasApprovedPrd: true,
    hasPrd: true,
    hasChildren: true,
    buildMode: "story",
    children: ROLLUP,
    parent: null,
    ...over,
  } as TrackerInput;
}

test("story mode: a feature offers Plan all N — it used to offer nothing", () => {
  const { action } = deriveTracker(feature());
  assert.equal(action?.kind, "batch-dispatch");
  assert.equal(action?.label, "Plan all 6 stories");
});

test("story mode: every plan approved offers Code all N", () => {
  const { action } = deriveTracker(
    feature({ children: { ...ROLLUP, planApproved: 6 } })
  );
  assert.equal(action?.kind, "batch-dispatch");
  assert.equal(action?.label, "Code all 6 stories");
});

test("feature and epic mode still offer it — unchanged", () => {
  for (const buildMode of ["feature", "epic"] as const) {
    const { action } = deriveTracker(feature({ buildMode }));
    assert.equal(
      action?.kind,
      "batch-dispatch",
      `${buildMode} mode should still batch`
    );
  }
});

test("a feature with no stories offers no bulk action", () => {
  const { action } = deriveTracker(
    feature({ hasChildren: false, children: undefined })
  );
  assert.notEqual(action?.kind, "batch-dispatch");
});

/* US-41.2: the curation gate keeps its meaning but stops costing 15 clicks.
 *
 * The rail used to return `action: null` here — a sentence ("15 still in
 * draft; curate them before planning") naming an action the page did not
 * provide. */

test("drafts present: the rail offers Curate all N, counting DRAFTS not total", () => {
  const { action, context } = deriveTracker(
    feature({ children: { ...ROLLUP, total: 15, curated: 0 } })
  );
  assert.equal(action?.kind, "curate-all");
  assert.equal(action?.label, "Curate all 15 stories");
  assert.match(context, /15 still in draft/);
});

test("partly curated: the label counts only what is left", () => {
  const { action } = deriveTracker(
    feature({ children: { ...ROLLUP, total: 15, curated: 14 } })
  );
  assert.equal(action?.label, "Curate all 1 story");
});

test("fully curated: the slot becomes the bulk dispatch, not curation", () => {
  const { action } = deriveTracker(
    feature({ children: { ...ROLLUP, total: 15, curated: 15 } })
  );
  assert.equal(action?.kind, "batch-dispatch");
  assert.equal(action?.label, "Plan all 15 stories");
});
