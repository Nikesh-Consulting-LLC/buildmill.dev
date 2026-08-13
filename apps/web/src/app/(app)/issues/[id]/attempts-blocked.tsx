"use client";

/**
 * US-31.5: a blocked item says what happened.
 *
 * When an item exhausts its attempt ceiling it stops being dispatched — by
 * anyone, including the auto-approve paths. That is only useful if the
 * manager can see how many attempts were spent, which agents spent them, and
 * what the last failure actually said (us-27.12: evidence before theory). The
 * release action clears the block AND the attempt history, so the item starts
 * counting again rather than blocking on its next failure.
 */

import { useState } from "react";
import { AlertTriangle, Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { ApiError, apiCall } from "@/lib/api";

type Summary = {
  attempts: number;
  ceiling: number;
  blocked: boolean;
  by_worker: { worker: string; attempts: number }[];
  last_error: string | null;
};

export function AttemptsBlockedBanner({
  issueId,
  summary,
}: {
  issueId: string;
  summary: Summary;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [released, setReleased] = useState(false);

  if (!summary.blocked || released) return null;

  async function release() {
    const ok = await confirmDialog({
      title: "Release this item to try again?",
      description:
        `It has failed ${summary.attempts} time(s). Releasing clears the ` +
        "attempt history so it can be dispatched again — fix the cause first, " +
        "or it will simply block again.",
      confirmLabel: "Release",
    });
    if (!ok) return;
    setBusy(true);
    setError(null);
    try {
      await apiCall(`/api/v1/issues/${issueId}/attempts/release`, {
        method: "POST",
      });
      setReleased(true);
    } catch (e: unknown) {
      setError(e instanceof ApiError ? String(e.detail) : "Release failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/5 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-sm font-medium text-destructive">
            <AlertTriangle className="size-4 shrink-0" />
            Stopped after {summary.attempts} attempt
            {summary.attempts === 1 ? "" : "s"} (ceiling {summary.ceiling})
          </p>
          {summary.by_worker.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {summary.by_worker
                .map((w) => `${w.worker}: ${w.attempts}`)
                .join(" · ")}
            </p>
          )}
          {summary.last_error && (
            /* Verbatim, monospaced: the command's own words, not the
               harness's paraphrase of them. */
            <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border bg-background/60 p-2 text-xs">
              {summary.last_error}
            </pre>
          )}
          {error && (
            <p className="mt-2 text-xs font-medium text-destructive">{error}</p>
          )}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={release}
          disabled={busy}
          className="shrink-0"
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <RotateCcw className="size-4" />
          )}
          Release to try again
        </Button>
      </div>
    </div>
  );
}
