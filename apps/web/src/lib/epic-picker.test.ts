/** Unit tests for the US-71.1 epic-picker model — pure projections, run with
 * `npm run test:web` (node --test with native type stripping). */

import { test } from "node:test";
import assert from "node:assert/strict";

import { defaultEpicId, epicPickerOptions } from "./epic-picker.ts";

const epic = (
  id: string,
  number: number,
  opts: { status?: string; active?: boolean; title?: string } = {}
) => ({
  id,
  number,
  title: opts.title ?? `T${number}`,
  status: opts.status ?? "open",
  active: opts.active ?? false,
});

test("epicPickerOptions lists open epics newest-first and drops closed ones", () => {
  const options = epicPickerOptions([
    epic("a", 1, { status: "completed" }),
    epic("b", 2),
    epic("c", 3),
  ]);
  assert.deepEqual(
    options.map((e) => e.id),
    ["c", "b"]
  );
});

test("epicPickerOptions keeps the closed epic an edited item sits on, last", () => {
  const options = epicPickerOptions(
    [epic("a", 1, { status: "completed" }), epic("b", 2), epic("c", 3)],
    "a"
  );
  assert.deepEqual(
    options.map((e) => e.id),
    ["c", "b", "a"]
  );
  // Another closed epic is still excluded.
  const other = epicPickerOptions(
    [epic("a", 1, { status: "completed" }), epic("b", 2)],
    "b"
  );
  assert.deepEqual(
    other.map((e) => e.id),
    ["b"]
  );
});

test("epicPickerOptions tolerates missing numbers (sorted after numbered)", () => {
  const options = epicPickerOptions([
    { id: "x", title: "unnumbered" },
    epic("b", 2),
  ]);
  assert.deepEqual(
    options.map((e) => e.id),
    ["b", "x"]
  );
});

test("defaultEpicId prefers the active epic", () => {
  const id = defaultEpicId([epic("a", 1), epic("b", 2, { active: true }), epic("c", 3)]);
  assert.equal(id, "b");
});

test("defaultEpicId falls back to the newest open epic when none is active", () => {
  const id = defaultEpicId([
    epic("a", 1),
    epic("b", 2),
    epic("c", 3, { status: "completed" }),
  ]);
  assert.equal(id, "b");
});

test("defaultEpicId is null when every epic is closed", () => {
  const id = defaultEpicId([
    epic("a", 1, { status: "completed" }),
    epic("b", 2, { status: "completed" }),
  ]);
  assert.equal(id, null);
});
