"use client";

// US-91.2: what is being worked on *right now*, on top of the tab where the
// manager decides whether to send more in. "In the factory" answers a
// different question — what the factory is holding, claimed or not — and
// keeps every row it shows today, including queued and held ones this
// section deliberately omits.
//
// US-91.3: each row carries the roster's CLI-window button, so watching an
// agent does not mean leaving the dashboard to find it in the roster.
// US-91.4: rows group by project, and a project folds away.

import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Bot, ChevronRight, Cog, TerminalSquare } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusBadge } from "@/components/status-badge";
import { TypeBadge, type IssueType } from "@/components/type-badge";
import { EmptyState } from "@/components/empty-state";
import { cn } from "@/lib/utils";
import { RequeueButton } from "./requeue-button";
import { formatMinutes } from "./duration";
import {
  bucketByProject,
  shouldGroup,
  useCollapsedProjects,
  IN_PROGRESS_COLLAPSE_KEY,
} from "./project-groups";
import type { AgentItem, FeatureRunInfo } from "./data";

/** Muted numeric cell — durations and ages line up in a column. */
const NUM = "font-mono text-xs tabular-nums text-muted-foreground";

/** Manager's note (2026-08-14): a quiet visual that an agent is at work. One
 *  slow turn every five seconds, muted — motion as a hum, not a siren. It
 *  stops when the worker has gone silent: this section already refuses to
 *  call silence work (the Heard column ambers), and an animation must not
 *  claim what the data doesn't. Still entirely under `prefers-reduced-motion`. */
function WorkingGear({ turning }: { turning: boolean }) {
  return (
    <Cog
      aria-hidden
      className={cn(
        "size-3.5 shrink-0 text-muted-foreground/60",
        turning && "animate-[spin_5s_linear_infinite] motion-reduce:animate-none"
      )}
    />
  );
}

/**
 * One thing an agent is holding right now.
 *
 * US-91.2 AC3: a feature-owned build is ONE row — the feature, with the run's
 * agent, elapsed and heard on it (us-86.2). Its stories are cargo and do not
 * appear separately; they carry no worker of their own, so the "has a live
 * claim" filter already excludes them.
 */
type LiveRow = {
  /** issue id — the feature's for a build, the story's otherwise. */
  id: string;
  projectId: string;
  project: string;
  type: string;
  displayId: string | null;
  title: string;
  status: string;
  /** Set on a feature-owned build: how many stories it carries. */
  stories: number | null;
  runId: string;
  workerName: string;
  workerPrincipalId: string | null;
  runningMinutes: number;
  silentMinutes: number;
  isSilent: boolean;
};

export function toLiveRows(
  items: AgentItem[],
  featureRuns: Record<string, FeatureRunInfo>
): LiveRow[] {
  const rows: LiveRow[] = [];

  // One row per live feature-owned build, from the stories it carries.
  const seenFeature = new Set<string>();
  for (const i of items) {
    const p = i.parent;
    if (!p) continue;
    const run = featureRuns[p.id];
    if (!run || seenFeature.has(p.id)) continue;
    seenFeature.add(p.id);
    rows.push({
      id: p.id,
      projectId: i.projectId,
      project: i.project,
      type: "feature",
      displayId: p.displayId,
      title: p.title,
      status: "running",
      stories: items.filter((x) => x.parent?.id === p.id).length,
      runId: run.runId,
      workerName: run.workerName,
      workerPrincipalId: run.workerPrincipalId,
      runningMinutes: run.runningMinutes,
      silentMinutes: run.silentMinutes,
      isSilent: run.isSilent,
    });
  }

  // US-91.2 AC2: claimed and running only. No worker holding it is not
  // "in progress" — queued and held rows live on In the factory, which is
  // where a manager goes to ask why nobody has taken something.
  for (const i of items) {
    if (!i.runId || !i.workerName) continue;
    if (i.parent && featureRuns[i.parent.id]) continue; // cargo of a build
    rows.push({
      id: i.id,
      projectId: i.projectId,
      project: i.project,
      type: i.type,
      displayId: i.displayId,
      title: i.title,
      status: i.status,
      stories: null,
      runId: i.runId,
      workerName: i.workerName,
      workerPrincipalId: i.workerPrincipalId ?? null,
      runningMinutes: i.runningMinutes ?? 0,
      silentMinutes: i.silentMinutes ?? 0,
      isSilent: !!i.isSilent,
    });
  }

  return rows;
}

export function InProgressSection({
  items,
  featureRuns,
  interactiveByPrincipal,
}: {
  items: AgentItem[];
  featureRuns: Record<string, FeatureRunInfo>;
  /** US-91.3: principal → runs the `interactive` module, resolved server-side
   *  in one query for the agents actually on screen. */
  interactiveByPrincipal: Record<string, boolean>;
}) {
  const rows = toLiveRows(items, featureRuns);
  const buckets = bucketByProject(rows, (r) => ({
    id: r.projectId,
    name: r.project,
  }));
  const grouped = shouldGroup(buckets);
  const { collapsed, toggle } = useCollapsedProjects(IN_PROGRESS_COLLAPSE_KEY);

  return (
    <section className="grid gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        In Progress ({rows.length})
      </h3>
      {rows.length === 0 ? (
        <EmptyState
          icon={Bot}
          title="Factory's idle"
          description="Dispatch something below."
        />
      ) : (
        <>
        {/* US-92.1: below `md` a table cannot hold six columns at 375px —
            the list beneath this section has had cards since us-68.4, and
            us-91.2 shipped without them. One card per running item. */}
        <div className="grid gap-2 md:hidden">
          {buckets.map((bucket) => {
            const isCollapsed = grouped && collapsed.has(bucket.id);
            return (
              <div key={`m-${bucket.id}`} className="grid gap-2">
                {grouped && (
                  <button
                    type="button"
                    onClick={() => toggle(bucket.id)}
                    aria-expanded={!isCollapsed}
                    className="flex w-full items-center gap-1.5 rounded-md bg-muted/50 px-2 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    <ChevronRight
                      className={cn(
                        "size-3.5 transition-transform",
                        !isCollapsed && "rotate-90"
                      )}
                    />
                    {bucket.name} ({bucket.items.length})
                  </button>
                )}
                {!isCollapsed &&
                  bucket.items.map((r) => (
                    <LiveCard
                      key={`c-${r.runId}`}
                      row={r}
                      hasCli={
                        !!r.workerPrincipalId &&
                        !!interactiveByPrincipal[r.workerPrincipalId]
                      }
                    />
                  ))}
              </div>
            );
          })}
        </div>
        <div className="hidden min-w-0 rounded-lg border md:block">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-full max-w-0">Work item</TableHead>
                <TableHead className="w-40">Stage</TableHead>
                <TableHead className="w-36">Agent</TableHead>
                <TableHead className="hidden w-24 lg:table-cell">
                  Elapsed
                </TableHead>
                <TableHead className="hidden w-28 lg:table-cell">
                  Heard
                </TableHead>
                <TableHead className="w-28" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {buckets.flatMap((bucket) => {
                const isCollapsed = grouped && collapsed.has(bucket.id);
                const header = grouped ? (
                  <TableRow
                    key={`proj-${bucket.id}`}
                    className="hover:bg-transparent"
                  >
                    <TableCell colSpan={6} className="bg-muted/50 py-1.5">
                      <button
                        type="button"
                        onClick={() => toggle(bucket.id)}
                        aria-expanded={!isCollapsed}
                        className="flex w-full items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
                      >
                        <ChevronRight
                          className={cn(
                            "size-3.5 transition-transform",
                            !isCollapsed && "rotate-90"
                          )}
                        />
                        {bucket.name} ({bucket.items.length})
                      </button>
                    </TableCell>
                  </TableRow>
                ) : null;

                const body = isCollapsed
                  ? []
                  : bucket.items.map((r) => (
                      <LiveTableRow
                        key={r.runId}
                        row={r}
                        indent={grouped}
                        hasCli={
                          !!r.workerPrincipalId &&
                          !!interactiveByPrincipal[r.workerPrincipalId]
                        }
                      />
                    ));

                return header ? [header, ...body] : body;
              })}
            </TableBody>
          </Table>
        </div>
        </>
      )}
    </section>
  );
}

/** US-92.1: the phone form of a live row. Same facts, stacked, with the
 *  actions at a real tap size instead of `size-6` icons at the table's edge. */
function LiveCard({ row, hasCli }: { row: LiveRow; hasCli: boolean }) {
  const router = useRouter();
  return (
    <div className="grid gap-2 rounded-lg border p-3">
      <span className="flex min-w-0 flex-col gap-1">
        <span className="flex min-w-0 items-center gap-2">
          <WorkingGear turning={!row.isSilent} />
          <TypeBadge type={row.type as IssueType} />
          {row.displayId && (
            <span className="shrink-0 font-mono text-xs text-muted-foreground">
              {row.displayId}
            </span>
          )}
          {row.stories != null ? (
            <Badge variant="secondary">Building {row.stories}</Badge>
          ) : (
            <StatusBadge status="running" />
          )}
        </span>
        <Link
          href={`/issues/${row.id}?from=workbench`}
          className="font-medium hover:underline"
        >
          {row.title}
        </Link>
        <span className="truncate text-xs text-muted-foreground">
          {row.workerPrincipalId ? (
            <Link
              href={`/team/${row.workerPrincipalId}/runner`}
              className="hover:underline"
            >
              {row.workerName}
            </Link>
          ) : (
            row.workerName
          )}
          {" · "}
          {formatMinutes(row.runningMinutes)} elapsed · heard{" "}
          <span
            className={cn(row.isSilent && "text-amber-700 dark:text-amber-400")}
          >
            {row.silentMinutes === 0
              ? "just now"
              : formatMinutes(row.silentMinutes)}
          </span>
        </span>
      </span>
      {(hasCli || row.isSilent) && (
        <span className="flex flex-wrap items-center gap-2">
          {hasCli && row.workerPrincipalId && (
            <Button
              variant="outline"
              className="h-10 flex-1"
              onClick={() => router.push(`/team/${row.workerPrincipalId}/console`)}
            >
              <TerminalSquare className="size-4" />
              CLI window
            </Button>
          )}
          {row.isSilent && <RequeueButton runId={row.runId} />}
        </span>
      )}
    </div>
  );
}

function LiveTableRow({
  row,
  indent,
  hasCli,
}: {
  row: LiveRow;
  indent: boolean;
  hasCli: boolean;
}) {
  const router = useRouter();
  return (
    <TableRow>
      <TableCell className={cn("w-full max-w-0 min-w-0", indent && "pl-6")}>
        <span className="flex min-w-0 flex-col">
          <span className="flex min-w-0 items-center gap-2">
            <TypeBadge type={row.type as IssueType} />
            {row.displayId && (
              <span className="shrink-0 font-mono text-xs text-muted-foreground">
                {row.displayId}
              </span>
            )}
            <Link
              href={`/issues/${row.id}?from=workbench`}
              className="min-w-0 truncate hover:underline"
            >
              {row.title}
            </Link>
          </span>
          {/* US-35.6: Elapsed and Heard are columns only at `lg`; at tablet
              width they ride here rather than being lost. */}
          <span className="truncate text-xs text-muted-foreground lg:hidden">
            {formatMinutes(row.runningMinutes)} elapsed · heard{" "}
            {row.silentMinutes === 0
              ? "just now"
              : formatMinutes(row.silentMinutes)}
          </span>
        </span>
      </TableCell>
      <TableCell>
        {/* Every row here has a live claim, so the stage is the RUN's truth,
            not the issue's status. Echoing the issue would put a "Queued"
            pill on a row titled In Progress whenever the status write lags
            the claim — the same contradiction us-86.2 removed from the
            factory tab. */}
        <span className="flex items-center gap-1.5">
          <WorkingGear turning={!row.isSilent} />
          {row.stories != null ? (
            <Badge variant="secondary" title="One run, building the whole feature">
              Building {row.stories}
            </Badge>
          ) : (
            <StatusBadge status="running" />
          )}
        </span>
      </TableCell>
      <TableCell className="max-w-52 truncate text-xs">
        {row.workerPrincipalId ? (
          <Link
            href={`/team/${row.workerPrincipalId}/runner`}
            className="truncate hover:underline"
            title={`What ${row.workerName} is doing right now`}
          >
            {row.workerName}
          </Link>
        ) : (
          <span className="truncate">{row.workerName}</span>
        )}
      </TableCell>
      <TableCell className={cn(NUM, "hidden lg:table-cell")}>
        {formatMinutes(row.runningMinutes)}
      </TableCell>
      <TableCell
        className={cn(
          NUM,
          "hidden lg:table-cell",
          row.isSilent && "text-amber-700 dark:text-amber-400"
        )}
        title={
          row.isSilent
            ? "The worker has not spoken for materially longer than its heartbeat cadence — silence is not the same as work."
            : undefined
        }
      >
        {row.silentMinutes === 0
          ? "just now"
          : formatMinutes(row.silentMinutes)}
      </TableCell>
      <TableCell>
        <span className="flex items-center justify-end gap-1">
          {/* US-91.3: the roster's CLI window, on the row that already names
              the agent. Every row here has a live claim, so the button always
              carries the working-now ring — it agrees with the row it sits
              on by construction. */}
          {hasCli && row.workerPrincipalId && (
            <Button
              variant="outline"
              size="sm"
              title="Open CLI window — working now"
              className="border-emerald-500 text-emerald-600 shadow-[0_0_0_2px_rgba(16,185,129,0.25)] dark:text-emerald-400"
              onClick={() =>
                router.push(`/team/${row.workerPrincipalId}/console`)
              }
            >
              <TerminalSquare className="size-4" />
            </Button>
          )}
          {row.isSilent && <RequeueButton runId={row.runId} />}
        </span>
      </TableCell>
    </TableRow>
  );
}
