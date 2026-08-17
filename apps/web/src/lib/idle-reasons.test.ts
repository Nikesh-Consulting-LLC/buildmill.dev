/** us-116.2: the roster's idle vocabulary, its precedence, and what a blank
 * Model per role row says it inherits.
 *
 * Run with `npm run test:web`. The precedence list is mirrored from the API's
 * `worker_idle_reason` docstring; the last test reads that docstring off disk
 * so the two cannot drift apart silently.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  IDLE_LABELS,
  IDLE_PRECEDENCE,
  SESSION_BLOCKING_REASONS,
  idleFixHref,
  idleTone,
  inheritLine,
} from "./idle-reasons.ts";

test("every reason in the precedence has a label, and the two new ones read as conditions", () => {
  for (const reason of IDLE_PRECEDENCE) {
    assert.ok(IDLE_LABELS[reason], `no label for ${reason}`);
  }
  assert.equal(IDLE_LABELS["no-model"], "No model configured");
  assert.equal(IDLE_LABELS["no-roles"], "No roles checked");
});

test("configuration reasons sit above the queue tier and below revoked/working/paused", () => {
  const at = (r: string) => IDLE_PRECEDENCE.indexOf(r);
  assert.ok(at("revoked") < at("no-roles"));
  assert.ok(at("working") < at("no-roles"));
  assert.ok(at("paused") < at("no-roles"));
  assert.ok(at("no-roles") < at("no-model"));
  assert.ok(at("no-model") < at("no-grants"));
  assert.ok(at("no-grants") < at("queue-held"));
  assert.ok(at("queue-held") < at("idle"));
});

test("a blocked agent reads amber, not muted", () => {
  assert.equal(idleTone("no-model"), idleTone("no-grants"));
  assert.notEqual(idleTone("no-model"), idleTone("idle"));
  assert.notEqual(idleTone("no-model"), idleTone("working"));
});

test("only the configuration reasons disable the Start session button", () => {
  assert.deepEqual([...SESSION_BLOCKING_REASONS].sort(), ["no-model", "no-roles"]);
});

test("the fix links land on the settings-page anchors that exist", () => {
  assert.deepEqual(idleFixHref("p1", "no-model"), {
    href: "/team/p1/settings#model-overrides",
    label: "Model per role",
  });
  assert.deepEqual(idleFixHref("p1", "no-roles"), {
    href: "/team/p1/settings#kinds",
    label: "What this agent does",
  });
  assert.equal(idleFixHref("p1", "idle"), null);
});

test("a blank Model per role row says what it inherits, naming the preset", () => {
  assert.equal(
    inheritLine({ name: "Balanced", model: null }),
    "Inherits Balanced — no model set",
  );
  assert.equal(
    inheritLine({ name: "Balanced", model: "claude-sonnet-5" }),
    "Inherits Balanced — claude-sonnet-5",
  );
  assert.equal(inheritLine(null), "Inherits nothing — the org has no default preset");
});

test("the precedence mirrors the API's own docstring", () => {
  const src = readFileSync(
    fileURLToPath(new URL("../../../api/app/db.py", import.meta.url)),
    "utf-8",
  );
  const start = src.indexOf("Presence is not permission. Returns one of, in this precedence:");
  assert.ok(start > 0, "worker_idle_reason docstring not found");
  const block = src.slice(start, src.indexOf('"""', start));
  const order = [...block.matchAll(/^\s{6}([a-z-]+)\s+—/gm)].map((m) => m[1]);
  assert.deepEqual(order, [...IDLE_PRECEDENCE]);
});
