/** PRD markdown shape (us-2.3). The API (workflow.py `draft_prd`) generates
 * content with exactly these headings, in this order — parsing must match. */

export const PRD_SECTIONS = [
  { key: "problem", heading: "Problem" },
  { key: "goals", heading: "Goals" },
  { key: "out_of_scope", heading: "Out of scope" },
  { key: "acceptance_criteria", heading: "Acceptance criteria" },
] as const;

export type PrdSectionKey = (typeof PRD_SECTIONS)[number]["key"];
export type PrdSections = Record<PrdSectionKey, string>;

export const EMPTY_PRD_SECTIONS: PrdSections = {
  problem: "",
  goals: "",
  out_of_scope: "",
  acceptance_criteria: "",
};

/** Best-effort split of a PRD markdown document into its known sections.
 * Content before the first recognized heading is discarded (there shouldn't
 * be any in a well-formed draft); unrecognized headings are ignored. */
export function parsePrdSections(content: string): PrdSections {
  const sections: PrdSections = { ...EMPTY_PRD_SECTIONS };
  if (!content) return sections;

  const headingByLower: Record<string, PrdSectionKey> = {};
  for (const s of PRD_SECTIONS) headingByLower[s.heading.toLowerCase()] = s.key;

  const lines = content.split("\n");
  let current: PrdSectionKey | null = null;
  let buffer: string[] = [];

  function flush() {
    if (current) sections[current] = buffer.join("\n").trim();
    buffer = [];
  }

  for (const line of lines) {
    const match = /^##\s+(.+?)\s*$/.exec(line);
    if (match && headingByLower[match[1].toLowerCase()]) {
      flush();
      current = headingByLower[match[1].toLowerCase()];
      continue;
    }
    if (current) buffer.push(line);
  }
  flush();

  return sections;
}

export function serializePrdSections(sections: PrdSections): string {
  return PRD_SECTIONS.map(
    ({ key, heading }) => `## ${heading}\n\n${(sections[key] ?? "").trim()}`
  ).join("\n\n");
}
