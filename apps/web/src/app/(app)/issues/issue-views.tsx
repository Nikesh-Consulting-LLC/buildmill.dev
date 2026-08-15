"use client";

import {
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type SetStateAction,
} from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { ChevronDown } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import { BulkDeleteBar } from "@/components/bulk-delete-bar";
import {
  StatusBadge,
  statusLabel,
  type IssueStatus,
} from "@/components/status-badge";
import { StageDots } from "@/components/stage-tracker";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  matchesStatusFilter,
  statusFilterLabel,
} from "@/lib/issue-status-filter";
import { ISSUE_TYPES, TYPE_LABELS } from "@/lib/issue-body";
import { projectColor, workItemDisplayId } from "@/lib/work-items";
import { ComplexityBadge } from "@/components/complexity-badge";
import { IssueOutlineView } from "./issue-outline-view";
import { IssueTableView } from "./issue-table-view";
import {
  ISSUES_LAYOUTS,
  ISSUE_PHASES,
  ISSUE_STATUS_ORDER,
  formatIssueWhen,
  phaseForStatus,
  type HubEpic,
  type HubProject,
  type IssuePhase,
  type IssuesLayout,
  type ViewIssue,
} from "./issue-view-types";

// Mirrors issue-actions.tsx: items mid-run can't be deleted.
const RUNNING_STATUSES = ["queued", "running"];

// Ordered status list for the toolbar Status filter.
const ALL_STATUSES = ISSUE_STATUS_ORDER as readonly IssueStatus[];

export function IssueViews({
  issues,
  setIssues,
  projects,
  epics,
  searchQuery,
  layout,
  typeFilter,
  statusFilter,
  recentlyChanged,
}: {
  /** Already narrowed to the selected projects, kept live by the hub. */
  issues: ViewIssue[];
  /** Master setter (spans all projects) for optimistic bulk delete. */
  setIssues: Dispatch<SetStateAction<ViewIssue[]>>;
  projects: HubProject[];
  epics: HubEpic[];
  searchQuery: string;
  /** Toolbar state lives on the hub (one compact row) and is passed down. */
  layout: IssuesLayout;
  typeFilter: IssueType | null;
  /** US-91.5: the statuses to show. Merged and done start unchecked. */
  statusFilter: ReadonlySet<string>;
  /** US-87.12: ids whose rows just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const singleProject = projects.length === 1;

  // Bulk selection is table-only; drop it when the lens changes.
  useEffect(() => {
    setSelected(new Set());
  }, [layout]);

  const filteredIssues = useMemo(() => {
    let out = issues;
    if (typeFilter) out = out.filter((i) => i.type === typeFilter);
    out = out.filter((i) => matchesStatusFilter(i.status, statusFilter));
    return out;
  }, [issues, typeFilter, statusFilter]);

  // Deletion only ever targets currently visible selected rows.
  const selectedVisible = useMemo(
    () => filteredIssues.filter((i) => selected.has(i.id)),
    [filteredIssues, selected]
  );
  const blocked = selectedVisible.filter((i) =>
    RUNNING_STATUSES.includes(i.status)
  );
  const deletable = selectedVisible.filter(
    (i) => !RUNNING_STATUSES.includes(i.status)
  );

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAllVisible() {
    const allSelected =
      filteredIssues.length > 0 &&
      filteredIssues.every((i) => selected.has(i.id));
    setSelected(
      allSelected ? new Set() : new Set(filteredIssues.map((i) => i.id))
    );
  }

  async function handleBulkDelete() {
    const supabase = createClient();

    // Force path: queued/running items the guard trigger would reject — the
    // user confirmed the force warning, so use force_delete_issues.
    if (blocked.length) {
      const ids = selectedVisible.map((i) => i.id);
      const { data, error } = await supabase.rpc("force_delete_issues", {
        p_issue_ids: ids,
      });
      if (error) throw new Error(error.message);
      const deleted = data ?? 0;
      const idSet = new Set(ids);
      setIssues((prev) => prev.filter((i) => !idSet.has(i.id)));
      setSelected(new Set());
      router.refresh();
      if (deleted < ids.length) {
        throw new Error(
          `Only ${deleted} of ${ids.length} work items were deleted — refresh and retry.`
        );
      }
      return;
    }

    const ids = deletable.map((i) => i.id);
    const { data, error } = await supabase
      .from("issues")
      .delete()
      .in("id", ids)
      .select("id");
    if (error) throw new Error(error.message);
    const deletedIds = new Set((data ?? []).map((r) => r.id));
    setIssues((prev) => prev.filter((i) => !deletedIds.has(i.id)));
    setSelected(new Set());
    router.refresh();
    if (deletedIds.size < ids.length) {
      throw new Error(
        `Only ${deletedIds.size} of ${ids.length} work items were deleted — the rest may have changed; refresh and retry.`
      );
    }
  }

  const searchEmpty = searchQuery.trim() && filteredIssues.length === 0;

  return (
    <div className="flex flex-col gap-3">
      {layout === "table" && (
        <BulkDeleteBar
          count={selectedVisible.length}
          onClear={() => setSelected(new Set())}
          notice={
            blocked.length
              ? `${blocked.length} of these ${blocked.length === 1 ? "is" : "are"} queued or running — deleting will force-delete ${blocked.length === 1 ? "it" : "them"}.`
              : undefined
          }
          confirmTitle={
            blocked.length
              ? `Force delete ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"}?`
              : `Delete ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"}?`
          }
          confirmDescription={
            blocked.length
              ? `This permanently deletes ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"} and their events, runs, and reviews. Queued or running and force-deleted: ${blocked.map((i) => i.title).join(", ")} — any run in flight is discarded and a worker still on it will fail to hand back. Linked test cases and documents are detached, not deleted. This can't be undone.`
              : `This permanently deletes ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"} and their events, runs, and reviews. This can't be undone.`
          }
          confirmLabel={blocked.length ? "Force delete work items" : "Delete work items"}
          requireText={blocked.length ? "force delete" : undefined}
          onDelete={handleBulkDelete}
        />
      )}

      {searchEmpty && (
        <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
          No work items match &ldquo;{searchQuery.trim()}&rdquo;.
        </p>
      )}

      {!searchEmpty && layout === "outline" && (
        <IssueOutlineView
          issues={filteredIssues}
          projects={projects}
          epics={epics}
          singleProject={singleProject}
          // US-71.1: the Close gate reads the pre-type/status-filter set so a
          // filter can't hide an epic's open items from it; a search narrows
          // what was even loaded, so the buttons hide instead.
          gateIssues={issues}
          searchActive={!!searchQuery.trim()}
          recentlyChanged={recentlyChanged}
        />
      )}
      {!searchEmpty && layout === "board" && (
        <IssueBoardView
          issues={filteredIssues}
          projects={projects}
          singleProject={singleProject}
          recentlyChanged={recentlyChanged}
        />
      )}
      {!searchEmpty && layout === "table" && (
        <IssueTableView
          issues={filteredIssues}
          projects={projects}
          epics={epics}
          singleProject={singleProject}
          selected={selected}
          onToggle={toggleSelected}
          onToggleAll={toggleAllVisible}
          recentlyChanged={recentlyChanged}
        />
      )}
    </div>
  );
}

// Toolbar controls — rendered by the hub in one compact row (state lives
// there); kept here alongside the lens rendering they drive.
export function LensSwitch({
  layout,
  onChange,
}: {
  layout: IssuesLayout;
  onChange: (next: IssuesLayout) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1">
      {ISSUES_LAYOUTS.map((v) => (
        <button
          key={v.id}
          type="button"
          onClick={() => onChange(v.id)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
            // US-92.2: five phase columns cannot be made to work at 375px, so
            // Board is hidden below `md` rather than offered and broken.
            v.id === "board" && "hidden md:inline-block",
            layout === v.id
              ? "border-primary bg-primary text-primary-foreground"
              : "border-input text-muted-foreground hover:bg-muted"
          )}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

export function TypeFilters({
  value,
  onChange,
}: {
  value: IssueType | null;
  onChange: (next: IssueType | null) => void;
}) {
  return (
    <>
      <button
        type="button"
        onClick={() => onChange(null)}
        className={cn(
          "rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          value === null
            ? "border-primary bg-primary text-primary-foreground"
            : "border-input text-muted-foreground hover:bg-muted"
        )}
      >
        All types
      </button>
      {ISSUE_TYPES.map((t) => (
        <button
          key={t}
          type="button"
          onClick={() => onChange(value === t ? null : t)}
          className={cn(
            "rounded-full border px-2.5 py-1 text-xs font-medium capitalize transition-colors",
            value === t
              ? "border-primary bg-primary text-primary-foreground"
              : "border-input text-muted-foreground hover:bg-muted"
          )}
        >
          {TYPE_LABELS[t]}
        </button>
      ))}
    </>
  );
}

export function StatusFilter({
  value,
  onChange,
}: {
  /** US-91.5: the set of statuses to SHOW. Empty means nothing shows. */
  value: ReadonlySet<string>;
  onChange: (v: ReadonlySet<string>) => void;
}) {
  const all = ALL_STATUSES as readonly string[];
  const everything = value.size === all.length;
  // Filtering is on unless every status is checked — the pill has to look
  // different when something is being withheld, including under the default.
  const filtering = !everything;

  function toggle(status: string) {
    const next = new Set(value);
    if (next.has(status)) next.delete(status);
    else next.add(status);
    onChange(next);
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium transition-colors",
          filtering
            ? "border-primary bg-primary text-primary-foreground"
            : "border-input text-muted-foreground hover:bg-muted"
        )}
      >
        {statusFilterLabel(value, all, statusLabel)}
        <ChevronDown className="size-3" />
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-52">
        {/* Reaching "everything" by ticking eleven boxes is how a filter
            becomes an obstacle. */}
        <div className="flex items-center justify-between gap-2 px-2 py-1.5 text-xs text-muted-foreground">
          <button
            type="button"
            className="hover:text-foreground"
            onClick={() => onChange(new Set(all))}
          >
            Select all
          </button>
          <button
            type="button"
            className="hover:text-foreground"
            onClick={() => onChange(new Set())}
          >
            Clear
          </button>
        </div>
        {ALL_STATUSES.map((s) => (
          <DropdownMenuCheckboxItem
            key={s}
            checked={value.has(s)}
            // The menu stays open while several boxes are ticked.
            closeOnClick={false}
            onCheckedChange={() => toggle(s)}
          >
            <StatusBadge status={s} />
          </DropdownMenuCheckboxItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// US-8.4: five phase columns, optional project swimlanes.
function IssueBoardView({
  issues,
  projects,
  singleProject,
  recentlyChanged,
}: {
  issues: ViewIssue[];
  projects: HubProject[];
  singleProject: boolean;
  /** US-87.12: ids whose cards just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  const [swimlanes, setSwimlanes] = useState(false);
  const canSwimlane = !singleProject;
  const grouped = canSwimlane && swimlanes;

  return (
    <div className="flex flex-col gap-3">
      {canSwimlane && (
        <label className="flex w-fit cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
          <input
            type="checkbox"
            className="size-3.5 accent-primary"
            checked={swimlanes}
            onChange={(e) => setSwimlanes(e.target.checked)}
          />
          Group by project
        </label>
      )}

      {grouped ? (
        <div className="flex flex-col gap-4">
          {projects.map((p) => {
            const projectIssues = issues.filter((i) => i.project_id === p.id);
            return (
              <div key={p.id} className="flex flex-col gap-2">
                <div className="flex items-center gap-2">
                  <span
                    className="h-4 w-1 rounded-full"
                    style={{ backgroundColor: projectColor(p.id) }}
                  />
                  <span className="text-sm font-medium">{p.name}</span>
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {projectIssues.length}
                  </span>
                </div>
                <PhaseColumns
                  issues={projectIssues}
                  accent
                  recentlyChanged={recentlyChanged}
                />
              </div>
            );
          })}
        </div>
      ) : (
        <PhaseColumns
          issues={issues}
          accent={!singleProject}
          recentlyChanged={recentlyChanged}
        />
      )}
    </div>
  );
}

function PhaseColumns({
  issues,
  accent,
  recentlyChanged,
}: {
  issues: ViewIssue[];
  accent: boolean;
  /** US-87.12: ids whose cards just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  const byPhase = useMemo(() => {
    const map = new Map<IssuePhase, ViewIssue[]>();
    for (const phase of ISSUE_PHASES) map.set(phase.id, []);
    for (const i of issues) map.get(phaseForStatus(i.status))!.push(i);
    return map;
  }, [issues]);

  return (
    <div className="overflow-x-auto pb-2">
      <div className="flex min-w-max gap-3">
        {ISSUE_PHASES.map((phase) => {
          const columnIssues = byPhase.get(phase.id) ?? [];
          return (
            <div
              key={phase.id}
              className="flex w-64 shrink-0 flex-col gap-2 rounded-lg border bg-muted/30 p-2"
            >
              <div className="flex items-center justify-between px-1 pt-1">
                <span className="text-xs font-semibold">{phase.id}</span>
                <span className="text-xs tabular-nums text-muted-foreground">
                  {columnIssues.length}
                </span>
              </div>
              <div
                className={cn(
                  "flex flex-1 flex-col gap-2",
                  !columnIssues.length && "min-h-16"
                )}
              >
                {columnIssues.map((i) => (
                  <BoardCard
                    key={i.id}
                    issue={i}
                    accent={accent}
                    changed={recentlyChanged?.has(i.id)}
                  />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function BoardCard({
  issue: i,
  accent,
  changed,
}: {
  issue: ViewIssue;
  accent: boolean;
  changed?: boolean;
}) {
  const displayId = workItemDisplayId({
    type: i.type,
    epicNumber: i.epic_number,
    itemNo: i.item_no,
    subNo: i.sub_no,
  });
  return (
    <Link
      href={`/issues/${i.id}?from=work-items`}
      data-changed={changed ? "" : undefined}
      className={cn(
        // US-87.4: `list-card` skips layout/paint for cards scrolled out of
        // a column's view (see globals.css).
        "list-card rounded-md border bg-card p-3 text-sm shadow-xs transition-colors hover:border-ring/60",
        accent && "border-l-2"
      )}
      style={accent ? { borderLeftColor: projectColor(i.project_id) } : undefined}
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <p className="line-clamp-2 font-medium">{i.title}</p>
        <TypeBadge type={i.type as IssueType} />
      </div>
      <StageDots type={i.type} status={i.status} className="mb-1.5" />
      <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
        <StatusBadge status={i.status as IssueStatus} issueType={i.type} />
        {i.complexity && <ComplexityBadge complexity={i.complexity} />}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {formatIssueWhen(i.updated_at)}
        </p>
        {displayId && (
          <span className="shrink-0 font-mono text-xs text-muted-foreground">
            {displayId}
          </span>
        )}
      </div>
    </Link>
  );
}
