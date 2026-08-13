/**
 * US-78.3 / US-78.6: what a new agent may be created as, and where it may run.
 *
 * `MODULES` is the one table three surfaces read — the wizard's radios, the
 * agent settings page's label, and the pool-only rule. Pinning it means a
 * change to the offer list is a deliberate edit to a test, not a silent shift
 * in what managers can create.
 *
 * Read off disk rather than imported: `agent-runner-data.ts` imports through
 * the `@/` alias, which the bare node test runner does not resolve. The same
 * approach `agent-roles.test.ts` already uses to read `runner_socket.py`.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const SOURCE = readFileSync(
  new URL(
    "../app/(app)/team/[principalId]/agent-runner-data.ts",
    import.meta.url,
  ),
  "utf8",
);

/** Every `{ key: "x", ... }` entry in the MODULES table, with its flags. */
function modules(): { key: string; offered: boolean; poolOnly: boolean }[] {
  const table = SOURCE.split("export const MODULES")[1]?.split("];")[0] ?? "";
  return [...table.matchAll(/\{[^{}]*key:\s*"([a-z]+)"[^{}]*\}/g)].map((m) => ({
    key: m[1],
    offered: /offered:\s*true/.test(m[0]),
    poolOnly: /poolOnly:\s*true/.test(m[0]),
  }));
}

test("three agent types are on offer, and interactive is one of them", () => {
  assert.deepEqual(
    modules()
      .filter((m) => m.offered)
      .map((m) => m.key),
    ["grok", "opencode", "interactive"],
  );
});

test("modules that are no longer offered still resolve a label", () => {
  // An agent already running one of these keeps reading correctly on its
  // settings page (us-77.2); it is simply not a choice for a NEW agent.
  const all = modules();
  for (const key of ["claude", "buildmill", "sim"]) {
    const entry = all.find((m) => m.key === key);
    assert.ok(entry, `${key} must stay in the table`);
    assert.equal(entry.offered, false, `${key} must not be offered`);
  }
});

test("only the interactive agent is pool-only", () => {
  // It holds a live session on hardware the platform provisions, patches and
  // can reach. Grok Build and OpenCode are deliberately placeable on a machine
  // an org manages, and stay that way.
  assert.deepEqual(
    modules()
      .filter((m) => m.poolOnly)
      .map((m) => m.key),
    ["interactive"],
  );
});

test("the offer list is derived from the flag, not written out twice", () => {
  assert.match(
    SOURCE,
    /OFFERED_MODULES = MODULES\.filter\(\(m\) => m\.offered\)/,
    "OFFERED_MODULES must stay derived — a second hand-written list would drift",
  );
});
