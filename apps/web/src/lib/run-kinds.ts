/** US-14.6: one place that says which run kinds the factory can actually
 * dispatch, so no surface has to hardcode its own guess.
 *
 * The capability matrix used to carry a literal `reserved: true` against
 * test, release and deploy. Those became real run kinds in us-13.11–13.13
 * and the label stayed behind, telling an operator that granting `Test`
 * did nothing yet — when in truth it decided whether that worker could
 * claim verification work. A hardcoded list is what let the copy drift; a
 * derived one cannot, because adding a kind here is the same edit that
 * makes it dispatchable.
 *
 * Keep this in step with the run kinds the API accepts. If a kind is
 * added to the pipeline but not here, it will read as "not dispatchable
 * yet" — the safe direction to be wrong in, since it understates rather
 * than promises. */

export const RUN_KINDS = [
  "prd",
  "breakdown",
  "plan",
  "code",
  "test",
  "release",
  "deploy",
] as const;

export type RunKind = (typeof RUN_KINDS)[number];

/** Every kind the factory dispatches today. us-13.11 (test), us-13.12
 * (release) and us-13.13 (deploy) shipped, so nothing is reserved. */
export const DISPATCHABLE_RUN_KINDS: ReadonlySet<string> = new Set(RUN_KINDS);

export const RUN_KIND_LABELS: Record<RunKind, string> = {
  prd: "PRD",
  breakdown: "Breakdown",
  plan: "Plan",
  code: "Code",
  test: "Test",
  release: "Release",
  deploy: "Deploy",
};

/** What granting this capability actually does, in the operator's terms —
 * shown on the matrix so a grant is not a word with no consequence. */
export const RUN_KIND_GRANT_HELP: Record<RunKind, string> = {
  prd: "may draft PRDs for this project",
  breakdown: "may split approved PRDs into stories",
  plan: "may write implementation and test plans",
  code: "may write code and hand back a changeset",
  test: "may claim verification runs and report per-case results",
  release: "may prepare release cuts and promotion PRs",
  deploy: "may execute deployments, under the deployment's own rails",
};

export function isDispatchable(kind: string): boolean {
  return DISPATCHABLE_RUN_KINDS.has(kind);
}
