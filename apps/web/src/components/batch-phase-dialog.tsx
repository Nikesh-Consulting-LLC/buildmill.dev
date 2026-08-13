"use client";

import { Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { InstructionPreview } from "@/components/instruction-preview";

/** US-27.11: what a batch dispatch is ABOUT to do. On 2026-07-26 the manager
 * asked for six stories to go back to coding and the factory queued six plan
 * runs instead; the only signal was six `plan-dispatched` events nobody was
 * watching for. The phase comes from the same predicates the dispatcher uses
 * (`feature_dispatch_phase`), so the two cannot drift.
 *
 * US-84.1: extracted from the stage tracker so the dashboard's feature-header
 * batch action confirms with the SAME dialog — one action, two entry points,
 * one set of words. */
export type DispatchPhase = {
  phase: string;
  reason: string;
  children: number;
  buildable: number;
  unplanned: number;
  /** US-41.1: every story sits at the same stage, so "next" means one
   * thing. Mixed stages get no bulk action. */
  same_stage: boolean;
  common_stage: string | null;
  /** US-41.1: feature/epic → one run over every story; story → one run
   * each. From the RPC, so the label cannot drift from the dispatcher. */
  build_mode: string;
};

export async function fetchDispatchPhase(
  featureId: string
): Promise<DispatchPhase | null> {
  const supabase = createClient();
  const { data, error } = await supabase.rpc("feature_dispatch_phase", {
    p_feature: featureId,
  });
  if (error) throw new Error(error.message);
  return (data as DispatchPhase) ?? null;
}

/** US-27.11: say which phase this is before queueing anything. The two
 * outcomes are "plan these stories" and "build these stories", and getting
 * the wrong one costs six runs and a repair. */
export function BatchPhaseDialog({
  featureId,
  orgId,
  phase,
  onOpenChange,
  onConfirm,
  busy,
}: {
  featureId: string;
  orgId: string;
  phase: DispatchPhase | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  busy: boolean;
}) {
  /** US-41.1: in feature/epic mode the feature owns the code build — one run
   * carrying every story. In story mode the batch is one run each, so the
   * confirm must not promise "one PR". */
  const featureOwnsBuild =
    phase?.build_mode === "feature" || phase?.build_mode === "epic";

  return (
    <Dialog open={!!phase} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {!phase?.same_stage
              ? "These stories are at different stages"
              : phase?.phase === "code"
                ? `Build ${phase.buildable} ${phase.buildable === 1 ? "story" : "stories"}${featureOwnsBuild ? " as one run" : ""}?`
                : phase?.phase === "plan"
                  ? `Plan ${phase.children} ${phase.children === 1 ? "story" : "stories"}?`
                  : "Nothing to dispatch"}
          </DialogTitle>
          <DialogDescription>
            {/* US-41.1: a bulk action needs one meaning of "next". Mixed
                stages get the reason instead of the action. */}
            {!phase?.same_stage
              ? "Move them to the same stage first, or dispatch them individually — with some ahead of others, “next” would mean two different things."
              : phase?.reason}
            {phase?.same_stage &&
              phase?.phase === "code" &&
              (featureOwnsBuild
                ? " — one branch, one PR, one review covering all of them."
                : " — one run, one branch and one PR per story, drained one at a time.")}
            {phase?.same_stage &&
              phase?.phase === "plan" &&
              " — one planning run per story, drained one at a time."}
          </DialogDescription>
        </DialogHeader>
        {/* US-49.1: a code batch in feature/epic mode seeds the FEATURE
            (migrations 139/169), so there is one instruction set and it is
            shown. A plan batch is one run per story with one set each —
            fifteen scrolling documents is not a confirmation, so it says so
            and previews nothing. */}
        {phase?.same_stage && phase?.phase === "code" && featureOwnsBuild && (
          <InstructionPreview issueId={featureId} orgId={orgId} kind="code" />
        )}
        {phase?.same_stage && phase?.phase === "plan" && (
          <p className="text-xs text-muted-foreground">
            {phase.children} {phase.children === 1 ? "story" : "stories"}, each
            with its own instruction set — open a story to see or change its
            own.
          </p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Not now
          </Button>
          <Button
            disabled={
              busy ||
              !phase?.same_stage ||
              (phase?.phase !== "code" && phase?.phase !== "plan")
            }
            onClick={onConfirm}
          >
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {phase?.phase === "code" ? "Build them" : "Plan them"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
