/**
 * US-114.1: a template travels as a zip — the round trip, and the rules an
 * import applies before anything is written.
 *
 * The module is pure and imports relatively, so the bare node runner can
 * load it (the `@/` alias is not resolved here). fflate runs in node, so the
 * archive built here is a real zip, not a fake.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { strToU8, zipSync } from "fflate";

import { KIND_FILES } from "./instruction-files.ts";
import {
  buildTemplateZip,
  exportEntries,
  oversizeFile,
  planImport,
  readTemplateZip,
  templateZipFilename,
} from "./template-zip.ts";
import type { TemplateContents } from "./template-files.ts";

const SAMPLE: TemplateContents = {
  agentInstructions: "# Agent Instructions\n\nBe kind.\n",
  instructions: {
    plan: "# Planning\n\nFour sections.\n",
    code: "# Coding\n\nKeep the diff focused.\n",
    release: "# Release prep\n",
    test: "   ", // whitespace-only counts as empty
  },
};

test("export carries the document and every filled task file, at the published path", () => {
  const entries = exportEntries(SAMPLE);
  assert.deepEqual(Object.keys(entries).sort(), [
    ".buildmill/Code.md",
    ".buildmill/Plan.md",
    ".buildmill/Release_Prep.md",
    "AGENTS.md",
  ]);
  assert.equal(entries["AGENTS.md"], SAMPLE.agentInstructions);
  assert.equal(entries[".buildmill/Plan.md"], SAMPLE.instructions.plan);
});

test("export → import is lossless", () => {
  const bytes = buildTemplateZip(SAMPLE);
  const { files, ignored } = readTemplateZip(bytes);
  assert.deepEqual(ignored, []);
  const byKey = Object.fromEntries(files.map((f) => [f.key, f.text]));
  assert.deepEqual(byKey, {
    agents: SAMPLE.agentInstructions,
    plan: SAMPLE.instructions.plan,
    code: SAMPLE.instructions.code,
    release: SAMPLE.instructions.release,
  });
  // Every file also names the path it publishes to.
  assert.equal(files.find((f) => f.key === "release")?.path, ".buildmill/Release_Prep.md");
  assert.equal(files.find((f) => f.key === "agents")?.path, "AGENTS.md");
});

test("a common top-level folder is stripped and archiver noise is dropped", () => {
  const bytes = zipSync({
    "my-template/AGENTS.md": strToU8("doc"),
    "my-template/.buildmill/Plan.md": strToU8("plan"),
    "my-template/.buildmill/Fix.md": strToU8("fix"),
    "__MACOSX/my-template/._AGENTS.md": strToU8("junk"),
    "my-template/.DS_Store": strToU8("junk"),
  });
  const { files, ignored } = readTemplateZip(bytes);
  assert.deepEqual(ignored, []);
  assert.deepEqual(
    Object.fromEntries(files.map((f) => [f.key, f.text])),
    { agents: "doc", plan: "plan", bug_fix: "fix" },
  );
});

test("unknown entries are ignored by name, and a loose task file at the root is accepted", () => {
  const bytes = zipSync({
    "AGENTS.md": strToU8("doc"),
    "Plan.md": strToU8("plan at root"),
    ".buildmill/Guidelines.md": strToU8("not a kind"),
    "README.md": strToU8("hello"),
    "docs/.buildmill/Code.md": strToU8("too deep"),
    "notes/AGENTS.md": strToU8("wrong place"),
  });
  const { files, ignored } = readTemplateZip(bytes);
  assert.deepEqual(
    Object.fromEntries(files.map((f) => [f.key, f.text])),
    { agents: "doc", plan: "plan at root" },
  );
  assert.deepEqual(ignored.sort(), [
    ".buildmill/Guidelines.md",
    "README.md",
    "docs/.buildmill/Code.md",
    "notes/AGENTS.md",
  ]);
});

test("every kind in KIND_FILES round-trips by file name", () => {
  const contents: TemplateContents = {
    agentInstructions: "",
    instructions: Object.fromEntries(Object.keys(KIND_FILES).map((k) => [k, `text for ${k}`])),
  };
  const { files } = readTemplateZip(buildTemplateZip(contents));
  assert.deepEqual(
    files.map((f) => f.key).sort(),
    Object.keys(KIND_FILES).sort(),
  );
});

test("the plan says overwrite / cleared / unchanged, and an empty file clears", () => {
  const current: TemplateContents = {
    agentInstructions: "doc",
    instructions: { plan: "old plan", code: "same code", test: "" },
  };
  const plan = planImport(current, [
    { key: "agents", path: "AGENTS.md", text: "doc" },
    { key: "plan", path: ".buildmill/Plan.md", text: "new plan" },
    { key: "code", path: ".buildmill/Code.md", text: "same code" },
    { key: "release", path: ".buildmill/Release_Prep.md", text: "" }, // absent today
    { key: "test", path: ".buildmill/Test.md", text: "brand new" },
  ]);
  assert.deepEqual(plan.overwrite.map((f) => f.key), ["plan", "test"]);
  assert.deepEqual(plan.cleared.map((f) => f.key), []);
  assert.deepEqual(plan.unchanged.map((f) => f.key), ["agents", "code", "release"]);

  const clearing = planImport(current, [
    { key: "plan", path: ".buildmill/Plan.md", text: "\n" },
  ]);
  assert.deepEqual(clearing.cleared.map((f) => f.key), ["plan"]);
});

test("an oversize task file is named before anything is written", () => {
  assert.equal(
    oversizeFile([{ key: "plan", path: ".buildmill/Plan.md", text: "x".repeat(20000) }]),
    null,
  );
  const hit = oversizeFile([
    { key: "agents", path: "AGENTS.md", text: "x".repeat(20001) }, // document cap is higher
    { key: "code", path: ".buildmill/Code.md", text: "x".repeat(20001) },
  ]);
  assert.equal(hit?.path, ".buildmill/Code.md");
});

test("the archive is named after the template", () => {
  assert.equal(templateZipFilename("default"), "default-template.zip");
  assert.equal(templateZipFilename("Web App (copy 2)"), "web-app-copy-2-template.zip");
  assert.equal(templateZipFilename("***"), "template-template.zip");
});

test("bytes that are not a zip throw rather than import nothing quietly", () => {
  assert.throws(() => readTemplateZip(strToU8("this is not a zip")));
});
