import { test } from "node:test";
import assert from "node:assert/strict";

import {
  resolveActiveModule,
  stepValid,
  wizardSteps,
  type WizardFormState,
} from "./agent-wizard-steps.ts";

const OFFERED = [
  { key: "interactive", poolOnly: true },
  { key: "grok" },
  { key: "opencode" },
];

const FORM: WizardFormState = {
  name: "Programmer",
  activeModule: "interactive",
  placement: "pool",
  machineId: "",
  poolId: "pool-1",
};

// ---------------------------------------------- the sequence (us-111.1 AC8)

test("the step after Where is Projects, not What", () => {
  assert.deepEqual(
    wizardSteps("opencode").map((s) => s.label),
    ["Who", "Where", "Projects", "Done"],
  );
});

test("Claude still inserts Billing, and nothing else does", () => {
  assert.deepEqual(
    wizardSteps("claude").map((s) => s.id),
    ["who", "where", "what", "billing", "done"],
  );
  for (const key of ["interactive", "grok", "opencode", "sim"]) {
    assert.ok(
      !wizardSteps(key).some((s) => s.id === "billing"),
      `${key} must not get a billing step`,
    );
  }
});

// ------------------------------------------------------- what gates a step

test("Who wants a name and nothing more", () => {
  assert.equal(stepValid("who", { ...FORM, name: "  " }), false);
  assert.equal(stepValid("who", { ...FORM, name: "Ada" }), true);
});

test("a benched agent is still creatable — no role is required to leave Who", () => {
  // US-77.1's warning is a warning. The wizard must not turn it into a block
  // now that the roles are asked on this step.
  assert.equal(stepValid("who", { ...FORM, name: "Ada" }), true);
});

test("Where now gates on the type as well as the placement", () => {
  assert.equal(stepValid("where", { ...FORM, activeModule: "" }), false);
  assert.equal(stepValid("where", { ...FORM, placement: null }), false);
  assert.equal(
    stepValid("where", { ...FORM, placement: "machine", machineId: "" }),
    false,
  );
  assert.equal(
    stepValid("where", { ...FORM, placement: "machine", machineId: "h-1" }),
    true,
  );
  assert.equal(stepValid("where", FORM), true);
});

test("Projects never blocks — every project starts checked", () => {
  assert.equal(stepValid("what", { ...FORM, name: "" }), true);
});

// ------------------------------------------- the conditional default (AC3)

test("Interactive is the default when a pool has room", () => {
  assert.equal(resolveActiveModule("interactive", OFFERED, true), "interactive");
});

test("with no pool, the default falls through to a type that can run", () => {
  // The whole point: pool-only + no pool would otherwise open the Where step
  // on a type with no placement and a Next that can never enable.
  assert.equal(resolveActiveModule("interactive", OFFERED, false), "grok");
});

test("an explicit pick is honoured, and is not overridden by the default", () => {
  assert.equal(resolveActiveModule("opencode", OFFERED, true), "opencode");
});

test("a pick the catalog no longer offers falls back rather than sticking", () => {
  assert.equal(resolveActiveModule("claude", OFFERED, true), "interactive");
});

test("an empty catalog resolves to nothing rather than throwing", () => {
  assert.equal(resolveActiveModule("interactive", [], true), "");
});

test("when only pool-only types are offered and there is no pool, one is still named", () => {
  // Nothing can run, but the form must not render with no radio selected —
  // it falls back to the first offered type so the refusal is visible on the
  // option itself rather than as an unexplained dead Next.
  assert.equal(
    resolveActiveModule("interactive", [{ key: "interactive", poolOnly: true }], false),
    "interactive",
  );
});
