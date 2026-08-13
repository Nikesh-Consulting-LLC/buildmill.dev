"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ListChecks, Loader2, Rocket } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { ApproveAllPlansButton } from "@/components/approve-all-plans-button";
import {
  BatchPhaseDialog,
  fetchDispatchPhase,
  type DispatchPhase,
} from "@/components/batch-phase-dialog";
import { batchGateLabel, type BatchGate } from "@/lib/batch-gate";

/** US-84.1: the feature header's one-click batch action in Waiting on you.
 *
 * The one exception to us-24.1's "the feature header carries no action",
 * generalized from us-25.2's plan gate: it renders only when EVERY
 * non-abandoned child sits at the same gate (data.ts derives that from all
 * siblings, not just the loaded rows), and it does something no single row
 * can — clear the whole gate. Same endpoints, same server-side checks as
 * the feature page's stage tracker: one action, two entry points.
 *
 * - curate → `curate_feature_stories` RPC (us-41.2), one transaction.
 * - plan / code → the us-27.11 phase-stating confirm (shared
 *   BatchPhaseDialog), then the same `/batch-dispatch` endpoint.
 * - approve → ApproveAllPlansButton, unchanged from us-25.2.
 */
export function FeatureBatchAction({
  featureId,
  orgId,
  gate,
  compact = false,
}: {
  featureId: string;
  orgId: string;
  gate: BatchGate;
  /** Waiting on you nests this inside a table header row, where the page's
   * standard button height breaks the row rhythm. */
  compact?: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingPhase, setPendingPhase] = useState<DispatchPhase | null>(null);

  if (gate.kind === "approve") {
    return (
      <ApproveAllPlansButton
        featureId={featureId}
        pending={gate.count}
        compact={compact}
      />
    );
  }

  /** US-41.2's action from a second entry point: move every draft story to
   * `ready` in one transaction. Plain org-scoped CRUD under RLS, so it goes
   * straight to the RPC like the stage tracker does. */
  async function curateAll() {
    setError(null);
    setBusy(true);
    try {
      const supabase = createClient();
      const { error: rpcError } = await supabase.rpc(
        "curate_feature_stories",
        { p_feature: featureId }
      );
      if (rpcError) throw new Error(rpcError.message);
      toastSuccess(
        `Curated ${gate.count} stories`,
        "They move to ready — plan them when you're set."
      );
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      toastError("Couldn't curate the stories", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  /** US-27.11: a batch that spends money states its phase first. The phase
   * comes from `feature_dispatch_phase` — the dispatcher's own predicates —
   * never from this row's idea of what the children are doing. */
  async function askPhase() {
    setError(null);
    setBusy(true);
    try {
      setPendingPhase(await fetchDispatchPhase(featureId));
    } catch (e) {
      setError((e as Error).message);
      toastError("Couldn't read the batch", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function batchDispatch() {
    const phase = pendingPhase?.phase;
    setPendingPhase(null);
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/issues/${featureId}/batch-dispatch`, {
        method: "POST",
      });
      toastSuccess(
        phase === "code" ? "Build dispatched" : "Planning dispatched",
        "The stories move to In the factory."
      );
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      toastError("Couldn't dispatch the batch", (e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const label = batchGateLabel(gate);
  const title =
    gate.kind === "curate"
      ? `Move all ${gate.count} draft stories to ready — "I have read the breakdown"`
      : gate.kind === "plan"
        ? `Dispatch planning for all ${gate.count} stories`
        : `Dispatch the code phase for all ${gate.count} stories`;

  return (
    <span className="flex shrink-0 items-center gap-2">
      {error && (
        <span className="text-xs font-medium text-destructive">{error}</span>
      )}
      <Button
        size="sm"
        variant={gate.kind === "curate" ? "success" : "default"}
        className={compact ? "h-6" : undefined}
        disabled={busy}
        title={title}
        onClick={gate.kind === "curate" ? curateAll : askPhase}
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : gate.kind === "curate" ? (
          <ListChecks className="size-4" />
        ) : (
          <Rocket className="size-4" />
        )}
        {label}
      </Button>
      <BatchPhaseDialog
        featureId={featureId}
        orgId={orgId}
        phase={pendingPhase}
        onOpenChange={(o) => !o && setPendingPhase(null)}
        onConfirm={batchDispatch}
        busy={busy}
      />
    </span>
  );
}
