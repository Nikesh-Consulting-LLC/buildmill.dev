/** us-101.2: the running order of a release's checklist.
 *
 * MIRROR of `SECTIONS` in `apps/api/app/release_notes.py`, which is
 * canonical. There is no build step between a Python API and a TypeScript web
 * app, so this is a hand-kept copy and
 * `apps/api/tests/test_release_section_map.py` parses both and fails if they
 * disagree in either direction — the pattern us-99.1 established for
 * `instruction-files.ts`, and it exists because an unchecked mirror is
 * exactly how that file came to list seven run kinds while the database
 * allowed ten.
 *
 * The ORDER is the whole point: a tester works top to bottom, and "the happy
 * path first, because every refusal below assumes the happy path's object
 * exists" is only true if the list is in this order.
 */
export const RELEASE_SECTIONS = [
  "pre-flight",
  "happy-path",
  "refusals",
  "regression",
  "other",
] as const;

export type ReleaseSection = (typeof RELEASE_SECTIONS)[number];

export const RELEASE_SECTION_LABELS: Record<string, string> = {
  "pre-flight": "Pre-flight",
  "happy-path": "The happy path",
  refusals: "The refusals",
  regression: "Regression",
  other: "Other",
};

export const DEFAULT_RELEASE_SECTION = "other";

/** Known sections in their order; anything the agent invented after them. */
export function sectionRank(key: string | null | undefined): number {
  const i = (RELEASE_SECTIONS as readonly string[]).indexOf(key ?? "");
  return i === -1 ? RELEASE_SECTIONS.length : i;
}

/** A heading for a section, including one this app has never seen. */
export function sectionLabel(key: string | null | undefined): string {
  const k = (key ?? "").trim();
  if (!k) return RELEASE_SECTION_LABELS[DEFAULT_RELEASE_SECTION];
  return (
    RELEASE_SECTION_LABELS[k] ??
    k.replace(/-/g, " ").replace(/^./, (c) => c.toUpperCase())
  );
}

/** Sort comparator for cases: section order, then `sort`, then title. */
export function compareCases<
  T extends { section?: string | null; sort?: number | null; title: string },
>(a: T, b: T): number {
  const bySection = sectionRank(a.section) - sectionRank(b.section);
  if (bySection !== 0) return bySection;
  // Two invented sections both rank last; keep them stably grouped by name.
  if (sectionRank(a.section) === RELEASE_SECTIONS.length) {
    const byName = (a.section ?? "").localeCompare(b.section ?? "");
    if (byName !== 0) return byName;
  }
  const as = a.sort ?? Number.MAX_SAFE_INTEGER;
  const bs = b.sort ?? Number.MAX_SAFE_INTEGER;
  if (as !== bs) return as - bs;
  return a.title.localeCompare(b.title);
}
