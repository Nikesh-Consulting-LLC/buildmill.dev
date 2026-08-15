/** US-99.1: which `.buildmill/` file each instruction kind publishes to.
 *
 * The canonical declaration is `apps/api/app/instruction_files.py`. This is a
 * mirror, because the api is Python and the web is TypeScript and there is no
 * generation step between them — the same situation as `run-kinds.ts` and
 * `runs_kind_check`.
 *
 * A mirror that nothing checks is how `run-kinds.ts` came to list seven kinds
 * while the database allowed ten, so `test_instruction_file_map.py` parses
 * both and fails if they disagree in either direction. Edit one, edit the
 * other, or the suite says so.
 */

export const INSTRUCTION_ROOT = ".buildmill";

export const KIND_FILES: Record<string, string> = {
  // requirements
  prd: "PRD.md",
  breakdown: "Breakdown.md",
  elaborate: "Elaborate.md",
  wireframe: "Wireframe.md",
  // planning — one per type, because Phase 96 made them genuinely different
  plan: "Plan.md",
  standalone_plan: "Plan_Standalone.md",
  bug_rca: "RCA.md",
  // building
  code: "Code.md",
  standalone_code: "Code_Standalone.md",
  bug_fix: "Fix.md",
  chore: "Chore.md",
  // integration
  merge: "Merge.md",
  // verification and shipping
  test: "Test.md",
  deploy: "Deploy.md",
  release: "Release_Prep.md",
  // the run that proposes changes to the conventions — NOT the conventions
  guidelines: "Guidelines_Refresh.md",
};

/** The project's own conventions — the assembled output of
 * `project_guidelines`, not an instruction kind (us-99.3). */
export const CONVENTIONS_FILE = "Guidelines.md";

/** Instruction kinds that get no file: server-side LLM prompts driven by
 * `llm.LLM_FUNCTIONS`, which no agent ever reads and which are
 * platform-global rather than per-project. */
export const EXCLUDED_KINDS: readonly string[] = [
  "story_breakdown",
  "test_case_elaborate",
  "deploy_script_generate",
];

/** The repo-relative path this kind publishes to, or null if it has none. */
export function pathForKind(kind: string): string | null {
  const name = KIND_FILES[kind];
  return name ? `${INSTRUCTION_ROOT}/${name}` : null;
}

/** Just the file name, for a label that should not carry the directory. */
export function fileForKind(kind: string): string | null {
  return KIND_FILES[kind] ?? null;
}
