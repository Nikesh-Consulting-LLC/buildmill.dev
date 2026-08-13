"use client";

import { useCallback, useEffect, useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { FolderGit2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/empty-state";
import { type IssueStatus } from "@/components/status-badge";
import { type IssueType } from "@/components/type-badge";
import { setGlobalProjects } from "@/lib/global-project-filter-action";
import { AbandonedIssueList } from "./abandoned-issue-list";
import { IssueSearchInput } from "./issue-search-input";
import {
  IssueViews,
  LensSwitch,
  StatusFilter,
  TypeFilters,
} from "./issue-views";
import { useHubIssues } from "./use-project-issues";
import { hubIssuesQuery, hubAbandonedQuery } from "./hub-query";
import {
  HUB_PAGE_SIZE,
  ISSUES_LAYOUT_KEY,
  mapIssueRow,
  parseIssuesLayout,
  type HubEpic,
  type HubProject,
  type IssuesLayout,
  type ViewIssue,
} from "./issue-view-types";

type AbandonedRow = {
  id: string;
  title: string;
  status: string;
  updated_at: string;
  project_id: string;
};

/**
 * US-8.1 / US-8.2: the cross-project Work Items hub. Owns the project
 * selection (remembered per browser), the live issue set spanning it, the
 * shared toolbar state (lens + filters), and the New-work-item create flow.
 */
export function IssuesHub({
  projects,
  epics,
  activeIssues,
  abandonedIssues,
  selectedIds,
  seededProjectId,
  searchQuery,
  showAbandoned,
  totalActive,
  totalAbandoned,
}: {
  projects: HubProject[];
  epics: HubEpic[];
  activeIssues: ViewIssue[];
  abandonedIssues: AbandonedRow[];
  /** Phase 64: resolved server-side from the global project filter (already
   * narrowed to `[seededProjectId]` when a deep link named one). */
  selectedIds: string[];
  seededProjectId: string | null;
  searchQuery: string;
  showAbandoned: boolean;
  /** US-87.3: how many rows MATCH, against the page actually delivered. */
  totalActive: number;
  totalAbandoned: number;
}) {
  // US-87.5: the hub's live subscription is scoped to this workspace. Every
  // project on the hub belongs to the active org, so any of them names it.
  const orgId = projects[0]?.org_id ?? "";
  const [issues, setIssues, recentlyChanged] = useHubIssues(
    activeIssues,
    searchQuery,
    epics,
    orgId
  );
  const selected = useMemo(() => new Set(selectedIds), [selectedIds]);

  // US-87.3: the hub is bounded now, so it has to say so and offer the rest.
  // `extraAbandoned` is kept separate from the server's first page for the
  // same reason `issues` is: the server page resets on every navigation.
  const [extraAbandoned, setExtraAbandoned] = useState<AbandonedRow[]>([]);
  const [loadingMore, setLoadingMore] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    setExtraAbandoned([]);
    setLoadError(null);
  }, [searchQuery, showAbandoned, selectedIds]);

  const loadMore = useCallback(async () => {
    setLoadingMore(true);
    setLoadError(null);
    const supabase = createClient();
    // Same builder the server page used — one definition, so page 2 can never
    // apply a different filter or order than page 1.
    const opts = {
      projectIds: selectedIds,
      search: searchQuery,
      from: showAbandoned
        ? abandonedIssues.length + extraAbandoned.length
        : issues.length,
      to:
        (showAbandoned
          ? abandonedIssues.length + extraAbandoned.length
          : issues.length) + HUB_PAGE_SIZE - 1,
    };
    const { data, error } = showAbandoned
      ? await hubAbandonedQuery(supabase, opts)
      : await hubIssuesQuery(supabase, opts);
    if (error) {
      setLoadError(error.message);
    } else if (showAbandoned) {
      setExtraAbandoned((prev) => [...prev, ...((data ?? []) as AbandonedRow[])]);
    } else {
      const rows = (data ?? []).map((r) =>
        mapIssueRow(r as Record<string, unknown>)
      );
      // Realtime may already have delivered a row this page also contains.
      setIssues((prev) => {
        const seen = new Set(prev.map((x) => x.id));
        return [...prev, ...rows.filter((r) => !seen.has(r.id))];
      });
    }
    setLoadingMore(false);
  }, [
    selectedIds,
    searchQuery,
    showAbandoned,
    issues.length,
    abandonedIssues.length,
    extraAbandoned.length,
    setIssues,
  ]);

  // Shared toolbar state, one compact row on the hub.
  const [layout, setLayout] = useState<IssuesLayout>("outline");
  const [typeFilter, setTypeFilter] = useState<IssueType | null>(null);
  const [statusFilter, setStatusFilter] = useState<IssueStatus | null>(null);
  const [, startTransition] = useTransition();

  useEffect(() => {
    setLayout(parseIssuesLayout(window.localStorage.getItem(ISSUES_LAYOUT_KEY)));
  }, []);

  // A deep-linked project narrows the global filter itself, not just this
  // view — so leaving Work Items for another page keeps the same scope.
  useEffect(() => {
    if (!seededProjectId) return;
    startTransition(async () => {
      await setGlobalProjects([seededProjectId]);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seededProjectId]);

  function chooseLayout(next: IssuesLayout) {
    setLayout(next);
    window.localStorage.setItem(ISSUES_LAYOUT_KEY, next);
  }

  const selectedProjects = useMemo(
    () => projects.filter((p) => selected.has(p.id)),
    [projects, selected]
  );
  const visibleIssues = useMemo(
    () => issues.filter((i) => selected.has(i.project_id)),
    [issues, selected]
  );
  const visibleAbandoned = useMemo(
    () =>
      [...abandonedIssues, ...extraAbandoned].filter((a) =>
        selected.has(a.project_id)
      ),
    [abandonedIssues, extraAbandoned, selected]
  );

  // US-87.3: what is loaded, against what matches. `visible*` counts are what
  // the manager can actually see, so the two numbers answer the question the
  // footer raises ("is there more?") rather than a different one.
  const shownCount = showAbandoned ? visibleAbandoned.length : visibleIssues.length;
  const matchCount = showAbandoned ? totalAbandoned : totalActive;
  const hasMore = shownCount < matchCount;

  function tabHref(abandoned: boolean) {
    const params = new URLSearchParams();
    if (seededProjectId) params.set("project", seededProjectId);
    if (abandoned) params.set("view", "abandoned");
    if (searchQuery) params.set("q", searchQuery);
    const s = params.toString();
    return s ? `/issues?${s}` : "/issues";
  }

  return (
    <div className="flex w-full flex-col gap-3">
      {/* US-71.1: search leads, top-left. Row 1: search · type + status
          filters; row 2: view tabs · lens switch (project selection is the
          global filter in the app shell; New work item lives in the page
          header, next to it). */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <IssueSearchInput
          seededProjectId={seededProjectId}
          abandoned={showAbandoned}
          initialQuery={searchQuery}
        />
        {!showAbandoned && (
          <div className="flex flex-wrap items-center gap-1.5">
            <TypeFilters value={typeFilter} onChange={setTypeFilter} />
            <StatusFilter value={statusFilter} onChange={setStatusFilter} />
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <ViewTabs
          showAbandoned={showAbandoned}
          abandonedCount={visibleAbandoned.length}
          hrefFor={tabHref}
        />
        {!showAbandoned && (
          <LensSwitch layout={layout} onChange={chooseLayout} />
        )}
      </div>

      {showAbandoned ? (
        searchQuery && visibleAbandoned.length === 0 ? (
          <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
            No abandoned work items match &ldquo;{searchQuery}&rdquo;.
          </p>
        ) : (
          <AbandonedIssueList issues={visibleAbandoned} />
        )
      ) : selectedProjects.length === 0 ? (
        <EmptyState
          icon={FolderGit2}
          title="No projects selected"
          description="Pick one or more projects to see and add work items."
        />
      ) : (
        <IssueViews
          issues={visibleIssues}
          setIssues={setIssues}
          projects={selectedProjects}
          epics={epics}
          searchQuery={searchQuery}
          layout={layout}
          typeFilter={typeFilter}
          statusFilter={statusFilter}
          recentlyChanged={recentlyChanged}
        />
      )}

      {/* US-87.3: the hub loads one page at a time, so it says what it has
          and offers the rest. Silence here would read as "that's all of
          them", which is exactly the lie an unannounced cap tells. */}
      {(hasMore || loadError) && selectedProjects.length > 0 && (
        <div className="flex flex-wrap items-center justify-center gap-3 border-t pt-3 text-sm text-muted-foreground">
          <span>
            Showing {shownCount} of {matchCount}
            {searchQuery ? " matching" : ""} work item
            {matchCount === 1 ? "" : "s"}
          </span>
          {hasMore && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void loadMore()}
              disabled={loadingMore}
            >
              {loadingMore ? "Loading…" : `Load ${HUB_PAGE_SIZE} more`}
            </Button>
          )}
          {loadError && (
            <span className="text-destructive">
              Could not load more: {loadError}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// Segmented control (matches ui/tabs TabsList) — one muted container with the
// active view as a raised pill. URL-driven, so the segments are Links.
function ViewTabs({
  showAbandoned,
  abandonedCount,
  hrefFor,
}: {
  showAbandoned: boolean;
  abandonedCount: number;
  hrefFor: (abandoned: boolean) => string;
}) {
  const seg =
    "inline-flex h-6 items-center justify-center gap-1.5 rounded-md px-2.5 text-sm font-medium whitespace-nowrap transition-colors";
  const on = "bg-background text-foreground shadow-sm";
  const off = "text-muted-foreground hover:text-foreground";
  return (
    <div className="inline-flex h-8 w-fit items-center gap-1 rounded-lg bg-muted p-1">
      <Link href={hrefFor(false)} className={cn(seg, showAbandoned ? off : on)}>
        Work Items
      </Link>
      <Link href={hrefFor(true)} className={cn(seg, showAbandoned ? on : off)}>
        Abandoned ({abandonedCount})
      </Link>
    </div>
  );
}
