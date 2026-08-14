// US-91.5: the Work Items status filter is a set, not a single choice.
//
// The radio group answered "show me exactly one status" when the question a
// manager arrives with is "show me everything except the finished ones" — so
// the hub opened on a wall of merged and done items, most of which are most
// of them.
//
// Pure logic, so it can be tested without a DOM.

/** Unchecked on a first visit: finished work is one checkbox away, and its
 *  absence is stated on the trigger rather than left silent. */
export const HIDDEN_BY_DEFAULT: readonly string[] = ["merged", "done"];

/** Everything except the finished statuses. */
export function defaultStatusSelection(all: readonly string[]): Set<string> {
  return new Set(all.filter((s) => !HIDDEN_BY_DEFAULT.includes(s)));
}

/**
 * An empty selection shows nothing — deliberately, and stated on the trigger
 * as "No statuses". The alternative (treating empty as "everything") makes
 * the last uncheck silently do the opposite of every uncheck before it.
 */
export function matchesStatusFilter(
  status: string,
  selected: ReadonlySet<string>
): boolean {
  return selected.has(status);
}

/**
 * What the closed pill says. The rule is that it must never lie by omission:
 * a manager who cannot find an item has to be able to see why from the
 * toolbar, without opening the menu.
 */
export function statusFilterLabel(
  selected: ReadonlySet<string>,
  all: readonly string[],
  /** How a status is worded — `statusLabel` from the badge, so the filter
   *  says "PRD review" in the same words the pills do. Injected rather than
   *  imported so this module stays free of React and stays testable under
   *  the bare node runner, which does not resolve the `@/` alias. */
  labelOf: (status: string) => string = (s) => s
): string {
  if (selected.size === 0) return "No statuses";
  if (selected.size === all.length) return "All statuses";

  const hidden = all.filter((s) => !selected.has(s));
  if (hidden.length <= 2)
    return `All but ${hidden.map(labelOf).join(", ").toLowerCase()}`;
  if (selected.size <= 2)
    return all
      .filter((s) => selected.has(s))
      .map(labelOf)
      .join(", ");
  return `${selected.size} statuses`;
}

/** Serialise for `localStorage`; the reader tolerates anything it wrote. */
export function parseStoredSelection(
  raw: string | null,
  all: readonly string[]
): Set<string> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return null;
    const known = parsed.filter(
      (s): s is string => typeof s === "string" && all.includes(s)
    );
    return new Set(known);
  } catch {
    return null;
  }
}
