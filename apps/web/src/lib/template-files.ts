/** US-100.4: what a project template holds — the contents of the files a
 * project will publish, and nothing else.
 *
 * A template used to hold "sections" of three types (guideline,
 * worker_instruction, prompt). Two of those stopped meaning anything once a
 * project's conventions became one document (us-100.1) and the platform
 * prompts left the published set (us-99.1). What a superadmin edits is now
 * literally what lands in a repository: the `AGENTS.md` body and one editor
 * per `.buildmill/*.md` — the same two surfaces a project has (us-100.3), in
 * the same order, with the same names.
 *
 * Storage did not move: the document is `agent_instructions` on the template
 * row and each per-task file is still a `worker_instruction` section keyed by
 * kind. This module is the shape between that storage and the two editors.
 */

import { KIND_FILES, INSTRUCTION_ROOT } from "./instruction-files.ts";
import {
  INSTRUCTION_GROUPS,
  metaForKind,
  type InstructionGroup,
} from "./instruction-kinds.ts";

export const AGENTS_FILE = "AGENTS.md";

/** The document is addressed by this pseudo-kind in URLs and drafts, so a
 * single `?file=` param can name either surface. It can never collide with a
 * run kind: run kinds are snake_case words. */
export const AGENTS_KEY = "agents";

export type TemplateFile = {
  /** `agents` for the document, otherwise the run kind. */
  key: string;
  /** Repo-relative path the content publishes to. */
  path: string;
  /** What the project surface calls it. */
  title: string;
  description: string;
};

export function agentsFile(): TemplateFile {
  return {
    key: AGENTS_KEY,
    path: AGENTS_FILE,
    title: "Agent Instructions",
    description:
      "The project's conventions — the body of AGENTS.md, which every task's instruction file is indexed from.",
  };
}

export function fileForKind(kind: string): TemplateFile | null {
  const name = KIND_FILES[kind];
  if (!name) return null;
  const meta = metaForKind(kind);
  return {
    key: kind,
    path: `${INSTRUCTION_ROOT}/${name}`,
    title: meta.title,
    description: meta.description,
  };
}

export type TemplateFileGroup = InstructionGroup & { files: TemplateFile[] };

/** The per-task files, grouped and ordered exactly as the project's Task
 * Instructions tab shows them. */
export function templateFileGroups(): TemplateFileGroup[] {
  return INSTRUCTION_GROUPS.map((g) => ({
    ...g,
    files: g.kinds
      .map(fileForKind)
      .filter((f): f is TemplateFile => f !== null),
  }));
}

/** Every file a template holds, document first, then the per-task files in
 * display order. */
export function templateFiles(): TemplateFile[] {
  return [agentsFile(), ...templateFileGroups().flatMap((g) => g.files)];
}

/** Resolve a `?file=` value to a file, or null when it names nothing. */
export function templateFileForKey(key: string | null): TemplateFile | null {
  if (!key) return null;
  if (key === AGENTS_KEY) return agentsFile();
  return fileForKind(key);
}

/** The template's contents in the shape both editors hold: the document, and
 * the per-task text by kind. A kind with no text is absent — an empty file
 * would publish as a delete (us-99.4), so it is not "a file" here either. */
export type TemplateContents = {
  agentInstructions: string;
  instructions: Record<string, string>;
};

/** How many of the template's files carry content — the number the list
 * shows. Counts the document as one. */
export function filledFileCount(c: TemplateContents): number {
  const doc = c.agentInstructions.trim() ? 1 : 0;
  const tasks = Object.keys(KIND_FILES).filter(
    (k) => (c.instructions[k] ?? "").trim() !== "",
  ).length;
  return doc + tasks;
}

export function totalFileCount(): number {
  return 1 + Object.keys(KIND_FILES).length;
}

/** The text a file currently holds. */
export function contentFor(c: TemplateContents, key: string): string {
  return key === AGENTS_KEY ? c.agentInstructions : (c.instructions[key] ?? "");
}
