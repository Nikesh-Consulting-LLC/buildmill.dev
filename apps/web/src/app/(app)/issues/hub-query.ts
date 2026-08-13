// apps/web/src/app/(app)/issues/hub-query.ts
//
// US-87.3: ONE definition of the hub's work-item query, built once and used
// by both sides — the server component that renders the first page, and the
// browser client that fetches the next one behind `Load more`. Two hand-kept
// copies of a filter/order/select triple is how a "Load more" quietly starts
// returning rows the first page would never have shown.

import type { SupabaseClient } from "@supabase/supabase-js";
import { applyIssueSearch } from "@/lib/issue-search";
import { VIEW_ISSUE_SELECT } from "./issue-view-types";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyClient = SupabaseClient<any, any, any>;

export type HubQueryOptions = {
  /** The projects actually in view. Scoping here rather than in the browser
   * is the point of us-87.3: the hub used to fetch every project in the
   * workspace and narrow afterwards. */
  projectIds: string[];
  search: string;
  /** Zero-based, inclusive — PostgREST `.range()`. */
  from: number;
  to: number;
  /** Ask for the matching total so the hub can say "N of M". */
  withCount?: boolean;
};

export function hubIssuesQuery(supabase: AnyClient, opts: HubQueryOptions) {
  let query = supabase
    .from("issues")
    .select(
      VIEW_ISSUE_SELECT,
      opts.withCount ? { count: "exact" } : undefined
    )
    .in("project_id", opts.projectIds)
    .is("abandoned_at", null)
    .order("updated_at", { ascending: false })
    // Deterministic paging: `updated_at` is not unique, and two rows sharing
    // a timestamp across a page boundary would otherwise let one repeat and
    // another disappear entirely as pages are walked.
    .order("id", { ascending: false })
    .range(opts.from, opts.to);

  if (opts.search) query = applyIssueSearch(query, opts.search);
  return query;
}

export function hubAbandonedQuery(supabase: AnyClient, opts: HubQueryOptions) {
  let query = supabase
    .from("issues")
    .select(
      "id, title, status, updated_at, project_id",
      opts.withCount ? { count: "exact" } : undefined
    )
    .in("project_id", opts.projectIds)
    .not("abandoned_at", "is", null)
    .order("updated_at", { ascending: false })
    .order("id", { ascending: false })
    .range(opts.from, opts.to);

  if (opts.search) query = applyIssueSearch(query, opts.search);
  return query;
}
