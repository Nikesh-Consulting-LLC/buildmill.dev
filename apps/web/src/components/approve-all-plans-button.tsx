"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { CheckCheck, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";

/** US-20.6: clear the plan gate for every child story sitting in plan-review.
 *
 * Disabled — not hidden — when there is nothing to approve. A missing button
 * reads as "nothing to do here"; a disabled one that says why reads as the
 * truth. It only ever approves: a send-back stays per-story, because one
 * comment cannot honestly describe what is wrong with nine plans.
 *
 * US-25.2: also the feature header's action in Waiting on you, so a manager
 * looking at six sibling stories clears them without opening the feature. One
 * action, two entry points — same endpoint, same server-side checks. */
export function ApproveAllPlansButton({
  featureId,
  pending,
  compact = false,
}: {
  featureId: string;
  pending: number;
  /** Waiting on you nests this inside a table header row, where the page's
   * standard button height breaks the row rhythm. */
  compact?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function approveAll() {
    setBusy(true);
    setError(null);
    try {
      const result = (await apiFetch(
        `/api/v1/issues/${featureId}/plans/approve-all`,
        { method: "POST" }
      )) as {
        approved: unknown[];
        skipped: { issue_id: string; reason: string }[];
      };
      const ok = result.approved.length;
      const bad = result.skipped.length;
      // US-25.2: a partial result is the dangerous one — the rows that did
      // clear vanish on refresh, so a manager who is told nothing reads the
      // survivors as a rendering glitch rather than as work still on them.
      if (!ok && bad) {
        setError(`Nothing approved — ${result.skipped[0].reason}`);
        toastError("Nothing approved", result.skipped[0].reason);
      } else if (bad) {
        setError(`${ok} approved, ${bad} not`);
        toastError(
          `${ok} approved, ${bad} not`,
          `Still waiting on you: ${result.skipped
            .map((s) => s.reason)
            .join("; ")}`
        );
      } else if (ok) {
        toastSuccess(
          `Approved ${ok} plan${ok === 1 ? "" : "s"}`,
          "The stories move on to the next stage."
        );
      }
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      toastError("Couldn't approve the plans", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="flex shrink-0 items-center gap-2">
      {error && (
        <span className="text-xs font-medium text-destructive">{error}</span>
      )}
      <Button
        size="sm"
        variant="success"
        className={compact ? "h-6" : undefined}
        disabled={busy || pending === 0}
        title={
          pending === 0
            ? "No story is waiting on plan approval"
            : `Approve ${pending} plan${pending === 1 ? "" : "s"} at once`
        }
        onClick={approveAll}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <CheckCheck className="size-4" />
        )}
        Approve all {pending || ""} plans
      </Button>
    </span>
  );
}
