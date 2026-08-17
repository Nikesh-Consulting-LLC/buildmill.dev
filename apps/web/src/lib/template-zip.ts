/** US-114.1: a project template travels as a zip.
 *
 * A template is the contents of the files a project publishes (us-100.4):
 * the `AGENTS.md` body and one `.buildmill/<File>.md` per task kind. That is
 * already a tree on disk, so the export IS that tree, zipped — no manifest,
 * no second format. Unzip it and you are looking at exactly what a new
 * project receives; zip such a folder back up and it imports.
 *
 * Import overwrites the selected template in place, file by file, and never
 * creates one. What the zip carries wins; what it does not carry is left
 * alone; an empty file clears its section (the same rule the editor has —
 * a stored empty string would beat the factory default at project creation,
 * so blank means delete). Everything here is pure so the round trip can be
 * pinned by a test without a browser; the download and the file picker live
 * with the buttons component.
 */

import { strFromU8, strToU8, unzipSync, zipSync } from "fflate";

import { INSTRUCTION_ROOT, KIND_FILES } from "./instruction-files.ts";
import { INSTRUCTION_GROUPS } from "./instruction-kinds.ts";
import {
  AGENTS_FILE,
  AGENTS_KEY,
  contentFor,
  templateFiles,
  type TemplateContents,
} from "./template-files.ts";

/** One file the zip carries that maps to a template file. */
export type ZipFile = {
  /** `agents` for the document, otherwise the run kind. */
  key: string;
  /** The path it publishes to — `AGENTS.md` or `.buildmill/<File>.md`. */
  path: string;
  text: string;
};

export type ReadResult = {
  files: ZipFile[];
  /** Entries in the archive that map to nothing — reported, never written. */
  ignored: string[];
};

/** File name → key, the inverse of `KIND_FILES` plus the document. */
function keyForName(name: string): string | null {
  if (name === AGENTS_FILE) return AGENTS_KEY;
  for (const [kind, file] of Object.entries(KIND_FILES)) {
    if (file === name) return kind;
  }
  return null;
}

/** The entries an export carries: every file with content, at the path it
 * publishes to. An empty file is not "a file" (`filledFileCount`), so it is
 * not in the archive either. */
export function exportEntries(contents: TemplateContents): Record<string, string> {
  const out: Record<string, string> = {};
  for (const f of templateFiles()) {
    const text = contentFor(contents, f.key);
    if (text.trim() === "") continue;
    out[f.path] = text;
  }
  return out;
}

/** Build the zip bytes for a template. */
export function buildTemplateZip(contents: TemplateContents): Uint8Array {
  const entries = exportEntries(contents);
  const data: Record<string, Uint8Array> = {};
  for (const [path, text] of Object.entries(entries)) {
    data[path] = strToU8(text);
  }
  return zipSync(data, { level: 6 });
}

/** `<slug>-template.zip` — the catalog uses its key, an org copy its name. */
export function templateZipFilename(nameOrKey: string): string {
  const slug = nameOrKey
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return `${slug || "template"}-template.zip`;
}

/** Junk archivers add that never means a file. */
function isNoise(path: string): boolean {
  const base = path.split("/").pop() ?? "";
  return (
    path.startsWith("__MACOSX/") ||
    path.includes("/__MACOSX/") ||
    base === ".DS_Store" ||
    base === "Thumbs.db" ||
    base === ""
  );
}

/** Zipping a folder on macOS or Windows puts everything under one top-level
 * directory. Strip it when every real entry shares it, so `my-template/
 * AGENTS.md` reads as `AGENTS.md`. */
function stripCommonFolder(paths: string[]): Map<string, string> {
  const map = new Map<string, string>();
  const firstSegments = new Set(paths.map((p) => p.split("/")[0]));
  const nested = paths.every((p) => p.includes("/"));
  const strip = nested && firstSegments.size === 1;
  for (const p of paths) {
    map.set(p, strip ? p.slice(p.indexOf("/") + 1) : p);
  }
  return map;
}

/** Which template file a (normalised) path is, or null. `AGENTS.md` at the
 * root; a task file under `.buildmill/`, or bare at the root so a hand-made
 * zip of loose files still works. Anything deeper is not ours. */
function keyForPath(path: string): { key: string; path: string } | null {
  const parts = path.split("/");
  const name = parts[parts.length - 1];
  const dir = parts.slice(0, -1).join("/");
  const key = keyForName(name);
  if (!key) return null;
  if (key === AGENTS_KEY) {
    return dir === "" ? { key, path: AGENTS_FILE } : null;
  }
  if (dir === "" || dir === INSTRUCTION_ROOT) {
    return { key, path: `${INSTRUCTION_ROOT}/${name}` };
  }
  return null;
}

/** Read a zip into the template files it carries. Throws when the bytes are
 * not a zip. Directory entries and archiver noise are dropped silently;
 * anything else that is not a template file is listed in `ignored`. */
export function readTemplateZip(bytes: Uint8Array): ReadResult {
  const unzipped = unzipSync(bytes);
  const entries = Object.keys(unzipped)
    .map((original) => ({
      original,
      cleaned: original.replace(/\\/g, "/").replace(/^\/+/, ""),
    }))
    .filter((e) => !e.cleaned.endsWith("/") && !isNoise(e.cleaned));
  const normalised = stripCommonFolder(entries.map((e) => e.cleaned));
  const files: ZipFile[] = [];
  const ignored: string[] = [];
  const seen = new Set<string>();
  for (const e of entries) {
    const path = normalised.get(e.cleaned) ?? e.cleaned;
    const hit = keyForPath(path);
    if (!hit || seen.has(hit.key)) {
      ignored.push(path);
      continue;
    }
    seen.add(hit.key);
    files.push({ key: hit.key, path: hit.path, text: strFromU8(unzipped[e.original]) });
  }
  return { files, ignored };
}

export type ImportPlan = {
  /** Files whose text differs and is non-empty — written. */
  overwrite: ZipFile[];
  /** Files the zip carries empty — their section is deleted. */
  cleared: ZipFile[];
  /** Files whose text is identical to what the template holds. */
  unchanged: ZipFile[];
};

/** What an import would do to a template, so the manager confirms the plan,
 * not the file picker. A cleared file that is already empty is unchanged. */
export function planImport(current: TemplateContents, files: ZipFile[]): ImportPlan {
  const plan: ImportPlan = { overwrite: [], cleared: [], unchanged: [] };
  for (const f of files) {
    const before = contentFor(current, f.key);
    if (f.text.trim() === "") {
      if (before.trim() === "") plan.unchanged.push(f);
      else plan.cleared.push(f);
    } else if (f.text === before) {
      plan.unchanged.push(f);
    } else {
      plan.overwrite.push(f);
    }
  }
  return plan;
}

/** The import confirmation offers one checkbox per group — the document, then
 * the phase groups the tree draws — so a manager can take a zip's Coding files
 * and leave its AGENTS.md alone. `agents` is the document's group key. */
export type ImportGroup = {
  key: string;
  label: string;
  /** Files from the plan that fall in this group, in plan order. */
  overwrite: ZipFile[];
  cleared: ZipFile[];
  unchanged: ZipFile[];
};

export const AGENTS_GROUP_KEY = AGENTS_KEY;

/** Which group a file key belongs to: `agents` for the document, otherwise the
 * phase group that lists its kind. */
export function groupKeyFor(fileKey: string): string | null {
  if (fileKey === AGENTS_KEY) return AGENTS_GROUP_KEY;
  const g = INSTRUCTION_GROUPS.find((grp) => grp.kinds.includes(fileKey));
  return g?.key ?? null;
}

/** Split a plan into the groups it touches — only groups with at least one
 * file in the plan, document first, then in tree order. */
export function groupPlan(plan: ImportPlan): ImportGroup[] {
  const groups: ImportGroup[] = [
    { key: AGENTS_GROUP_KEY, label: "AGENTS.md", overwrite: [], cleared: [], unchanged: [] },
    ...INSTRUCTION_GROUPS.map((g) => ({
      key: g.key,
      label: g.label,
      overwrite: [],
      cleared: [],
      unchanged: [],
    })),
  ];
  const byKey = new Map(groups.map((g) => [g.key, g]));
  for (const bucket of ["overwrite", "cleared", "unchanged"] as const) {
    for (const f of plan[bucket]) {
      const gk = groupKeyFor(f.key);
      if (gk) byKey.get(gk)![bucket].push(f);
    }
  }
  return groups.filter((g) => g.overwrite.length + g.cleared.length + g.unchanged.length > 0);
}

/** The groups checked when the confirmation opens: every phase group, but not
 * the document — a project's AGENTS.md is the one file most often tuned by
 * hand, so overwriting it must be a choice, not a default. */
export function defaultSelectedGroups(groups: ImportGroup[]): Set<string> {
  return new Set(groups.map((g) => g.key).filter((k) => k !== AGENTS_GROUP_KEY));
}

/** Narrow a plan to the selected groups. */
export function filterPlan(plan: ImportPlan, selected: Set<string>): ImportPlan {
  const keep = (f: ZipFile) => {
    const gk = groupKeyFor(f.key);
    return gk !== null && selected.has(gk);
  };
  return {
    overwrite: plan.overwrite.filter(keep),
    cleared: plan.cleared.filter(keep),
    unchanged: plan.unchanged.filter(keep),
  };
}

/** The admin api caps a per-task file at 20,000 characters and the document
 * at 200,000 (`ProjectTemplateSectionBody`, `ProjectTemplatePatch`). Refuse
 * before the first write rather than fail half-way. Returns the offending
 * file's path, or null. */
export const TASK_FILE_MAX = 20000;
export const DOCUMENT_MAX = 200000;

export function oversizeFile(files: ZipFile[]): ZipFile | null {
  for (const f of files) {
    const cap = f.key === AGENTS_KEY ? DOCUMENT_MAX : TASK_FILE_MAX;
    if (f.text.length > cap) return f;
  }
  return null;
}
