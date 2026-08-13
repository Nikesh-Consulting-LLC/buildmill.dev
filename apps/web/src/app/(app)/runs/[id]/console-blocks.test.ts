import { strict as assert } from "node:assert";
import { test } from "node:test";

import { toBlocks, groupTitle, type Line } from "./console-blocks.ts";

const line = (kind: string, content = kind): Line => ({ kind, content });

test("consecutive working rows fold into one group", () => {
  const blocks = toBlocks([
    line("output", "I'll pull the entrypoints."),
    line("step", "The user wants the architecture."),
    line("tool", "Read: main.py"),
    line("tool", "tool — completed"),
    line("output", "# Core architecture"),
  ]);
  assert.deepEqual(
    blocks.map((b) => b.type),
    ["line", "group", "line"],
  );
  const group = blocks[1];
  assert.equal(group.type === "group" && group.lines.length, 3);
});

test("even a lone working row folds", () => {
  // A single thought is a paragraph: flat it wraps to four lines between two
  // sentences of the answer.
  const blocks = toBlocks([line("output"), line("step", "a long thought"), line("output")]);
  assert.deepEqual(
    blocks.map((b) => (b.type === "group" ? `group(${b.lines.length})` : b.line.kind)),
    ["output", "group(1)", "output"],
  );
});

test("what the manager is addressed by is never folded", () => {
  // The agent's speech, an error and the manager's own words break the fold
  // rather than disappearing into it.
  const blocks = toBlocks([
    line("step"),
    line("step"),
    line("output", "I'll pull claimable work."),
    line("step"),
    line("step"),
    line("error", "bash: not found"),
    line("you", "try again"),
  ]);
  assert.deepEqual(
    blocks.map((b) => (b.type === "group" ? `group(${b.lines.length})` : b.line.kind)),
    ["group(2)", "output", "group(2)", "error", "you"],
  );
});

test("the whole clogged turn from the manager's screenshot is four blocks", () => {
  // 23 rows as they arrived: a steer, two sentences of narration, and 19 rows
  // of tool calls, thoughts and permission notices between them.
  const turn: Line[] = [
    line("you", "Fetch the open items in buildmill"),
    line("decision", "manager steered the run"),
    line("output", "long-running AI work."),
    line("step", "The user wants me to fetch open items from Buildmill."),
    line("output", "I'll pull claimable work and the full factory queue."),
    ...Array.from({ length: 17 }, (_, i) => line(i % 4 === 3 ? "decision" : "tool", `t${i}`)),
    line("step", "The factory is empty."),
  ];
  assert.deepEqual(
    toBlocks(turn).map((b) => (b.type === "group" ? `group(${b.lines.length})` : b.line.kind)),
    ["you", "group(1)", "output", "group(1)", "output", "group(18)"],
  );
});

test("permission notices fold instead of shattering the fold", () => {
  // Straight from a real turn: three tool calls, each announcing a permission
  // decision between them. As separate rows that is eleven lines of clog; the
  // whole run is one.
  const blocks = toBlocks([
    line("output", "I'll pull claimable work and the full factory queue."),
    line("tool", "search_tool: factory list available work queue"),
    line("tool", "tool — completed"),
    line("step", "Let me call the three list tools in parallel."),
    line("tool", "use_tool"),
    line("tool", "factory__list_available_work"),
    line("decision", "permission selected for factory__list_available_work (allow_once)"),
    line("tool", "use_tool"),
    line("tool", "factory__list_factory_queue"),
    line("decision", "permission selected for factory__list_factory_queue (allow_once)"),
    line("step", "The factory is empty. I should report this clearly."),
  ]);
  assert.deepEqual(
    blocks.map((b) => b.type),
    ["line", "group"],
  );
  assert.equal(blocks[1].type === "group" && blocks[1].lines.length, 10);
});

test("a group keeps its key as it grows, so an opened fold stays open", () => {
  const before = toBlocks([line("output"), line("step"), line("step")]);
  const after = toBlocks([line("output"), line("step"), line("step"), line("tool")]);
  assert.equal(before[1].key, 1);
  assert.equal(after[1].key, 1);
  assert.equal(after[1].type === "group" && after[1].lines.length, 3);
});

test("the title is the opening line of the newest row", () => {
  assert.equal(
    groupTitle([line("step", "first thought"), line("step", "  Reading main.py\nthen worker.py")]),
    "Reading main.py",
  );
});

test("the title skips rows that are only whitespace", () => {
  assert.equal(groupTitle([line("step", "a real thought"), line("step", "\n \n")]), "a real thought");
  assert.equal(groupTitle([line("step", " ")]), "working");
});
