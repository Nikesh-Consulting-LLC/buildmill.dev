"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowDown, ArrowUp, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { money } from "@/lib/budget";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  compareWorkItemSequence,
  epicLabel,
  projectColor,
  workItemDisplayId,
} from "@/lib/work-items";
import {
  ISSUE_STATUS_ORDER,
  formatIssueWhen,
  type HubEpic,
  type HubProject,
  type ViewIssue,
} from "./issue-view-types";

type SortKey = "title" | "status" | "updated";
type GroupBy = "project" | "epic" | "status" | "none";

const GROUP_LABELS: Record<GroupBy, string> = {
  project: "Project",
  epic: "Epic",
  status: "Status",
  none: "None",
};

const statusIndex = (s: string) => {
  const i = (ISSUE_STATUS_ORDER as readonly string[]).indexOf(s);
  return i === -1 ? 999 : i;
};

export function IssueTableView({
  issues,
  projects,
  epics,
  singleProject,
  selected,
  onToggle,
  onToggleAll,
  recentlyChanged,
}: {
  issues: ViewIssue[];
  projects: HubProject[];
  epics: HubEpic[];
  singleProject: boolean;
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
  onToggleAll: () => void;
  /** US-87.12: ids whose rows just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  // US-71.1: epics are the working unit, so the table opens grouped by them.
  const [groupBy, setGroupBy] = useState<GroupBy>("epic");
  const [sort, setSort] = useState<SortKey>("updated");

  const allSelected =
    issues.length > 0 && issues.every((i) => selected.has(i.id));
  const someSelected = issues.some((i) => selected.has(i.id));

  const showProjectColumn = groupBy !== "project" && !singleProject;
  const projectName = useMemo(() => {
    const m = new Map(projects.map((p) => [p.id, p.name]));
    return (id: string) => m.get(id) ?? "";
  }, [projects]);
  const epicById = useMemo(
    () => new Map(epics.map((e) => [e.id, e])),
    [epics]
  );

  const cmp = useMemo(() => {
    return (a: ViewIssue, b: ViewIssue) => {
      if (sort === "title") return a.title.localeCompare(b.title);
      if (sort === "status") return statusIndex(a.status) - statusIndex(b.status);
      return b.updated_at.localeCompare(a.updated_at); // updated: newest first
    };
  }, [sort]);

  // Order a group's items: top-level sorted by the active sort, each feature
  // immediately followed by its child stories, indented.
  //
  // US-74.1: the children are always in numeric (item_no, sub_no) order, no
  // matter which header sort is active. A feature's stories are a sequence —
  // US-3.1.1 then US-3.1.2 — and reordering them by "recently updated" makes
  // the list read as shuffled. The header sort still orders top-level rows.
  const orderRows = useMemo(() => {
    return (items: ViewIssue[]): { issue: ViewIssue; depth: number }[] => {
      const featureIds = new Set(
        items.filter((i) => i.type === "feature").map((i) => i.id)
      );
      const childrenByParent = new Map<string, ViewIssue[]>();
      for (const i of items) {
        if (i.parent_id && featureIds.has(i.parent_id)) {
          const list = childrenByParent.get(i.parent_id) ?? [];
          list.push(i);
          childrenByParent.set(i.parent_id, list);
        }
      }
      const topLevel = items
        .filter((i) => !(i.parent_id && featureIds.has(i.parent_id)))
        .sort(cmp);
      const out: { issue: ViewIssue; depth: number }[] = [];
      for (const it of topLevel) {
        out.push({ issue: it, depth: 0 });
        if (it.type === "feature") {
          const kids = (childrenByParent.get(it.id) ?? [])
            .slice()
            .sort(compareWorkItemSequence);
          for (const k of kids) out.push({ issue: k, depth: 1 });
        }
      }
      return out;
    };
  }, [cmp]);

  const groups = useMemo(() => {
    if (groupBy === "none") {
      return [{ key: "all", header: null as null | GroupHeader, items: issues }];
    }
    if (groupBy === "project") {
      return projects
        .map((p) => ({
          key: p.id,
          header: {
            color: projectColor(p.id),
            label: p.name,
          } as GroupHeader,
          items: issues.filter((i) => i.project_id === p.id),
        }))
        .filter((g) => g.items.length > 0);
    }
    if (groupBy === "status") {
      const present = Array.from(new Set(issues.map((i) => i.status))).sort(
        (a, b) => statusIndex(a) - statusIndex(b)
      );
      return present.map((s) => ({
        key: s,
        header: { status: s } as GroupHeader,
        items: issues.filter((i) => i.status === s),
      }));
    }
    // epic — ordered by project order then epic number descending (US-71.1:
    // latest first, matching the Outline), then a No-epic bucket.
    const projOrder = new Map(projects.map((p, idx) => [p.id, idx]));
    const orderedEpics = [...epics].sort(
      (a, b) =>
        (projOrder.get(a.project_id) ?? 999) -
          (projOrder.get(b.project_id) ?? 999) || b.number - a.number
    );
    const out = orderedEpics
      .map((e) => ({
        key: e.id,
        header: {
          color: projectColor(e.project_id),
          label: epicLabel(e.number, e.title),
          sub: singleProject ? undefined : projectName(e.project_id),
        } as GroupHeader,
        items: issues.filter((i) => i.epic_id === e.id),
      }))
      .filter((g) => g.items.length > 0);
    const noEpic = issues.filter((i) => !i.epic_id);
    if (noEpic.length) {
      out.push({
        key: "__none__",
        header: { label: "No epic" } as GroupHeader,
        items: noEpic,
      });
    }
    return out;
  }, [groupBy, issues, projects, epics, singleProject, projectName]);

  const colCount = 7 + (showProjectColumn ? 1 : 0); // checkbox..updated

  if (!issues.length) {
    return (
      <p className="rounded-lg border border-dashed px-4 py-8 text-center text-sm text-muted-foreground">
        No work items to show.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Group by</span>
        <DropdownMenu>
          <DropdownMenuTrigger className="flex items-center gap-1 rounded-md border border-input px-2 py-1 font-medium text-foreground hover:bg-muted">
            {GROUP_LABELS[groupBy]}
            <ChevronDown className="size-3" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-36">
            <DropdownMenuRadioGroup
              value={groupBy}
              onValueChange={(v) => setGroupBy(v as GroupBy)}
            >
              {(Object.keys(GROUP_LABELS) as GroupBy[]).map((g) => (
                <DropdownMenuRadioItem key={g} value={g}>
                  {GROUP_LABELS[g]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full min-w-[52rem] border-collapse text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-left text-xs text-muted-foreground">
              <th className="w-8 px-3 py-2">
                <Checkbox
                  checked={allSelected}
                  indeterminate={someSelected && !allSelected}
                  onCheckedChange={onToggleAll}
                  aria-label="Select all visible work items"
                />
              </th>
              <th className="px-3 py-2 font-medium">ID</th>
              <SortableTh
                label="Title"
                active={sort === "title"}
                onClick={() => setSort("title")}
              />
              <th className="px-3 py-2 font-medium">Type</th>
              <SortableTh
                label="Status"
                active={sort === "status"}
                onClick={() => setSort("status")}
              />
              <th className="px-3 py-2 font-medium">Epic</th>
              {showProjectColumn && (
                <th className="px-3 py-2 font-medium">Project</th>
              )}
              {/* US-91.14: what it cost. */}
              <th
                className="px-3 py-2 text-right font-medium"
                title="Everything this item has cost, across every run against it — failed, cancelled and superseded attempts included. Not the price of the merge."
              >
                Cost
              </th>
              <SortableTh
                label="Updated"
                active={sort === "updated"}
                onClick={() => setSort("updated")}
              />
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <GroupBlock
                key={group.key}
                header={group.header}
                colCount={colCount}
                rows={orderRows(group.items)}
                showProjectColumn={showProjectColumn}
                projectName={projectName}
                epicById={epicById}
                selected={selected}
                onToggle={onToggle}
                recentlyChanged={recentlyChanged}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

type GroupHeader = {
  color?: string;
  label?: string;
  sub?: string;
  status?: string;
};

function SortableTh({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <th className="px-3 py-2 font-medium">
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex items-center gap-1 hover:text-foreground",
          active && "text-foreground"
        )}
      >
        {label}
        {active &&
          (label === "Updated" ? (
            <ArrowDown className="size-3" />
          ) : (
            <ArrowUp className="size-3" />
          ))}
      </button>
    </th>
  );
}

function GroupBlock({
  header,
  colCount,
  rows,
  showProjectColumn,
  projectName,
  epicById,
  selected,
  onToggle,
  recentlyChanged,
}: {
  header: GroupHeader | null;
  colCount: number;
  rows: { issue: ViewIssue; depth: number }[];
  showProjectColumn: boolean;
  projectName: (id: string) => string;
  epicById: Map<string, HubEpic>;
  selected: ReadonlySet<string>;
  onToggle: (id: string) => void;
  /** US-87.12: ids whose rows just changed from a live update. */
  recentlyChanged?: ReadonlySet<string>;
}) {
  return (
    <>
      {header && (
        <tr className="border-b bg-muted/20">
          <td colSpan={colCount} className="px-3 py-1.5">
            <div className="flex items-center gap-2">
              {header.color && (
                <span
                  className="h-3.5 w-1 rounded-full"
                  style={{ backgroundColor: header.color }}
                />
              )}
              {header.status ? (
                <StatusBadge status={header.status as IssueStatus} />
              ) : (
                <span className="text-xs font-semibold">{header.label}</span>
              )}
              {header.sub && (
                <span className="text-xs text-muted-foreground">
                  · {header.sub}
                </span>
              )}
              <span className="text-xs tabular-nums text-muted-foreground">
                {rows.length}
              </span>
            </div>
          </td>
        </tr>
      )}
      {rows.map(({ issue: i, depth }) => {
        const epic = i.epic_id ? epicById.get(i.epic_id) : undefined;
        const displayId = workItemDisplayId({
          type: i.type,
          epicNumber: i.epic_number ?? epic?.number ?? null,
          itemNo: i.item_no,
          subNo: i.sub_no,
        });
        return (
          // US-87.4: skip layout/paint for off-screen rows (see globals.css).
          <tr
            key={i.id}
            data-changed={recentlyChanged?.has(i.id) ? "" : undefined}
            className="list-row border-b last:border-0 hover:bg-muted/30"
          >
            <td className="px-3 py-2 align-middle">
              <Checkbox
                checked={selected.has(i.id)}
                onCheckedChange={() => onToggle(i.id)}
                aria-label={`Select ${i.title}`}
              />
            </td>
            <td className="px-3 py-2 align-middle font-mono text-xs text-muted-foreground">
              {displayId ?? ""}
            </td>
            <td className="px-3 py-2 align-middle">
              <Link
                href={`/issues/${i.id}?from=work-items`}
                className="font-medium hover:underline"
                style={depth ? { paddingLeft: depth * 16 } : undefined}
              >
                {i.title}
              </Link>
            </td>
            <td className="px-3 py-2 align-middle">
              <TypeBadge type={i.type as IssueType} />
            </td>
            <td className="px-3 py-2 align-middle">
              <StatusBadge status={i.status as IssueStatus} />
            </td>
            <td className="max-w-[10rem] truncate px-3 py-2 align-middle text-muted-foreground">
              {epic
                ? epicLabel(epic.number, epic.title)
                : i.epic_number != null
                  ? epicLabel(i.epic_number, i.epic_title)
                  : (i.epic_title ?? "")}
            </td>
            {showProjectColumn && (
              <td className="max-w-[10rem] truncate px-3 py-2 align-middle text-muted-foreground">
                {projectName(i.project_id)}
              </td>
            )}
            <td
              className="whitespace-nowrap px-3 py-2 text-right align-middle font-mono text-xs tabular-nums text-muted-foreground"
              title="Everything this item has cost, across every run against it — failed, cancelled and superseded attempts included. Not the price of the merge."
            >
              {/* Null is "nothing has run yet"; a real zero is "$0.00". They
                  are different facts and must look different. */}
              {i.cost_usd == null ? "—" : money(Number(i.cost_usd))}
            </td>
            <td className="whitespace-nowrap px-3 py-2 align-middle text-xs text-muted-foreground">
              {formatIssueWhen(i.updated_at)}
            </td>
          </tr>
        );
      })}
    </>
  );
}
