"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { CheckCircle2, ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useRouter } from "@/lib/router-with-progress";
import { createClient } from "@/lib/supabase/client";
import { closeEpic } from "@/lib/close-epic";
import { isIssueTerminal } from "@/lib/epics";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { StageDots } from "@/components/stage-tracker";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";
import {
  compareWorkItemSequence,
  epicLabel,
  projectColor,
  workItemDisplayId,
} from "@/lib/work-items";
import {
  type HubEpic,
  type HubProject,
  type ViewIssue,
} from "./issue-view-types";

const NO_EPIC = "__none__";

/**
 * US-8.3: the Outline lens — Project → Epic → Feature → Story, collapsible at
 * every tier. On load each project is expanded; US-71.1: epics list
 * newest-first, open epics start expanded, closed epics (and the No-epic
 * bucket) collapse to a header + count, and an open epic whose items are all
 * merged/done offers Close right on its header.
 */
export function IssueOutlineView({
  issues,
  projects,
  epics,
  singleProject,
  gateIssues,
  searchActive,
  recentlyChanged,
}: {
  issues: ViewIssue[];
  projects: HubProject[];
  epics: HubEpic[];
  singleProject: boolean;
  /** US-71.1: the pre-type/status-filter set — the Close gate must not be
   * fooled by a filter hiding an epic's open items. */
  gateIssues: ViewIssue[];
  /** A search narrows what the client even loaded, so the Close buttons
   * hide rather than gate on a partial set. */
  searchActive: boolean;
  /** US-87.12: ids whose rows just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  // Explicit expand/collapse overrides keyed per tier; absent → the default.
  const [overrides, setOverrides] = useState<Map<string, boolean>>(new Map());
  const expanded = (key: string, def: boolean) =>
    overrides.has(key) ? overrides.get(key)! : def;
  const toggle = (key: string, def: boolean) =>
    setOverrides((prev) => {
      const next = new Map(prev);
      next.set(key, !(prev.has(key) ? prev.get(key)! : def));
      return next;
    });

  const epicsByProject = useMemo(() => {
    const map = new Map<string, HubEpic[]>();
    for (const e of epics) {
      const list = map.get(e.project_id) ?? [];
      list.push(e);
      map.set(e.project_id, list);
    }
    // US-71.1: latest epic on top.
    for (const list of map.values()) list.sort((a, b) => b.number - a.number);
    return map;
  }, [epics]);

  if (!issues.length) {
    return (
      <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        No work items to show.
      </p>
    );
  }

  const renderProjectBody = (project: HubProject) => {
    const projectIssues = issues.filter((i) => i.project_id === project.id);
    const projectEpics = epicsByProject.get(project.id) ?? [];

    // Epic buckets in numbering order, then a No-epic bucket if it has items.
    const buckets: { id: string; epic: HubEpic | null }[] = projectEpics.map(
      (e) => ({ id: e.id, epic: e })
    );
    const hasNoEpic = projectIssues.some((i) => !i.epic_id);
    if (hasNoEpic) buckets.push({ id: NO_EPIC, epic: null });

    // Children (stories under a feature) render nested; keep the rest flat.
    const presentFeatureIds = new Set(
      projectIssues.filter((i) => i.type === "feature").map((i) => i.id)
    );
    const childrenByParent = new Map<string, ViewIssue[]>();
    for (const i of projectIssues) {
      if (i.parent_id && presentFeatureIds.has(i.parent_id)) {
        const list = childrenByParent.get(i.parent_id) ?? [];
        list.push(i);
        childrenByParent.set(i.parent_id, list);
      }
    }
    // US-74.1: the shared sequence comparator — the Table nests with the
    // same one, so a feature's stories read in one order everywhere.
    const bySeq = compareWorkItemSequence;
    for (const list of childrenByParent.values()) list.sort(bySeq);

    if (!buckets.length) {
      return (
        <p className="px-3 py-2 text-xs text-muted-foreground">
          No work items in this project.
        </p>
      );
    }

    return buckets.map(({ id, epic }) => {
      const inBucket = projectIssues.filter(
        (i) => (i.epic_id ?? NO_EPIC) === id
      );
      const isActive = !!epic?.active;
      const isClosed = epic?.status === "completed";
      const key = `e:${project.id}:${id}`;
      // US-71.1: open epics start expanded, closed ones (and No-epic) start
      // collapsed — expansion follows status, not activeness.
      const defOpen = !!epic && !isClosed;
      const open = expanded(key, defOpen);

      // US-71.1: Close is offered when every item the epic has (in the
      // pre-filter set) is merged/done — at least one, so a brand-new empty
      // epic isn't nudged shut. The epics_guard_completion trigger stays
      // the enforcement for anything the client can't see.
      const gateItems = epic
        ? gateIssues.filter((i) => i.epic_id === epic.id)
        : [];
      const closable =
        !!epic &&
        !isClosed &&
        !searchActive &&
        gateItems.length > 0 &&
        gateItems.every((i) =>
          isIssueTerminal({ status: i.status as IssueStatus })
        );

      // Rows for this epic: feature rows (with nested children) + flat items.
      const features = inBucket
        .filter((i) => i.type === "feature")
        .sort(bySeq);
      const flat = inBucket
        .filter(
          (i) =>
            i.type !== "feature" &&
            !(i.parent_id && presentFeatureIds.has(i.parent_id))
        )
        .sort(bySeq);

      return (
        <div key={id} className="border-t first:border-t-0">
          {/* The Close button cannot nest inside the toggle (button-in-button
              is invalid HTML), so the header is a flex row of the two. */}
          <div className="flex items-center">
            <button
              type="button"
              onClick={() => toggle(key, defOpen)}
              className="flex min-w-0 flex-1 items-center gap-1.5 px-3 py-2 text-left hover:bg-muted/40"
            >
              {open ? (
                <ChevronDown className="size-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" />
              )}
              <span className="truncate text-sm font-medium">
                {epic ? epicLabel(epic.number, epic.title) : "No epic"}
              </span>
              {isActive && (
                <Badge variant="secondary" className="text-[10px]">
                  Active
                </Badge>
              )}
              {isClosed && (
                <Badge
                  variant="outline"
                  className="text-[10px] text-muted-foreground"
                >
                  Closed
                </Badge>
              )}
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {inBucket.length}
              </span>
            </button>
            {closable && epic && <CloseEpicButton epic={epic} />}
          </div>

          {open && (
            <div className="pb-1">
              {features.map((f) => {
                const children = childrenByParent.get(f.id) ?? [];
                const fKey = `f:${f.id}`;
                const fOpen = expanded(fKey, true);
                return (
                  <div key={f.id}>
                    <OutlineRow
                      issue={f}
                      indent={1}
                      changed={recentlyChanged?.has(f.id)}
                      caret={
                        children.length
                          ? {
                              open: fOpen,
                              onClick: () => toggle(fKey, true),
                            }
                          : undefined
                      }
                    />
                    {fOpen &&
                      children.map((c) => (
                        <OutlineRow
                          key={c.id}
                          issue={c}
                          indent={2}
                          changed={recentlyChanged?.has(c.id)}
                        />
                      ))}
                  </div>
                );
              })}
              {flat.map((i) => (
                <OutlineRow
                  key={i.id}
                  issue={i}
                  indent={1}
                  changed={recentlyChanged?.has(i.id)}
                />
              ))}
              {!features.length && !flat.length && (
                <p className="px-3 py-1.5 pl-9 text-xs text-muted-foreground">
                  No items.
                </p>
              )}
            </div>
          )}
        </div>
      );
    });
  };

  // Single project: skip the Project tier and start at Epic.
  if (singleProject && projects.length) {
    return (
      <div className="rounded-lg border">{renderProjectBody(projects[0])}</div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      {projects.map((project) => {
        const count = issues.filter(
          (i) => i.project_id === project.id
        ).length;
        const pKey = `p:${project.id}`;
        const open = expanded(pKey, true);
        return (
          <div key={project.id} className="overflow-hidden rounded-lg border">
            <button
              type="button"
              onClick={() => toggle(pKey, true)}
              className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40"
              style={{
                boxShadow: `inset 3px 0 0 ${projectColor(project.id)}`,
              }}
            >
              {open ? (
                <ChevronDown className="size-4 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
              )}
              <span className="text-sm font-semibold">{project.name}</span>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {count}
              </span>
            </button>
            {open && <div className="border-t">{renderProjectBody(project)}</div>}
          </div>
        );
      })}
    </div>
  );
}

/** US-71.1: close a finished epic right where it reads as finished. The DB
 * close-gate (epics_guard_completion) is the enforcement — its refusal
 * surfaces as the error toast. */
function CloseEpicButton({ epic }: { epic: HubEpic }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function handleClose() {
    setBusy(true);
    try {
      const error = await closeEpic(createClient(), {
        id: epic.id,
        projectId: epic.project_id,
        active: epic.active,
      });
      if (error) {
        toastError("Could not close the epic", error);
        return;
      }
      toastSuccess("Epic closed", epicLabel(epic.number, epic.title));
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      variant="outline"
      size="sm"
      className="mr-2 h-6 shrink-0 gap-1 px-2 text-xs"
      disabled={busy}
      onClick={handleClose}
    >
      {busy ? (
        <Loader2 className="size-3 animate-spin" />
      ) : (
        <CheckCircle2 className="size-3" />
      )}
      Close epic
    </Button>
  );
}

function OutlineRow({
  issue: i,
  indent,
  caret,
  changed,
}: {
  issue: ViewIssue;
  indent: number;
  caret?: { open: boolean; onClick: () => void };
  changed?: boolean;
}) {
  const displayId = workItemDisplayId({
    type: i.type,
    epicNumber: i.epic_number,
    itemNo: i.item_no,
    subNo: i.sub_no,
  });
  // Indent aligns rows under the epic caret; feature=1, story=2.
  const pad = 12 + indent * 16;
  return (
    // US-68.6: below, the title had `min-w-0` — happy to shrink to nothing —
    // beside four shrink-0 badges, so on a phone it could be squeezed to a
    // handful of visible characters while every badge stayed full-size.
    // `flex-wrap` plus a `min-w-24` floor on the title means once nothing
    // more can give, the status badge is what wraps to its own line instead.
    // US-87.4: `list-row` lets the browser skip layout and paint for rows
    // scrolled out of view. The node stays in the DOM, so nesting, in-page
    // find and deep links are untouched.
    <div
      data-changed={changed ? "" : undefined}
      className="group list-row flex flex-wrap items-center gap-x-2 gap-y-1 py-1.5 pr-3 hover:bg-muted/40"
      style={{ paddingLeft: pad }}
    >
      {caret ? (
        <button
          type="button"
          onClick={caret.onClick}
          aria-label={caret.open ? "Collapse" : "Expand"}
          className="shrink-0 text-muted-foreground hover:text-foreground"
        >
          {caret.open ? (
            <ChevronDown className="size-3.5" />
          ) : (
            <ChevronRight className="size-3.5" />
          )}
        </button>
      ) : (
        <span className="w-3.5 shrink-0" />
      )}
      {displayId && (
        <span className="shrink-0 font-mono text-xs text-muted-foreground">
          {displayId}
        </span>
      )}
      <TypeBadge type={i.type as IssueType} />
      <Link
        href={`/issues/${i.id}?from=work-items`}
        className="min-w-24 flex-1 truncate text-sm font-medium hover:underline"
      >
        {i.title}
      </Link>
      <StageDots
        type={i.type}
        status={i.status}
        className="hidden shrink-0 md:inline-flex"
      />
      <StatusBadge status={i.status as IssueStatus} />
    </div>
  );
}
