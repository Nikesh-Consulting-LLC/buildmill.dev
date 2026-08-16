import { test } from "node:test";
import assert from "node:assert/strict";
import { capabilityGapText, type CapabilityGap } from "./capability-gap.ts";

const ALL: CapabilityGap[] = [
  "no-agent-online",
  "no-project-access",
  "kind-disabled",
  "unknown",
];

test("every gap names the thing to change", () => {
  // The whole point of surfacing this is that it is fixable, so no gap may
  // render as a bare "cannot run".
  assert.match(capabilityGapText("no-agent-online"), /online/i);
  assert.match(capabilityGapText("no-project-access"), /access/i);
  assert.match(capabilityGapText("kind-disabled", "plan"), /unchecked/i);
});

test("kind-disabled names the run kind when it knows it", () => {
  assert.match(capabilityGapText("kind-disabled", "guidelines"), /'guidelines'/);
  // …and stays sensible when it does not, rather than printing "undefined".
  const vague = capabilityGapText("kind-disabled");
  assert.doesNotMatch(vague, /undefined/);
  assert.match(vague, /run kind/i);
});

test("no gap produces an empty or undefined-bearing string", () => {
  for (const gap of ALL) {
    const text = capabilityGapText(gap, "code");
    assert.ok(text.length > 0, `${gap} produced nothing`);
    assert.doesNotMatch(text, /undefined|null/, `${gap} leaked a placeholder`);
  }
});

test("the same gap always reads the same way", () => {
  // Two surfaces showing one state must not word it differently — that is the
  // whole reason this lives in one place.
  for (const gap of ALL) {
    assert.equal(capabilityGapText(gap, "plan"), capabilityGapText(gap, "plan"));
  }
});
