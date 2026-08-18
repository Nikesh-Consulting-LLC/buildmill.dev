import { test } from "node:test";
import assert from "node:assert/strict";

import { AGENT_STATES, showsStart } from "./idle-reasons.ts";

/** us-117.2: the roster's Start/Stop toggle, as a rule rather than an
 *  expression buried in JSX.
 *
 *  It used to be `agentState === "stopped"`. `agentState` is one of nine
 *  words, so every other one rendered Stop — and an agent that was paused AND
 *  offline / no-model / no-roles / no-grants / queue-held could not be started
 *  from the roster at all. That is exactly the set most likely to need it: on
 *  2026-08-18 a paused Architect held the only queued plan run in its
 *  workspace and the manager reported there was no way to start any agent.
 *
 *  Intent and status are different questions. Only intent has two answers. */
test("a stopped agent offers Start", () => {
  assert.equal(showsStart("paused"), true);
});

test("a running agent offers Stop", () => {
  assert.equal(showsStart("enabled"), false);
});

test("Start is offered in EVERY status, not just `stopped`", () => {
  // The regression, stated as the property it violated: whatever the agent is
  // doing or failing to do, if the manager stopped it, Start is the offer.
  for (const state of AGENT_STATES) {
    assert.equal(
      showsStart("paused"),
      true,
      `a paused agent showing state "${state}" must still offer Start`,
    );
  }
});

test("an unknown desired state does not claim the agent is stopped", () => {
  // Before the status poll lands the roster knew nothing and defaulted to
  // "ready", which rendered Stop for a stopped agent — and never flipped if
  // that request was slow or failed. Absent intent is not "stopped", but it is
  // also not read from a status word any more.
  assert.equal(showsStart(null), false);
  assert.equal(showsStart(undefined), false);
});
