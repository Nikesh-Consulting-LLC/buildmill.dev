/** US-100.4: a template holds exactly the files a project publishes.
 *
 * The rebuilt template editors render from `templateFiles()`, and the
 * project's Task Instructions tab renders from `INSTRUCTION_GROUPS`. These
 * pin the set-equality AC4 asks for — the same files, in the same order, with
 * the same names — so a kind added to `KIND_FILES` without a group, or a
 * group naming a kind with no file, fails here rather than rendering as a
 * bare slug on one surface and nothing on the other.
 *
 * Run with `npm run test:web`.
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { KIND_FILES, INSTRUCTION_ROOT, pathForKind } from "./instruction-files.ts";
import {
  GROUPED_KINDS,
  INSTRUCTION_GROUPS,
  INSTRUCTION_KIND_META,
  ungroupedKinds,
} from "./instruction-kinds.ts";
import {
  AGENTS_FILE,
  AGENTS_KEY,
  agentsFile,
  contentFor,
  filledFileCount,
  templateFileForKey,
  templateFileGroups,
  templateFiles,
  totalFileCount,
} from "./template-files.ts";

test("every kind that publishes a file is placed in exactly one group", () => {
  assert.deepEqual(ungroupedKinds(), []);
  const seen = new Map<string, number>();
  for (const k of GROUPED_KINDS) seen.set(k, (seen.get(k) ?? 0) + 1);
  const dupes = [...seen].filter(([, n]) => n > 1).map(([k]) => k);
  assert.deepEqual(dupes, []);
  // and no group names a kind that has no file
  assert.deepEqual(
    GROUPED_KINDS.filter((k) => !(k in KIND_FILES)),
    [],
  );
});

test("the template's file set is AGENTS.md plus every .buildmill file", () => {
  const paths = templateFiles().map((f) => f.path);
  const expected = [
    AGENTS_FILE,
    ...Object.keys(KIND_FILES).map((k) => pathForKind(k)),
  ];
  assert.deepEqual(new Set(paths), new Set(expected));
  assert.equal(paths.length, expected.length, "no file listed twice");
  assert.equal(paths[0], AGENTS_FILE, "the document comes first");
  assert.equal(totalFileCount(), expected.length);
});

test("the per-task files follow the project's group order", () => {
  const groups = templateFileGroups();
  assert.deepEqual(
    groups.map((g) => g.key),
    INSTRUCTION_GROUPS.map((g) => g.key),
  );
  for (const g of groups) {
    assert.deepEqual(
      g.files.map((f) => f.key),
      g.kinds,
      `group ${g.key} renders its kinds in order`,
    );
    for (const f of g.files) {
      assert.ok(f.path.startsWith(`${INSTRUCTION_ROOT}/`));
    }
  }
});

test("a file carries the project's title for its kind, not the slug", () => {
  for (const f of templateFiles()) {
    if (f.key === AGENTS_KEY) continue;
    assert.equal(f.title, INSTRUCTION_KIND_META[f.key]?.title, f.key);
    assert.notEqual(f.title, f.key, `${f.key} would render as a bare slug`);
  }
  assert.equal(agentsFile().title, "Agent Instructions");
});

test("the agents pseudo-key can never collide with a run kind", () => {
  assert.ok(!(AGENTS_KEY in KIND_FILES));
  assert.equal(templateFileForKey(AGENTS_KEY)?.path, AGENTS_FILE);
  assert.equal(templateFileForKey("code")?.path, ".buildmill/Code.md");
  assert.equal(templateFileForKey("no-such-kind"), null);
  assert.equal(templateFileForKey(null), null);
});

test("the count is filled files, document included, blanks excluded", () => {
  const empty = { agentInstructions: "", instructions: {} };
  assert.equal(filledFileCount(empty), 0);
  const some = {
    agentInstructions: "# Conventions",
    instructions: { code: "Build it.", plan: "   ", nonsense: "ignored" },
  };
  assert.equal(filledFileCount(some), 2);
  assert.equal(contentFor(some, AGENTS_KEY), "# Conventions");
  assert.equal(contentFor(some, "code"), "Build it.");
  assert.equal(contentFor(some, "test"), "");
});
