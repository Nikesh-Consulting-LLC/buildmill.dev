"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { toastError, toastSuccess } from "@/components/ui/toast";

/**
 * us-107.2: end a refresh nothing ever claimed.
 *
 * Its own client island so the card stays a server component — the card is
 * otherwise pure rendering, and turning the whole thing client-side to get one
 * button would drag the group's data into the bundle for no gain.
 *
 * Calls the existing `POST /api/v1/runs/{id}/cancel`, which already refuses
 * anything that is not `queued`/`running` and restores the run's issues. There
 * is deliberately no new endpoint here: cancelling a stalled guidelines run is
 * the same act as cancelling any other queued run, and a second path into it
 * would be a second thing to keep correct.
 *
 * Confirmed, unlike the Workbench's other one-click actions, because this one
 * is the only one that destroys queued work rather than starting some.
 */
export function CancelStalledRefresh({ runId }: { runId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function cancel() {
    setBusy(true);
    try {
      // `apiFetch` adds the bearer token but not the content type, and the
      // endpoint's body is a Pydantic model — without this header FastAPI
      // answers 422 and the reason never arrives.
      await apiFetch(`/api/v1/runs/${runId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          reason: "Queued with no worker — cancelled from the Workbench",
        }),
      });
      toastSuccess("Refresh cancelled", "Dispatch a new one when you're ready.");
      router.refresh();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Could not cancel the refresh");
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      trigger={
        <Button variant="outline" size="sm" disabled={busy}>
          {busy ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <X className="size-4" />
          )}
          Cancel it
        </Button>
      }
      title="Cancel this instructions refresh?"
      description="No worker ever claimed this run, so it has done no work and there is nothing to lose — but it also will not resume. The pass ends here; dispatch a fresh refresh whenever you want one."
      confirmLabel="Cancel the refresh"
      onConfirm={cancel}
    />
  );
}
