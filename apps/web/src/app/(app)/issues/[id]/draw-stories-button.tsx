"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { PencilRuler } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { toastError, toastSuccess } from "@/components/ui/toast";

/** US-48.3: draw every story under this feature, one run each.
 *
 * The confirm names the number before anything is queued, because fifteen
 * stories is fifteen separately metered agent runs — the same courtesy
 * us-41.2's curate confirm gives. */
export function DrawStoriesButton({
  featureId,
  candidates,
  alreadyDrawn,
}: {
  featureId: string;
  /** Children with no wireframe and none in flight — what would dispatch. */
  candidates: number;
  alreadyDrawn: number;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  if (candidates === 0 && alreadyDrawn === 0) return null;

  async function run() {
    setBusy(true);
    try {
      const result = (await apiCall(
        `/api/v1/issues/${featureId}/wireframes/batch-dispatch`,
        { method: "POST" }
      )) as { dispatched_count: number; skipped_count: number };
      // A batch where everything was skipped is a no-op, not a failure — say
      // so plainly rather than claiming work was queued.
      if (result.dispatched_count === 0) {
        toastSuccess(
          `Nothing to draw — all ${result.skipped_count} stories are already drawn or in flight`
        );
      } else {
        toastSuccess(
          `${result.dispatched_count} ${
            result.dispatched_count === 1 ? "story" : "stories"
          } queued for drawing` +
            (result.skipped_count ? ` · ${result.skipped_count} skipped` : "")
        );
      }
      router.refresh();
    } catch (e) {
      toastError(
        e instanceof ApiError
          ? String(e.message)
          : (e as Error).message || "Could not start the batch"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <ConfirmDialog
      trigger={
        <Button variant="outline" size="sm" disabled={busy || candidates === 0}>
          <PencilRuler className="size-4" />
          Draw every story
        </Button>
      }
      title={`Draw ${candidates} ${candidates === 1 ? "story" : "stories"}?`}
      description={
        `Each story gets its own agent run, drawn in order so that later ` +
        `stories can see the screens the earlier ones declared. Each run is ` +
        `separately metered.` +
        (alreadyDrawn
          ? ` ${alreadyDrawn} already drawn ${
              alreadyDrawn === 1 ? "story is" : "stories are"
            } skipped — redraw those individually, where you can say what was wrong.`
          : "")
      }
      confirmLabel={`Draw ${candidates}`}
      onConfirm={run}
    />
  );
}
