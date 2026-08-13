"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { Loader2, PauseCircle, X } from "lucide-react";
import { apiCall } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { ParkedRun } from "./data";

const REASON_LABEL: Record<string, string> = {
  turn_limit: "Turn limit — resumes on its own",
  "worker-unresponsive": "Worker went quiet — resumes when it reconnects",
  "manager-approved": "You approved this resume — waiting its turn",
  clarification: "Asked a question",
};

function reasonText(run: ParkedRun): string {
  if (run.status === "stopped") return "Spend ceiling hit — needs your OK to resume";
  return REASON_LABEL[run.reason ?? ""] ?? run.reason ?? "Paused";
}

/** US-59.7: every run parked in a resumable state — split into a "needs your
 * input" tier (awaiting_input; answering happens on the Questions card
 * above, which already owns that flow) and an informational tier (paused,
 * auto-resuming; or a spend-ceiling stopped run waiting on your OK). Manual
 * abandon and resume act through the API so the abandon-vs-in-flight-resume
 * race and the resume-attempt bookkeeping stay server-side. */
export function ParkedRunsCard({ items }: { items: ParkedRun[] }) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!items.length) return null;

  const needsInput = items.filter((r) => r.status === "awaiting_input");
  const informational = items.filter((r) => r.status !== "awaiting_input");

  async function abandon(run: ParkedRun) {
    const reason = window.prompt(
      `Abandon this run? It stays in the work item's history, but stops for good.\n\nWhy (required):`
    );
    if (reason === null) return;
    if (!reason.trim()) {
      setError("A reason is required.");
      return;
    }
    setBusyId(run.id);
    setError(null);
    try {
      await apiCall(`/api/v1/runs/${run.id}/abandon`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function resume(run: ParkedRun) {
    setBusyId(run.id);
    setError(null);
    try {
      await apiCall(`/api/v1/runs/${run.id}/resume`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  function row(run: ParkedRun) {
    return (
      <div
        key={run.id}
        className="flex items-center gap-2 rounded-lg border p-2.5 text-sm"
      >
        <div className="min-w-0 flex-1">
          {run.issueId ? (
            <Link
              href={`/issues/${run.issueId}?from=dashboard`}
              className="block truncate font-medium hover:underline"
            >
              {run.issueTitle}
            </Link>
          ) : (
            <span className="block truncate font-medium">
              {run.issueTitle}
            </span>
          )}
          <p className="truncate text-xs text-muted-foreground">
            {run.project} · {reasonText(run)} · parked {run.age}
            {run.resumeAttempts > 0 && ` · resumed ${run.resumeAttempts}x`}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {run.canResume && (
            <button
              type="button"
              onClick={() => resume(run)}
              disabled={busyId !== null}
              className="rounded-md border px-2 py-1 text-xs font-medium transition-colors hover:bg-muted/60 disabled:opacity-60"
            >
              {busyId === run.id ? (
                <Loader2 className="size-3 animate-spin" />
              ) : (
                "Resume"
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => abandon(run)}
            disabled={busyId !== null}
            aria-label={`Abandon "${run.issueTitle}"`}
            title="Close this out for good"
            className="rounded p-1 text-muted-foreground opacity-70 transition-colors hover:bg-destructive/10 hover:text-destructive hover:opacity-100 disabled:opacity-40"
          >
            <X className="size-3.5" />
          </button>
        </div>
      </div>
    );
  }

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <PauseCircle className="size-4 text-muted-foreground" />
          Parked runs
          {needsInput.length > 0 && (
            <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:bg-amber-900/60 dark:text-amber-200">
              {needsInput.length} need{needsInput.length === 1 ? "s" : ""} your
              input
            </span>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {needsInput.length > 0 && (
          <div className="grid gap-1.5">
            <p className="text-xs font-medium text-amber-800 dark:text-amber-300">
              Needs your input — answer the question above to resume
            </p>
            <div className="grid gap-1.5">{needsInput.map(row)}</div>
          </div>
        )}
        {informational.length > 0 && (
          <div className="grid gap-1.5">
            {needsInput.length > 0 && (
              <p className="text-xs font-medium text-muted-foreground">
                Resolves on its own — nothing needed from you
              </p>
            )}
            <div className="grid gap-1.5">{informational.map(row)}</div>
          </div>
        )}
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
