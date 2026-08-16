"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";

/**
 * us-107.1: complete a work item that was fixed outside the pipeline.
 *
 * Not every defect earns plan → code → review → merge; some are fixed in a
 * change already in flight. Until now the only way to clear one was **Abandon**,
 * which records the opposite of what happened — abandoned means "we decided not
 * to do this".
 *
 * One component for all three surfaces (Workbench, the item page, the Issues
 * hub) because they would otherwise drift on the thing that matters most here:
 * what the click actually does to the originating report.
 *
 * `mark_issue_fixed` (migration 278) does the whole transition in one
 * transaction — the item to `done`, the report it was promoted from to `fixed`,
 * and the parent feature closed if this was its last open child — so this
 * component never has to sequence writes or guard against a half-applied state.
 */
export function MarkFixedButton({
  issueId,
  variant = "outline",
  size = "sm",
  className,
  label = "Mark fixed",
  onFixed,
}: {
  issueId: string;
  variant?: "outline" | "ghost" | "default";
  size?: "sm" | "default";
  className?: string;
  label?: string;
  /** Lets a list update its own row instead of paying for a full refresh. */
  onFixed?: () => void;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  async function markFixed() {
    setBusy(true);
    const { data, error } = await createClient().rpc("mark_issue_fixed", {
      p_issue: issueId,
    });
    setBusy(false);
    if (error) {
      toastError(error.message);
      return;
    }
    // The RPC reports what else it closed, so the toast can say so rather than
    // leaving the manager to go and check whether the report followed.
    const result = (data ?? {}) as {
      report_id?: string | null;
      feature_completed?: string | null;
    };
    const also = [
      result.report_id ? "its report is fixed" : null,
      result.feature_completed ? "its feature is complete" : null,
    ].filter(Boolean);
    toastSuccess(
      "Marked fixed",
      also.length ? `Complete — ${also.join(", ")}.` : undefined,
    );
    if (onFixed) onFixed();
    else router.refresh();
  }

  return (
    <Button
      variant={variant}
      size={size}
      className={className}
      disabled={busy}
      onClick={markFixed}
      title="Complete this without a run — and close the report it came from"
    >
      {busy ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Check className="size-4 text-green-600" />
      )}
      {label}
    </Button>
  );
}
