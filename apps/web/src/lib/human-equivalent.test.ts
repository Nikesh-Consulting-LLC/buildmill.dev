import { test } from "node:test";
import assert from "node:assert/strict";

import {
  HUMAN_LINES_PER_HOUR,
  REMOVED_LINE_WEIGHT,
  formatHumanHours,
  humanEquivalentDays,
  humanEquivalentHours,
} from "./human-equivalent.ts";

test("added lines convert at the stated rate", () => {
  assert.equal(humanEquivalentHours(HUMAN_LINES_PER_HOUR, 0), 1);
  assert.equal(humanEquivalentHours(250, 0), 10);
});

test("removed lines count, at half weight", () => {
  assert.equal(
    humanEquivalentHours(0, HUMAN_LINES_PER_HOUR),
    REMOVED_LINE_WEIGHT,
  );
  // The real shape of the figure: added carries it, removed nudges it.
  assert.equal(humanEquivalentHours(7553, 621), (7553 + 310.5) / 25);
});

test("nothing changed is nothing estimated, and negatives cannot subtract", () => {
  assert.equal(humanEquivalentHours(0, 0), 0);
  assert.equal(humanEquivalentHours(100, -1000), 4);
});

test("hours render as an approximation, never to the minute", () => {
  assert.equal(formatHumanHours(0), "0h");
  assert.equal(formatHumanHours(-3), "0h");
  assert.equal(formatHumanHours(6.44), "6.4h");
  assert.equal(formatHumanHours(314.52), "315h");
});

test("days are hours over an eight-hour day", () => {
  assert.equal(humanEquivalentDays(80), 10);
});
