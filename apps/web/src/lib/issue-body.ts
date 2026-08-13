/** Type-specific shape of the issues.body markdown column (us-2.2).
 *
 * Only "story" and (optionally) "feature"/"bug" carry acceptance criteria —
 * `body` itself stays a single markdown text column, so a bug's Repro/
 * Expected sections are encoded as headed sections within it rather than
 * separate columns.
 */

export type IssueType = "feature" | "bug" | "chore" | "story";

export const ISSUE_TYPES: IssueType[] = ["feature", "bug", "chore", "story"];

export const TYPE_LABELS: Record<IssueType, string> = {
  feature: "Feature",
  bug: "Bug",
  chore: "Chore",
  story: "Story",
};

export const TYPE_DESCRIPTIONS: Record<IssueType, string> = {
  feature: "A larger capability — drafted as a PRD, then split into stories.",
  bug: "Something broken — describe the repro and the expected behavior.",
  chore: "Small housekeeping work with no user-facing acceptance criteria.",
  story: "A single engineering slice with acceptance criteria the factory can act on.",
};

const REPRO_HEADER = "## Repro";
const EXPECTED_HEADER = "## Expected";

export type BugBody = { repro: string; expected: string };

export function composeBugBody(repro: string, expected: string): string {
  return `${REPRO_HEADER}\n\n${repro.trim()}\n\n${EXPECTED_HEADER}\n\n${expected.trim()}`;
}

/** Best-effort parse — tolerates plain text written before this shape existed. */
export function parseBugBody(body: string | null): BugBody {
  const text = body ?? "";
  const reproIdx = text.indexOf(REPRO_HEADER);
  const expectedIdx = text.indexOf(EXPECTED_HEADER);
  if (reproIdx === -1 && expectedIdx === -1) {
    return { repro: text.trim(), expected: "" };
  }
  let repro = "";
  let expected = "";
  if (reproIdx !== -1) {
    const start = reproIdx + REPRO_HEADER.length;
    const end = expectedIdx !== -1 && expectedIdx > reproIdx ? expectedIdx : text.length;
    repro = text.slice(start, end).trim();
  }
  if (expectedIdx !== -1) {
    expected = text.slice(expectedIdx + EXPECTED_HEADER.length).trim();
  }
  return { repro, expected };
}

/** Whether acceptance criteria are required to save (story only — others optional/none). */
export function requiresAcceptanceCriteria(type: IssueType): boolean {
  return type === "story";
}

/** Whether the acceptance criteria editor should show at all. */
export function supportsAcceptanceCriteria(type: IssueType): boolean {
  return type !== "chore";
}
