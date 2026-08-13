/** Escape `%`, `_`, and `\` for ilike patterns. */
export function escapeIlike(raw: string): string {
  return raw.replace(/\\/g, "\\\\").replace(/%/g, "\\%").replace(/_/g, "\\_");
}

/** Quote a filter value for PostgREST `.or()` when it has special chars. */
function quoteFilterValue(value: string): string {
  if (/[,().]/.test(value) || value.includes('"')) {
    return `"${value.replace(/"/g, '\\"')}"`;
  }
  return value;
}

type OrFilterable = {
  or: (filters: string) => OrFilterable;
};

/**
 * Apply US-2.11 search: title, body, acceptance_criteria::text, github #.
 */
export function applyIssueSearch<T extends OrFilterable>(
  query: T,
  q: string | undefined | null
): T {
  const trimmed = q?.trim();
  if (!trimmed) return query;

  const pattern = quoteFilterValue(`%${escapeIlike(trimmed)}%`);
  // search_text is a generated column (title + body + AC text, migration
  // 036) — PostgREST or-filters can't cast jsonb, so we search that.
  const parts = [`search_text.ilike.${pattern}`];

  const digits = trimmed.replace(/^#/, "");
  if (/^\d+$/.test(digits)) {
    parts.push(`github_issue_number.eq.${digits}`);
  }

  return query.or(parts.join(",")) as T;
}

/**
 * Client-side match for Realtime rows when `q` is active.
 *
 * US-87.3: `search_text` is checked FIRST and is the authoritative field —
 * it is the same generated column (migration 036) that `applyIssueSearch`
 * above filters on server-side, so a row this function keeps is exactly a row
 * the server would have returned. A `postgres_changes` payload carries the
 * whole row, so it is always present there; `title`/`body`/`acceptance_criteria`
 * remain as fallbacks for callers holding a narrower row (the hub's list
 * select no longer fetches the prose columns), and `title` alone still
 * decides those. Every branch here can only ADD a match — nothing added for
 * performance may cause a matching item to be dropped from a search result.
 */
export function issueMatchesQuery(
  issue: {
    title?: string | null;
    body?: string | null;
    search_text?: string | null;
    acceptance_criteria?: unknown;
    github_issue_number?: number | null;
  },
  q: string
): boolean {
  const trimmed = q.trim().toLowerCase();
  if (!trimmed) return true;

  if (issue.search_text?.toLowerCase().includes(trimmed)) return true;
  if (issue.title?.toLowerCase().includes(trimmed)) return true;
  if (issue.body?.toLowerCase().includes(trimmed)) return true;

  const ac = issue.acceptance_criteria;
  if (ac != null) {
    const acText = Array.isArray(ac)
      ? ac.map(String).join(" ")
      : typeof ac === "string"
        ? ac
        : JSON.stringify(ac);
    if (acText.toLowerCase().includes(trimmed)) return true;
  }

  const digits = trimmed.replace(/^#/, "");
  if (
    /^\d+$/.test(digits) &&
    issue.github_issue_number != null &&
    String(issue.github_issue_number) === digits
  ) {
    return true;
  }

  return false;
}
