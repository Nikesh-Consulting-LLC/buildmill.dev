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
  "guidelines",
  "elaborate",
  "wireframe",
  "merge",
] as const;

export type RunKind = (typeof RUN_KINDS)[number];

/** Every kind the factory dispatches today. us-13.11 (test), us-13.12
 * (release) and us-13.13 (deploy) shipped, so nothing is reserved.
 *
 * us-98.1 brought this back into step with `runs_kind_check`. It had frozen
 * at seven while the database moved to ten: `guidelines` (us-43),
 * `elaborate` (us-44.1) and `wireframe` (us-48) were dispatchable and
 * routable but read here as "not dispatchable yet". The header above
 * rationalized understating as the safe direction to be wrong in — which is
 * true of a kind that does not exist yet, and false of three that do. */
export const DISPATCHABLE_RUN_KINDS: ReadonlySet<string> = new Set(RUN_KINDS);

/** The kind as a noun, for a column or a badge. See RUN_KIND_RUN_PHRASES
 * for the "<kind> run" form — they are different words for different
 * places, and they used to be two exports with the same name in two
 * modules (us-98.1). */
export const RUN_KIND_LABELS: Record<RunKind, string> = {
  prd: "PRD",
  breakdown: "Breakdown",
  plan: "Plan",
  code: "Code",
  test: "Test",
  release: "Release",
  deploy: "Deploy",
  guidelines: "Guidelines",
  elaborate: "Elaborate",
  wireframe: "Wireframe",
  merge: "Merge",
};

/** The kind as the run itself — "a Plan run" — for prose that names what is
 * about to happen. */
export const RUN_KIND_RUN_PHRASES: Record<RunKind, string> = {
  prd: "PRD run",
  breakdown: "Breakdown run",
  plan: "Plan run",
  code: "Code run",
  test: "Test run",
  release: "Release run",
  deploy: "Deploy run",
  guidelines: "Guidelines run",
  elaborate: "Elaboration run",
  wireframe: "Wireframe run",
  merge: "Merge run",
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
  guidelines: "may propose changes to the project's guidelines",
  elaborate: "may expand a story's body and acceptance criteria",
  wireframe: "may draw wireframes for a story's UI surface",
  merge: "may land named branches onto the default branch",
};

export function isDispatchable(kind: string): boolean {
  return DISPATCHABLE_RUN_KINDS.has(kind);
}
