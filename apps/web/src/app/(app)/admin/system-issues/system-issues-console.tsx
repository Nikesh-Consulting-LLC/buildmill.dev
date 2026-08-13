"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Check,
  ChevronRight,
  ClipboardCopy,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Wrench,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
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
import { OPEN_STATUSES, type ReportRow } from "../../reports/report-types";
import {
  asErrorText,
  asMarkdown,
  copyText,
  isWiringCheck,
  kind,
  origin,
  when,
} from "../../reports/report-format";

function CopyButton({
  label,
  icon: Icon,
  value,
  variant = "outline",
}: {
  label: string;
  icon: typeof ClipboardCopy;
  value: () => string;
  variant?: "outline" | "default";
}) {
  return (
    <Button variant={variant} size="sm" onClick={() => copyText(value(), label)}>
      <Icon className="mr-1 size-4" />
      {label}
    </Button>
  );
}

export function SystemIssuesConsole({
  initialReports,
  deploymentsConfigured,
  fixPrompt,
}: {
  initialReports: ReportRow[];
  deploymentsConfigured: boolean;
  /** US-16.9: a prompt_templates row, so its wording is fixable without a
   *  deploy. `{{REPORT}}` is where the report itself goes. */
  fixPrompt: string;
}) {
  const [reports, setReports] = useState(initialReports);
  const [showClosed, setShowClosed] = useState(false);
  const [sort, setSort] = useState<"recent" | "frequent">("recent");
  // A set, not a single id: comparing two errors should not mean losing the
  // first one you opened.
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    const supabase = createClient();
    const channel = supabase
      .channel("system-issues")
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "app_issues" },
        (payload) => {
          const row = payload.new as ReportRow | undefined;
          if (!row?.id) return;
          setReports((current) => {
            // RLS already limits this subscription to self-monitoring rows.
            const rest = current.filter((r) => r.id !== row.id);
            return [row, ...rest];
          });
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, []);

  const visible = useMemo(() => {
    const filtered = reports.filter(
      (r) => showClosed || OPEN_STATUSES.includes(r.status),
    );
    return [...filtered].sort((a, b) =>
      sort === "frequent"
        ? b.occurrence_count - a.occurrence_count
        : b.last_seen_at.localeCompare(a.last_seen_at),
    );
  }, [reports, showClosed, sort]);

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
    toastSuccess(status === "fixed" ? "Marked fixed" : `Moved to ${status}`);
  }

  if (!deploymentsConfigured) {
    return (
      <EmptyState
        icon={Wrench}
        title="Self-monitoring is not wired up yet"
        description="No deployment is flagged as Build Mill itself, so the factory has nowhere to file its own errors. Flag one, enable issue reporting on it, and point the apps at its key."
      />
    );
  }

  if (!reports.length) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="Build Mill has not reported anything"
        description="No unhandled errors have been recorded. That is the good outcome — this page stays empty when the factory is behaving."
      />
    );
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-1 rounded-full border border-input p-0.5 text-sm">
          {(
            [
              ["recent", "Most recent"],
              ["frequent", "Most frequent"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setSort(value)}
              className={cn(
                "rounded-full px-3 py-1 transition-colors",
                sort === value ? "bg-muted font-medium" : "text-muted-foreground",
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
          Show fixed and ignored
        </label>
        <span className="ml-auto text-sm text-muted-foreground">
          {visible.length} shown
        </span>
      </div>

      {/* US-35.7/35.8 + the manager's own note: the table FITS the width rather
          than scrolling inside it. `table-fixed` with sized side columns lets
          the one variable-length column — the error itself — take whatever is
          left and truncate. The detail extract that used to sit here made the
          table wider than the screen for no gain: it repeats the first line of
          the message, which the expansion shows in full. */}
      <div className="w-full overflow-hidden rounded-lg border">
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead className="w-[7.5rem] whitespace-nowrap">Last seen</TableHead>
              <TableHead className="hidden w-[16rem] md:table-cell">Path</TableHead>
              <TableHead>Error</TableHead>
              <TableHead className="w-16 whitespace-nowrap text-right">Count</TableHead>
              <TableHead className="w-24">Status</TableHead>
              <TableHead className="w-20 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((report) => {
              const isOpen = expanded.has(report.id);
              const closed = !OPEN_STATUSES.includes(report.status);
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
                    {when(report.last_seen_at)}
                  </TableCell>
                  <TableCell className="hidden truncate font-mono text-xs text-muted-foreground md:table-cell">
                    {origin(report)}
                  </TableCell>
                  <TableCell className="truncate text-sm font-medium">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate">{report.title}</span>
                      {/* US-79.1: the wiring check reads as a confirmation. */}
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
                    {report.occurrence_count}×
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{report.status}</Badge>
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
                        title="Copy the error message"
                        aria-label="Copy the error message"
                        onClick={() => copyText(asErrorText(report), "Error")}
                      >
                        <ClipboardCopy className="size-4" />
                      </Button>
                      {closed ? (
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
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Mark fixed"
                          aria-label="Mark fixed"
                          disabled={busy === report.id}
                          onClick={() => setStatus(report, "fixed")}
                        >
                          <Check className="size-4 text-green-600" />
                        </Button>
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
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap gap-1.5 text-xs">
                          <Badge variant="outline">
                            {report.source === "automated" ? "Crash" : "User report"}
                          </Badge>
                          {/* The full path lives here as well as in the row —
                              the row truncates it, and a truncated path is
                              exactly the thing you came to read. */}
                          <Badge variant="outline" className="font-mono">
                            {origin(report)}
                          </Badge>
                          <Badge variant="outline">
                            first seen {when(report.first_seen_at)}
                          </Badge>
                          <Badge variant="outline">
                            last seen {when(report.last_seen_at)}
                          </Badge>
                          {report.fingerprint && (
                            <Badge variant="outline" className="font-mono">
                              {report.fingerprint.slice(0, 12)}
                            </Badge>
                          )}
                        </div>

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
                          <CopyButton
                            label="Copy fix prompt"
                            icon={Sparkles}
                            variant="default"
                            value={() =>
                              fixPrompt.includes("{{REPORT}}")
                                ? fixPrompt.replace("{{REPORT}}", asMarkdown(report))
                                : `${fixPrompt}\n\n${asMarkdown(report)}`
                            }
                          />
                          <CopyButton
                            label="Copy details"
                            icon={ClipboardCopy}
                            value={() => asMarkdown(report)}
                          />
                          <CopyButton
                            label="Copy context"
                            icon={ClipboardCopy}
                            value={() =>
                              JSON.stringify(report.context ?? {}, null, 2)
                            }
                          />
                          {closed ? (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy === report.id}
                              onClick={() => setStatus(report, "new")}
                            >
                              <RotateCcw className="mr-1 size-4" /> Reopen
                            </Button>
                          ) : (
                            <>
                              <Button
                                size="sm"
                                disabled={busy === report.id}
                                onClick={() => setStatus(report, "fixed")}
                              >
                                <Check className="mr-1 size-4" /> Mark fixed
                              </Button>
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={busy === report.id}
                                onClick={() => setStatus(report, "ignored")}
                              >
                                Ignore
                              </Button>
                            </>
                          )}
                          {report.promoted_issue_id && (
                            <Button
                              variant="outline"
                              size="sm"
                              render={
                                <Link
                                  href={`/issues/${report.promoted_issue_id}?from=${encodeURIComponent("/admin/system-issues")}&fromLabel=${encodeURIComponent("System Issues")}`}
                                />
                              }
                            >
                              Open the work item
                            </Button>
                          )}
                        </div>

                        <p className="text-xs text-muted-foreground">
                          Marking it fixed closes it. If the same crash happens
                          again it opens a new report rather than reviving this
                          one — a regression is a new bug.
                        </p>
                      </div>
                    </TableCell>
                  </TableRow>
                ),
              ];
            })}
          </TableBody>
        </Table>
      </div>

      {!visible.length && (
        <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
          Nothing open. Tick “Show fixed and ignored” to see what has been dealt
          with.
        </p>
      )}
    </div>
  );
}
