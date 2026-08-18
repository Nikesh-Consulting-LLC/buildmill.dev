/** US-118.3: the rules behind the New project template row, kept out of the
 * JSX so they are testable and stated once.
 *
 *  - the org's default is preselected, else the first, else nothing;
 *  - category chips appear only when there are two or more distinct
 *    non-empty categories — one category is not a choice;
 *  - a filter box appears only past six templates — below that the row is
 *    the filter;
 *  - filtering narrows what is shown and never changes the selection; the
 *    line under the row states the selection so a hidden one is still said. */

export type PickableTemplate = {
  id: string;
  name: string;
  description?: string | null;
  category?: string | null;
  is_default?: boolean | null;
};

export const FILTER_BOX_THRESHOLD = 6;

export function defaultTemplateId<T extends PickableTemplate>(templates: readonly T[]): string {
  return templates.find((t) => t.is_default)?.id ?? templates[0]?.id ?? "";
}

/** Distinct non-empty categories, in the order first seen (the row's order). */
export function categoriesOf<T extends PickableTemplate>(templates: readonly T[]): string[] {
  const seen: string[] = [];
  for (const t of templates) {
    const c = (t.category ?? "").trim();
    if (c && !seen.includes(c)) seen.push(c);
  }
  return seen;
}

export function showCategoryChips<T extends PickableTemplate>(templates: readonly T[]): boolean {
  return categoriesOf(templates).length >= 2;
}

export function showFilterBox<T extends PickableTemplate>(templates: readonly T[]): boolean {
  return templates.length > FILTER_BOX_THRESHOLD;
}

/** Narrow by category (`"all"` or a category name) and a free-text query on
 * name and description, case-insensitively. Selection is not this
 * function's business. */
export function filterTemplates<T extends PickableTemplate>(
  templates: readonly T[],
  category: string,
  query: string,
): T[] {
  const q = query.trim().toLowerCase();
  return templates.filter((t) => {
    if (category !== "all" && (t.category ?? "").trim() !== category) return false;
    if (!q) return true;
    return (
      t.name.toLowerCase().includes(q) || (t.description ?? "").toLowerCase().includes(q)
    );
  });
}
