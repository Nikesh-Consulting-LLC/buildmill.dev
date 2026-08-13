"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  Bot,
  Check,
  ChevronRight,
  ClipboardCopy,
  RotateCcw,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import type { Database, Json } from "@/lib/supabase/database.types";
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
import { copyText, when } from "../../reports/report-format";

export type FailureRow =
  Database["public"]["Functions"]["list_agent_failures"]["Returns"][number];

/** What each category means, in the words the manager needs. */
const CATEGORY_LABELS: Record<string, string> = {
  "run-failed": "Run failed",
  "lease-expired": "Died holding claim",
  "heartbeat-stale": "Stopped reporting",
};

function categoryLabel(category: string) {
  return CATEGORY_LABELS[category] ?? category;
}

function agentLine(f: FailureRow) {
  const parts = [f.worker_name || "unknown agent"];
  if (f.worker_type) parts.push(f.worker_type);
  if (f.preset_name)
    parts.push(`${f.preset_name}${f.preset_version ? ` v${f.preset_version}` : ""}`);
  return parts.join(" · ");
}

/** The failure as Markdown — AC6's "one block ready to paste into an LLM". */
function asMarkdown(f: FailureRow, context: Json | null | undefined): string {
  const lines = [
    `## Agent failure: ${categoryLabel(f.category)}`,
    "",
    `- **When:** ${when(f.created_at)}`,
    `- **Workspace:** ${f.org_name || f.org_id}`,
    `- **Project:** ${f.project_name || "—"}`,
    `- **Work item:** ${f.issue_title ? `${f.issue_title} (${f.issue_type ?? "item"})` : "none (issue-less run)"}`,
    `- **Run:** \`${f.run_id ?? "—"}\` (${f.kind})`,
    `- **Agent:** ${agentLine(f)}`,
    `- **Category:** ${f.category}${f.resumable ? " (parked for its worker to resume)" : ""}`,
  ];
  if (f.error) lines.push("", "### Error", "", f.error);
  const detail = (f.detail ?? {}) as Record<string, unknown>;
  if (Object.keys(detail).length)
    lines.push("", "### Detail", "", "```json", JSON.stringify(detail, null, 2), "```");
  if (context !== undefined) {
    if (context === null) {
      lines.push("", "### Instructions", "", "_The run no longer exists; its instruction bundle is gone._");
    } else {
      lines.push("", "### Instructions the run carried", "");
      for (const [key, value] of Object.entries(
        (typeof context === "object" && context !== null && !Array.isArray(context)
          ? context
          : { input_context: context }) as Record<string, unknown>,
      )) {
        lines.push(`#### ${key}`, "");
        if (typeof value === "string") lines.push(value, "");
        else lines.push("```json", JSON.stringify(value, null, 2), "```", "");
      }
    }
  }
  return lines.join("\n");
}

/** AC5: the instruction bundle rendered readably — string sections as prose,
 *  everything else as JSON — never one undifferentiated blob. */
function ContextSections({ context }: { context: Json }) {
  if (typeof context !== "object" || context === null || Array.isArray(context)) {
    return (
      <pre className="max-h-80 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
        {JSON.stringify(context, null, 2)}
      </pre>
    );
  }
  const entries = Object.entries(context as Record<string, unknown>);
  if (!entries.length)
    return (
      <p className="text-xs text-muted-foreground">
        The run carried an empty instruction bundle.
      </p>
    );
  return (
    <div className="flex flex-col gap-2">
      {entries.map(([key, value]) => (
        <details key={key} className="rounded-md border bg-background">
          <summary className="cursor-pointer px-3 py-2 font-mono text-xs font-medium">
            {key}
          </summary>
          <pre className="max-h-80 overflow-auto border-t p-3 font-mono text-xs whitespace-pre-wrap">
            {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
          </pre>
        </details>
      ))}
    </div>
  );
}

export function AgentFailuresConsole({
  initialFailures,
}: {
  initialFailures: FailureRow[];
}) {
  const [failures, setFailures] = useState(initialFailures);
  const [showReviewed, setShowReviewed] = useState(false);
  // A set, not a single id: comparing two failures should not mean losing
  // the first one you opened (same call the System issues console made).
  const [expanded, setExpanded] = useState<Set<number>>(() => new Set());
  const [busy, setBusy] = useState<number | null>(null);
  // Instruction bundles, fetched on first expand. `null` = run is gone,
  // "loading" = in flight; absent = not asked yet.
  const [contexts, setContexts] = useState<
    Record<number, Json | null | "loading">
  >({});

  const visible = useMemo(
    () => failures.filter((f) => showReviewed || f.status === "new"),
    [failures, showReviewed],
  );

  async function loadContext(f: FailureRow): Promise<Json | null> {
    if (!f.run_exists) return null;
    const cached = contexts[f.id];
    if (cached !== undefined && cached !== "loading") return cached;
    setContexts((c) => ({ ...c, [f.id]: "loading" }));
    const { data, error } = await createClient().rpc(
      "agent_failure_run_context",
      { p_failure: f.id },
    );
    if (error) {
      toastError(error.message);
      setContexts((c) => {
        const next = { ...c };
        delete next[f.id];
        return next;
      });
      return null;
    }
    setContexts((c) => ({ ...c, [f.id]: data ?? null }));
    return data ?? null;
  }

  function toggle(f: FailureRow) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(f.id)) next.delete(f.id);
      else next.add(f.id);
      return next;
    });
    if (!expanded.has(f.id)) void loadContext(f);
  }

  async function setStatus(f: FailureRow, status: "new" | "reviewed") {
    setBusy(f.id);
    const { error } = await createClient()
      .from("agent_failures")
      .update({ status })
      .eq("id", f.id);
    setBusy(null);
    if (error) {
      toastError(error.message);
      return;
    }
    setFailures((current) =>
      current.map((row) => (row.id === f.id ? { ...row, status } : row)),
    );
    toastSuccess(status === "reviewed" ? "Marked reviewed" : "Reopened");
  }

  async function copyDetails(f: FailureRow) {
    // The whole point of the copy is handing an LLM everything at once, so
    // the instruction bundle is fetched before the markdown is assembled.
    const context = f.run_exists ? await loadContext(f) : null;
    await copyText(asMarkdown(f, context), "Failure details");
  }

  if (!failures.length) {
    return (
      <EmptyState
        icon={Bot}
        title="No agent failures recorded"
        description="No agent has failed a run, died holding a claim, or gone silent since this log began. This page stays empty when the fleet is behaving."
      />
    );
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="flex cursor-pointer items-center gap-2 text-sm text-muted-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={showReviewed}
            onChange={(e) => setShowReviewed(e.target.checked)}
          />
          Show reviewed
        </label>
        <span className="ml-auto text-sm text-muted-foreground">
          {visible.length} shown
        </span>
      </div>

      {/* Same width discipline the System issues table settled on: the table
          FITS the width, sized side columns, and the one variable-length
          column — the error — takes whatever is left and truncates. */}
      <div className="w-full overflow-hidden rounded-lg border">
        <Table className="w-full table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead className="w-[7.5rem] whitespace-nowrap">When</TableHead>
              <TableHead className="hidden w-[11rem] lg:table-cell">
                Workspace
              </TableHead>
              <TableHead className="hidden w-[13rem] xl:table-cell">
                Work item
              </TableHead>
              <TableHead className="w-[4.5rem]">Kind</TableHead>
              <TableHead className="hidden w-[9rem] md:table-cell">Agent</TableHead>
              <TableHead className="w-[9.5rem]">Failure</TableHead>
              <TableHead>Error</TableHead>
              <TableHead className="w-20 text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((f) => {
              const isOpen = expanded.has(f.id);
              const reviewed = f.status === "reviewed";
              const context = contexts[f.id];
              return [
                <TableRow
                  key={f.id}
                  onClick={() => toggle(f)}
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
                    {when(f.created_at)}
                  </TableCell>
                  <TableCell className="hidden truncate text-xs text-muted-foreground lg:table-cell">
                    {f.org_name || "—"}
                    {f.project_name ? ` · ${f.project_name}` : ""}
                  </TableCell>
                  <TableCell className="hidden truncate text-xs xl:table-cell">
                    {f.issue_title || (
                      <span className="text-muted-foreground">
                        — (no work item)
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="truncate text-xs">{f.kind}</TableCell>
                  <TableCell className="hidden truncate text-xs md:table-cell">
                    {f.worker_name || "—"}
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant="outline"
                      className={cn(
                        "whitespace-nowrap",
                        !reviewed && "border-destructive/50 text-destructive",
                      )}
                    >
                      {categoryLabel(f.category)}
                    </Badge>
                  </TableCell>
                  <TableCell className="truncate text-sm">
                    {f.error || <span className="text-muted-foreground">—</span>}
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
                        title="Copy the failure details"
                        aria-label="Copy the failure details"
                        onClick={() => void copyDetails(f)}
                      >
                        <ClipboardCopy className="size-4" />
                      </Button>
                      {reviewed ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Reopen"
                          aria-label="Reopen"
                          disabled={busy === f.id}
                          onClick={() => setStatus(f, "new")}
                        >
                          <RotateCcw className="size-4" />
                        </Button>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          title="Mark reviewed"
                          aria-label="Mark reviewed"
                          disabled={busy === f.id}
                          onClick={() => setStatus(f, "reviewed")}
                        >
                          <Check className="size-4 text-green-600" />
                        </Button>
                      )}
                    </div>
                  </TableCell>
                </TableRow>,

                isOpen && (
                  <TableRow
                    key={`${f.id}-detail`}
                    className="bg-muted/30 hover:bg-muted/30"
                  >
                    <TableCell colSpan={9} className="p-4">
                      <div className="flex flex-col gap-3">
                        <div className="flex flex-wrap gap-1.5 text-xs">
                          <Badge variant="outline">{categoryLabel(f.category)}</Badge>
                          {f.resumable && (
                            <Badge variant="outline">
                              parked for its worker to resume
                            </Badge>
                          )}
                          <Badge variant="outline">{f.kind} run</Badge>
                          <Badge variant="outline">{agentLine(f)}</Badge>
                          <Badge variant="outline">
                            {f.org_name || f.org_id}
                            {f.project_name ? ` · ${f.project_name}` : ""}
                          </Badge>
                          {f.run_id && (
                            <Badge variant="outline" className="font-mono">
                              run {f.run_id.slice(0, 8)}
                            </Badge>
                          )}
                        </div>

                        {f.error && (
                          <p className="text-sm whitespace-pre-wrap">{f.error}</p>
                        )}

                        {Object.keys((f.detail ?? {}) as Record<string, unknown>)
                          .length > 0 && (
                          <pre className="max-h-56 overflow-auto rounded-md border bg-background p-3 font-mono text-xs whitespace-pre-wrap">
                            {JSON.stringify(f.detail, null, 2)}
                          </pre>
                        )}

                        <div className="flex flex-col gap-1.5">
                          <p className="text-xs font-medium text-muted-foreground">
                            Instructions the run carried
                          </p>
                          {!f.run_exists ? (
                            <p className="text-xs text-muted-foreground">
                              The run this failure came from no longer exists
                              (its work item was deleted). The snapshot above —
                              agent, kind, category, error — is everything that
                              remains.
                            </p>
                          ) : context === undefined || context === "loading" ? (
                            <p className="text-xs text-muted-foreground">
                              Loading the instruction bundle…
                            </p>
                          ) : context === null ? (
                            <p className="text-xs text-muted-foreground">
                              The run carried no instruction bundle.
                            </p>
                          ) : (
                            <ContextSections context={context} />
                          )}
                        </div>

                        <div className="flex flex-wrap gap-2">
                          <Button
                            variant="default"
                            size="sm"
                            onClick={() => void copyDetails(f)}
                          >
                            <ClipboardCopy className="mr-1 size-4" /> Copy details
                          </Button>
                          {reviewed ? (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy === f.id}
                              onClick={() => setStatus(f, "new")}
                            >
                              <RotateCcw className="mr-1 size-4" /> Reopen
                            </Button>
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy === f.id}
                              onClick={() => setStatus(f, "reviewed")}
                            >
                              <Check className="mr-1 size-4" /> Mark reviewed
                            </Button>
                          )}
                          {f.issue_id && (
                            <Button
                              variant="outline"
                              size="sm"
                              render={
                                <Link
                                  href={`/issues/${f.issue_id}?from=${encodeURIComponent("/admin/agent-failures")}&fromLabel=${encodeURIComponent("Agent failures")}`}
                                />
                              }
                            >
                              Open the work item
                            </Button>
                          )}
                        </div>
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
          Nothing new. Tick “Show reviewed” to see what has been dealt with.
        </p>
      )}
    </div>
  );
}
