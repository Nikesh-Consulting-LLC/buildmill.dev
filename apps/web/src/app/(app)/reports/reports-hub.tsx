"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Bug,
  ChevronRight,
  ClipboardCopy,
  Inbox,
  MessageSquare,
  PlugZap,
  RotateCcw,
  ShieldCheck,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { projectColor } from "@/lib/work-items";
import { cn } from "@/lib/utils";
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
import { EmptyState } from "@/components/empty-state";
import { toastError, toastSuccess } from "@/components/ui/toast";
import type { HubProject } from "../issues/issue-view-types";
import { PromoteDialog, type PromoteEpic } from "./promote-dialog";
import {
  asErrorText,
  asMarkdown,
  copyText,
  isWiringCheck,
  kind,
  origin,
  when,
} from "./report-format";
import {
  OPEN_STATUSES,
  type ReportDeployment,
  type ReportRow,
} from "./report-types";

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "new"
      ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200"
      : status === "promoted"
        ? "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200"
        : status === "fixed"
          ? "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200"
          : "bg-muted text-muted-foreground";
  return (
    <span
      className={cn(
        "inline-block rounded-full px-2 py-0.5 text-xs font-medium",
        tone,
      )}
    >
      {status}
    </span>
  );
}

/**
 * US-16.6: every issue any deployed app has reported, across every project, in
 * one table — because the whole point is triaging without walking each
 * project's deployments one at a time.
 *
 * Automated crashes and user submissions share one list on purpose: to a
 * manager deciding what to fix, "the app broke" and "a user says the app
 * broke" are the same question with different evidence. The source column
 * keeps them tellable apart without splitting the queue in two.
 */
export function ReportsHub({
  projects,
  deployments,
  epics,
  initialReports,
  initialActiveId = null,
  selectedIds,
}: {
  projects: HubProject[];
  deployments: ReportDeployment[];
  epics: PromoteEpic[];
  initialReports: ReportRow[];
  /** US-16.7: `?report=<id>` from a promoted work item's back-link. */
  initialActiveId?: string | null;
  /** Phase 64: resolved server-side from the global project filter. */
  selectedIds: string[];
}) {
  const [reports, setReports] = useState<ReportRow[]>(initialReports);
  const selectedProjects = useMemo(() => new Set(selectedIds), [selectedIds]);

  // A deep-linked report is usually already decided, so the filter that hides
  // decided reports has to start off — otherwise the link lands on a table the
  // report is not in.
  const deepLinked = initialActiveId
    ? (initialReports.find((r) => r.id === initialActiveId) ?? null)
    : null;
  const [showClosed, setShowClosed] = useState(
    !!deepLinked && !OPEN_STATUSES.includes(deepLinked.status),
  );
  const [source, setSource] = useState<"all" | "automated" | "user_report">("all");
  // A set, not a single id: comparing two reports should not mean losing the
  // first one you opened.
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    deepLinked ? new Set([deepLinked.id]) : new Set(),
  );
  const [busy, setBusy] = useState<string | null>(null);

  // US-16.6: a report that arrives while the manager is looking should appear.
  useEffect(() => {
    const supabase = createClient();
    const channel = supabase
      .channel("app-issues-hub")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "app_issues" },
        (payload) => {
          const row = payload.new as ReportRow | undefined;
          if (!row?.id) return;
          setReports((current) => {
            const rest = current.filter((r) => r.id !== row.id);
            return [row, ...rest].sort((a, b) =>
              b.last_seen_at.localeCompare(a.last_seen_at),
            );
          });
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  /** US-92.5: the expanded detail, shared by the table row and the phone
   *  card — two copies of this would drift, and it is the substance of a
   *  report. */
  const detailFor = (report: ReportRow) => {
    const automated = report.source === "automated";
    const project = projects.find((p) => p.id === report.project_id);
    const deployment = deployments.find((d) => d.id === report.deployment_id);
    const decided = !OPEN_STATUSES.includes(report.status);
    void project;
    void decided;
    return (
                <div className="flex flex-col gap-3">
                  <div className="flex flex-wrap gap-1.5 text-xs">
                    <Badge variant="outline">
                      {automated ? "Crash" : "User report"}
                    </Badge>
                    {/* Where it happened moved here off the row: it is a
                        URL, and a URL never truncates usefully. */}
                    <Badge variant="outline" className="font-mono">
                      {origin(report)}
                    </Badge>
                    {automated && (
                      <Badge variant="outline">
                        {report.occurrence_count} occurrences
                      </Badge>
                    )}
                    <Badge variant="outline">
                      first seen {when(report.first_seen_at)}
                    </Badge>
                    <Badge variant="outline">
                      last seen {when(report.last_seen_at)}
                    </Badge>
                    {deployment?.environment && (
                      <Badge variant="outline">{deployment.environment}</Badge>
                    )}
                  </div>

                  {(report.reporter_name || report.reporter_email) && (
                    <p className="text-sm text-muted-foreground">
                      Reported by {report.reporter_name ?? "someone"}
                      {report.reporter_email
                        ? ` · ${report.reporter_email}`
                        : ""}
                    </p>
                  )}

                  {report.message && (
                    <p className="text-sm whitespace-pre-wrap">
                      {report.message}
                    </p>
                  )}

                  {report.stack_trace && (
                    <pre className="max-h-80 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
                      {report.stack_trace}
                    </pre>
                  )}

                  {report.context &&
                    Object.keys(report.context).length > 0 && (
                      <pre className="max-h-56 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
                        {JSON.stringify(report.context, null, 2)}
                      </pre>
                    )}

                  <div className="flex flex-wrap gap-2">
                    {/* US-79.1: promotion is not offered on the wiring
                        check — it is the pipe's own test ping. */}
                    {isWiringCheck(report) && !report.promoted_issue_id ? (
                      <span className="self-center text-xs text-muted-foreground">
                        This is the self-monitoring wiring check, not a
                        defect — there is nothing to promote.
                      </span>
                    ) : report.promoted_issue_id ? (
                      <Button
                        size="sm"
                        render={
                          <Link
                            href={`/issues/${report.promoted_issue_id}?from=${encodeURIComponent("/reports")}&fromLabel=${encodeURIComponent("Reports")}`}
                          />
                        }
                      >
                        Open the work item this became
                      </Button>
                    ) : (
                      <PromoteDialog
                        report={report}
                        epics={epics}
                        onPromoted={(issueId) =>
                          setReports((current) =>
                            current.map((r) =>
                              r.id === report.id
                                ? {
                                    ...r,
                                    status: "promoted",
                                    promoted_issue_id: issueId,
                                  }
                                : r,
                            ),
                          )
                        }
                      />
                    )}
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        copyText(asMarkdown(report), "Details")
                      }
                    >
                      <ClipboardCopy className="mr-1 size-4" /> Copy details
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        copyText(
                          JSON.stringify(report.context ?? {}, null, 2),
                          "Context",
                        )
                      }
                    >
                      <ClipboardCopy className="mr-1 size-4" /> Copy context
                    </Button>
                    {report.status === "ignored" ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy === report.id}
                        onClick={() => setStatus(report, "new")}
                      >
                        <RotateCcw className="mr-1 size-4" /> Reopen
                      </Button>
                    ) : (
                      !decided && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy === report.id}
                          onClick={() => setStatus(report, "ignored")}
                        >
                          Ignore
                        </Button>
                      )
                    )}
                  </div>
                </div>
    );
  };

  const visible = useMemo(
    () =>
      reports
        .filter(
          (r) =>
            selectedProjects.has(r.project_id) &&
            (showClosed || OPEN_STATUSES.includes(r.status)) &&
            (source === "all" || r.source === source),
        )
        .sort((a, b) => b.last_seen_at.localeCompare(a.last_seen_at)),
    [reports, selectedProjects, showClosed, source],
  );

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function setStatus(report: ReportRow, status: string) {
    setBusy(report.id);
    const { error } = await createClient()
      .from("app_issues")
      .update({
        status,
        triaged_at: OPEN_STATUSES.includes(status) ? null : new Date().toISOString(),
      })
      .eq("id", report.id);
    setBusy(null);
    if (error) {
      toastError(error.message);
      return;
    }
    setReports((current) =>
      current.map((r) => (r.id === report.id ? { ...r, status } : r)),
    );
    toastSuccess(status === "new" ? "Reopened" : `Moved to ${status}`);
  }

  if (!reports.length) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="Nothing has been reported"
        description="No deployed app has reported a crash or a user issue yet. Turn on issue reporting on a deployment to open the door."
      />
    );
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-1 rounded-full border border-input p-0.5 text-sm">
          {(
            [
              ["all", "All"],
              ["automated", "Crashes"],
              ["user_report", "User reports"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setSource(value)}
              className={cn(
                "rounded-full px-3 py-1 transition-colors",
                source === value ? "bg-muted font-medium" : "text-muted-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={showClosed}
            onChange={(e) => setShowClosed(e.target.checked)}
          />
          Show decided
        </label>
        <span className="ml-auto text-sm text-muted-foreground">
          {visible.length} shown
        </span>
      </div>

      {/* US-35.7/35.8 + the manager's own note: the table FITS the width rather
          than scrolling inside it. `table-fixed` with sized side columns lets
          the one variable-length column — the report itself — take whatever is
          left and truncate. The detail extract that used to sit here made the
          table wider than the screen for no gain: it repeats the first line of
          the message, which the expansion shows in full. */}
      {/* US-92.5: cards below `md`. Triage happens on a phone, when a
          notification lands — so the report's own words lead, and the project
          comes back. */}
      <div className="grid gap-2 md:hidden">
        {visible.map((report) => {
          const isOpen = expanded.has(report.id);
          const project = projects.find((p) => p.id === report.project_id);
          const deployment = deployments.find(
            (d) => d.id === report.deployment_id,
          );
          const automated = report.source === "automated";
          const decided = !OPEN_STATUSES.includes(report.status);
          return (
            <div key={`m-${report.id}`} className="grid gap-2 rounded-lg border p-3">
              <button
                type="button"
                onClick={() => toggle(report.id)}
                className="grid gap-1.5 text-left"
              >
                <span className="flex min-w-0 items-start gap-2">
                  {/* US-79.1: a wiring check stays a confirmation, never the
                      crash treatment — in the card as in the table. */}
                  {isWiringCheck(report) ? (
                    <PlugZap className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  ) : automated ? (
                    <Bug className="mt-0.5 size-4 shrink-0 text-destructive" />
                  ) : (
                    <MessageSquare className="mt-0.5 size-4 shrink-0 text-blue-600" />
                  )}
                  <span className="min-w-0 flex-1 text-sm font-medium">
                    {report.title}
                  </span>
                  <ChevronRight
                    className={cn(
                      "mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform",
                      isOpen && "rotate-90",
                    )}
                  />
                </span>

                {/* AC2/AC3: which app reported it, and the rest as words. */}
                <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <span
                      className="size-2.5 shrink-0 rounded-full"
                      style={{ backgroundColor: projectColor(report.project_id) }}
                    />
                    <span className="truncate">{project?.name ?? "Unknown"}</span>
                    {deployment?.name && (
                      <span className="truncate">· {deployment.name}</span>
                    )}
                  </span>
                  <span>
                    · {when(automated ? report.last_seen_at : report.created_at)}
                  </span>
                  {automated && <span>· seen {report.occurrence_count}×</span>}
                </span>

                <span className="flex flex-wrap items-center gap-1.5">
                  <StatusPill status={report.status} />
                  {isWiringCheck(report) && (
                    <Badge variant="outline">wiring check</Badge>
                  )}
                  {kind(report) && <Badge variant="outline">{kind(report)}</Badge>}
                </span>
              </button>

              {/* AC4: the actions are buttons on the card, at a tap size. */}
              <div className="flex flex-wrap gap-2 border-t pt-2 [&_button]:h-10 [&_button]:flex-1">
                <Button
                  variant="outline"
                  aria-label="Copy the report"
                  onClick={() => copyText(asErrorText(report), "Report")}
                >
                  <ClipboardCopy className="size-4" />
                  Copy
                </Button>
                {report.status === "ignored" ? (
                  <Button
                    variant="outline"
                    disabled={busy === report.id}
                    onClick={() => setStatus(report, "new")}
                  >
                    <RotateCcw className="size-4" />
                    Reopen
                  </Button>
                ) : (
                  !decided && (
                    <Button
                      variant="outline"
                      disabled={busy === report.id}
                      onClick={() => setStatus(report, "ignored")}
                    >
                      Ignore
                    </Button>
                  )
                )}
              </div>

              {isOpen && (
                <div className="border-t pt-2">{detailFor(report)}</div>
              )}
            </div>
          );
        })}
      </div>

      <div className="hidden w-full overflow-hidden rounded-lg border md:block">
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead className="w-[7.5rem] whitespace-nowrap">Last seen</TableHead>
              <TableHead className="hidden w-[13rem] md:table-cell">Project</TableHead>
              <TableHead>Report</TableHead>
              <TableHead className="w-16 whitespace-nowrap text-right">Count</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-20 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((report) => {
              const isOpen = expanded.has(report.id);
              const project = projects.find((p) => p.id === report.project_id);
              const deployment = deployments.find(
                (d) => d.id === report.deployment_id,
              );
              const automated = report.source === "automated";
              const decided = !OPEN_STATUSES.includes(report.status);
              return [
                <TableRow
                  key={report.id}
                  onClick={() => toggle(report.id)}
                  className={cn("cursor-pointer", isOpen && "bg-muted/50")}
                >
                  <TableCell className="pr-0">
                    <ChevronRight
                      className={cn(
                        "size-4 text-muted-foreground transition-transform",
                        isOpen && "rotate-90",
                      )}
                    />
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {when(automated ? report.last_seen_at : report.created_at)}
                  </TableCell>
                  <TableCell className="hidden md:table-cell">
                    <span className="flex min-w-0 items-center gap-2">
                      <span
                        className="size-2.5 shrink-0 rounded-full"
                        style={{ backgroundColor: projectColor(report.project_id) }}
                      />
                      <span className="truncate text-sm">
                        {project?.name ?? "Unknown"}
                      </span>
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {deployment?.name ?? ""}
                      </span>
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="flex min-w-0 items-center gap-2">
                      {/* US-79.1: a wiring check is a confirmation — neutral,
                          never the crash treatment. */}
                      {isWiringCheck(report) ? (
                        <PlugZap className="size-4 shrink-0 text-muted-foreground" />
                      ) : automated ? (
                        <Bug className="size-4 shrink-0 text-destructive" />
                      ) : (
                        <MessageSquare className="size-4 shrink-0 text-blue-600" />
                      )}
                      <span className="truncate text-sm font-medium">
                        {report.title}
                      </span>
                      {isWiringCheck(report) && (
                        <Badge variant="outline" className="shrink-0">
                          wiring check
                        </Badge>
                      )}
                      {/* US-79.4: connectivity noise reads apart from defects. */}
                      {kind(report) && (
                        <Badge variant="outline" className="shrink-0">
                          {kind(report)}
                        </Badge>
                      )}
                    </span>
                  </TableCell>
                  <TableCell className="text-right text-sm tabular-nums">
                    {automated ? `${report.occurrence_count}×` : "—"}
                  </TableCell>
                  <TableCell>
                    <StatusPill status={report.status} />
                  </TableCell>
                  {/* The row is a toggle, so the actions inside it must not be. */}
                  <TableCell
                    className="text-right"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div className="flex justify-end gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        title="Copy the report"
                        aria-label="Copy the report"
                        onClick={() => copyText(asErrorText(report), "Report")}
                      >
                        <ClipboardCopy className="size-4" />
                      </Button>
                      {report.status === "ignored" ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Reopen"
                          aria-label="Reopen"
                          disabled={busy === report.id}
                          onClick={() => setStatus(report, "new")}
                        >
                          <RotateCcw className="size-4" />
                        </Button>
                      ) : (
                        !decided && (
                          <Button
                            variant="ghost"
                            size="sm"
                            title="Ignore"
                            aria-label="Ignore"
                            disabled={busy === report.id}
                            onClick={() => setStatus(report, "ignored")}
                          >
                            ✕
                          </Button>
                        )
                      )}
                    </div>
                  </TableCell>
                </TableRow>,

                isOpen && (
                  <TableRow
                    key={`${report.id}-detail`}
                    className="bg-muted/30 hover:bg-muted/30"
                  >
                    <TableCell colSpan={7} className="p-4">
                      {detailFor(report)}
                    </TableCell>
                  </TableRow>
                ),
              ];
            })}
          </TableBody>
        </Table>
      </div>

      {!visible.length && (
        <EmptyState
          icon={Inbox}
          title="Nothing to decide"
          description="No report matches these filters. Everything reported has been triaged."
        />
      )}
    </div>
  );
}
