/** US-100.4: the one vocabulary for the per-task instructions.
 *
 * A project shows its instructions grouped by phase, in a fixed order, with a
 * title and a description per kind (`instructions-tab.tsx`). A
 * template used to describe the same kinds with a second, drifted list — bare
 * slugs, no groups, no descriptions — so a superadmin authoring a template
 * could not see what a manager would get. Both now read this module, and
 * `template-files.test.ts` pins that every kind in `KIND_FILES` is placed
 * exactly once, so the two surfaces stay set-equal by construction.
 */

import { KIND_FILES } from "./instruction-files.ts";

export type InstructionKindMeta = { title: string; description: string };

export const INSTRUCTION_KIND_META: Record<string, InstructionKindMeta> = {
  prd: {
    title: "PRD runs",
    description: "How a worker should draft a feature's PRD.",
  },
  breakdown: {
    title: "Story breakdown runs",
    description:
      "How a worker should split an approved PRD into engineering stories.",
  },
  elaborate: {
    title: "Elaboration runs",
    description:
      "How a worker should expand a rough story into a body and acceptance criteria without widening its scope.",
  },
  wireframe: {
    title: "Wireframe runs",
    description:
      "How a worker should draw a story's UI surface before it is built — or declare that it has none.",
  },
  plan: {
    title: "Stories in a feature — plan",
    description:
      "How a worker should write implementation and test plans for a story born from a PRD breakdown.",
  },
  standalone_plan: {
    title: "Standalone stories — plan",
    description:
      "Planning a story with no PRD and no parent feature — the story and its acceptance criteria are the whole contract.",
  },
  bug_rca: {
    title: "Bugs — root cause analysis",
    description:
      "How a worker should diagnose a bug: what broke, why, and the proposed fix — in plain language, no code.",
  },
  code: {
    title: "Stories in a feature — build",
    description:
      "How a worker should implement a feature-child story's approved plan.",
  },
  standalone_code: {
    title: "Standalone stories — build",
    description:
      "Implementing a standalone story's approved plan, inside this story's slice only.",
  },
  bug_fix: {
    title: "Bugs — the fix",
    description:
      "How a worker should implement an approved RCA's proposed fix, with the reproduction as the regression case.",
  },
  chore: {
    title: "Chores — single-shot build",
    description:
      "How a worker should build a chore directly — no plan phase precedes it, and the hand-back notes carry the verification story.",
  },
  merge: {
    title: "Merge runs",
    description:
      "How a worker should land named branches onto the default branch: read both sides of every conflict, never drop a change it did not understand, and account for every branch — all of them or none.",
  },
  test: {
    title: "Test runs",
    description:
      "How a worker should execute a work item's test cases and report results.",
  },
  release: {
    title: "Release runs",
    description:
      "How a worker should assemble a release: read the change range, propose the version, write the notes, deploy to UAT and verify it.",
  },
  deploy: {
    title: "Deployment runs",
    description:
      "How a worker should trigger, observe and verify one deployment — never claiming an outcome it did not see.",
  },
  guidelines: {
    title: "Instruction refresh runs",
    description:
      "How a worker should study this repository and propose a revised Agent Instructions document and per-task instructions. Steers the run that proposes them — it is not the instructions themselves.",
  },
  // Server-side LLM prompts. They are per-project rows but publish to no file
  // (EXCLUDED_KINDS) and a template does not carry them (us-100.4).
  test_case_elaborate: {
    title: "Test-case elaboration",
    description:
      "Extra guidance appended when a rough test description is expanded into a full manual test case.",
  },
  deploy_script_generate: {
    title: "Deploy-script generation",
    description:
      "Extra guidance appended when a deployment script is drafted for this project.",
  },
};

export type InstructionGroup = {
  key: string;
  label: string;
  blurb: string;
  kinds: string[];
};

/** The phase groups, in the order a manager reads them. Every kind that
 * publishes a file appears in exactly one group; the tab and the template
 * editors both render from this list. */
export const INSTRUCTION_GROUPS: InstructionGroup[] = [
  {
    key: "requirements",
    label: "Requirements",
    blurb:
      "Turning an idea into a specification, a specification into stories, and a story into the surface it will have.",
    kinds: ["prd", "breakdown", "elaborate", "wireframe"],
  },
  {
    key: "planning",
    label: "Planning",
    blurb:
      "Deciding how work will be built and verified — each work-item type in its own words (us-96.3).",
    kinds: ["plan", "standalone_plan", "bug_rca"],
  },
  {
    key: "coding",
    label: "Coding",
    blurb:
      "Writing the change and handing it back for review — each work-item type in its own words (us-96.3).",
    kinds: ["code", "standalone_code", "bug_fix", "chore"],
  },
  {
    key: "integration",
    label: "Integration",
    blurb:
      "Landing finished branches onto the default branch, conflicts and all (us-98.1).",
    kinds: ["merge"],
  },
  {
    key: "testing",
    label: "Testing",
    blurb: "Executing test cases and reporting what actually happened.",
    kinds: ["test"],
  },
  {
    key: "release",
    label: "Release",
    blurb: "Assembling a release, shipping it, and verifying where it landed.",
    kinds: ["release", "deploy"],
  },
  {
    key: "refresh",
    label: "Refresh",
    blurb:
      "The run that reads the repository and proposes better instructions for it (us-100.5).",
    kinds: ["guidelines"],
  },
];

/** Every kind placed by a group. Should equal `Object.keys(KIND_FILES)`;
 * the test says so. */
export const GROUPED_KINDS: string[] = INSTRUCTION_GROUPS.flatMap((g) => g.kinds);

export function metaForKind(kind: string): InstructionKindMeta {
  return INSTRUCTION_KIND_META[kind] ?? { title: kind, description: "" };
}

/** Kinds that publish a file but no group places — must be empty. Exported
 * so a test can assert it rather than a reviewer noticing. */
export function ungroupedKinds(): string[] {
  const placed = new Set(GROUPED_KINDS);
  return Object.keys(KIND_FILES).filter((k) => !placed.has(k));
}
