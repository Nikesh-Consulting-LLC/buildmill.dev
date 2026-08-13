/** Unit tests for `issueMatchesQuery` — the client-side predicate the Work
 * Items hub applies to Realtime rows while a search is active. Pure, so it
 * needs no DOM and no network. Run with `npm run test:web`.
 *
 * US-87.3 is what these guard. The hub's list select no longer fetches `body`
 * or `acceptance_criteria` (902 bytes of markdown per item on average, never
 * rendered by any list view). The hazard that change introduces is precise
 * and one-directional: `use-project-issues.ts` DROPS a row from the visible
 * set when this function returns false for an incoming update, so a false
 * negative does not merely fail to add an item — it makes an item the manager
 * is looking at disappear.
 *
 * The defence is that `search_text` — the generated column (migration 036)
 * that the server's own `applyIssueSearch` filters on — is authoritative and
 * always present on a `postgres_changes` payload, which carries the whole row
 * regardless of what the page selected.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { issueMatchesQuery } from "./issue-search.ts";

test("search_text matches what the server would have matched", () => {
  // The realtime payload shape: whole row, prose included, search_text set.
  const row = {
    title: "Untitled",
    search_text: "Untitled\nthe connection pool leaks under load",
    body: "the connection pool leaks under load",
  };
  assert.equal(issueMatchesQuery(row, "connection pool"), true);
});

test("a row with only search_text still matches on body text", () => {
  // us-87.3's actual case: prose is no longer selected, so `body` is absent
  // and `search_text` is the only field carrying it.
  const row = { title: "Untitled", search_text: "Untitled\npool exhaustion" };
  assert.equal(issueMatchesQuery(row, "exhaustion"), true);
});

test("a narrow row still matches on its title", () => {
  // A row from the hub's list select: no prose, no search_text.
  const row = { title: "Pool the API's connections" };
  assert.equal(issueMatchesQuery(row, "pool"), true);
});

test("a non-matching row is rejected", () => {
  const row = { title: "Something else", search_text: "Something else\nnope" };
  assert.equal(issueMatchesQuery(row, "connection pool"), false);
});

test("an empty query keeps everything", () => {
  assert.equal(issueMatchesQuery({ title: "anything" }, "   "), true);
});

test("matching is case-insensitive on every field", () => {
  assert.equal(issueMatchesQuery({ title: "POOL" }, "pool"), true);
  assert.equal(issueMatchesQuery({ search_text: "POOL" }, "pool"), true);
  assert.equal(issueMatchesQuery({ body: "POOL" }, "pool"), true);
});

test("a github number matches with or without the hash", () => {
  const row = { title: "no words in common", github_issue_number: 249 };
  assert.equal(issueMatchesQuery(row, "249"), true);
  assert.equal(issueMatchesQuery(row, "#249"), true);
  assert.equal(issueMatchesQuery(row, "250"), false);
});

test("acceptance_criteria still matches for callers that carry it", () => {
  const row = {
    title: "no words in common",
    acceptance_criteria: ["the pool is sized for the pooler"],
  };
  assert.equal(issueMatchesQuery(row, "sized for the pooler"), true);
});

test("every field can only ADD a match, never veto one", () => {
  // The regression this file exists for: no field's absence, and no field
  // disagreeing with another, may turn a match into a miss. If a future
  // change makes one field authoritative in the negative, this fails.
  const matchesSomewhere = [
    { search_text: "alpha" },
    { title: "alpha" },
    { body: "alpha" },
    { acceptance_criteria: ["alpha"] },
    // search_text says no, but title says yes — still a match.
    { search_text: "zzz", title: "alpha" },
    // title says no, but the prose says yes — still a match.
    { title: "zzz", body: "alpha" },
  ];
  for (const row of matchesSomewhere) {
    assert.equal(
      issueMatchesQuery(row, "alpha"),
      true,
      `expected a match for ${JSON.stringify(row)}`
    );
  }
});
