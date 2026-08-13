"use client";

import { useRouter } from "@/lib/router-with-progress";
import { RotateCcw } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

/** US-15.14: what a reset throws away, by run kind — spelled out so the
 * confirmation states plainly what's lost before it happens. */
const KIND_DISCARDS: Record<string, string> = {
  prd: "the draft PRD from this attempt",
  plan: "the draft plan and test plan from this attempt",
  breakdown: "the draft child stories from this attempt",
  code: "this attempt's branch pointer (the branch on GitHub is left in place)",
  test: "the queued/running verification attempt (nothing on the code branch is touched)",
};

/** US-15.14: reset a wrongly-started run from the work item's own page —
 * discard this attempt's draft output, return the item to its pre-dispatch
 * status, and re-queue a fresh attempt. Available whenever a run is active
 * (queued or running), not gated on the worker looking stuck. */
export function ResetRunButton({
  runId,
  runKind,
}: {
  runId: string;
  runKind: string;
}) {
  const router = useRouter();
  const discards = KIND_DISCARDS[runKind] ?? "this attempt's draft output";

  async function reset() {
    await apiCall(`/api/v1/runs/${runId}/reset`, { method: "POST" });
    router.refresh();
  }

  return (
    <ConfirmDialog
      trigger={
        <Button variant="outline" size="sm">
          <RotateCcw className="size-4" />
          Reset run
        </Button>
      }
      title="Reset and send back to the queue?"
      description={`This cancels the ${runKind} run in flight, discards ${discards}, returns the work item to the status it had before the run was dispatched, and re-queues a fresh attempt. A worker still on this run will fail to hand back. This can't be undone.`}
      confirmLabel="Reset run"
      requireText="reset"
      onConfirm={reset}
    />
  );
}
