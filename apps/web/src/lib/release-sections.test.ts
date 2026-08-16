import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  compareCases,
  RELEASE_SECTIONS,
  sectionLabel,
  sectionRank,
} from "./release-sections.ts";

/** us-101.2/101.5: the checklist's running order.
 *
 * The order IS the instruction — a tester works top to bottom, and the
 * refusals only make sense after the happy path has created something to
 * refuse. Before this, cases were ordered by `issue_id`, i.e. by a UUID.
 */

const c = (
  title: string,
  section?: string | null,
  sort?: number | null
) => ({ title, section, sort });

test("known sections come in the order a release is worked", () => {
  assert.deepEqual(
    [...RELEASE_SECTIONS],
    ["pre-flight", "happy-path", "refusals", "regression", "other"]
  );
});

test("cases sort into section order regardless of the order given", () => {
  const cases = [
    c("regress", "regression"),
    c("refuse", "refusals"),
    c("smoke", "pre-flight"),
    c("merge", "happy-path"),
  ];
  assert.deepEqual(
    cases.sort(compareCases).map((x) => x.title),
    ["smoke", "merge", "refuse", "regress"]
  );
});

test("within a section, sort wins, then title", () => {
  const cases = [
    c("b", "happy-path", 2),
    c("a", "happy-path", 1),
    c("z", "happy-path", null),
    c("y", "happy-path", null),
  ];
  assert.deepEqual(
    cases.sort(compareCases).map((x) => x.title),
    ["a", "b", "y", "z"]
  );
});

test("a section the agent invented sorts after every known one", () => {
  assert.ok(sectionRank("data-migration") > sectionRank("regression"));
  const cases = [c("custom", "data-migration"), c("known", "other")];
  assert.deepEqual(
    cases.sort(compareCases).map((x) => x.title),
    ["known", "custom"]
  );
});

test("two invented sections stay grouped rather than interleaving", () => {
  const cases = [
    c("b1", "beta"),
    c("a1", "alpha"),
    c("b2", "beta"),
    c("a2", "alpha"),
  ];
  assert.deepEqual(
    cases.sort(compareCases).map((x) => x.title),
    ["a1", "a2", "b1", "b2"]
  );
});

test("a missing section is treated as the last known one, not dropped", () => {
  assert.equal(sectionRank(null), RELEASE_SECTIONS.length);
  assert.equal(sectionLabel(null), "Other");
});

test("an invented section still gets a readable heading", () => {
  assert.equal(sectionLabel("data-migration"), "Data migration");
  assert.equal(sectionLabel("pre-flight"), "Pre-flight");
});
