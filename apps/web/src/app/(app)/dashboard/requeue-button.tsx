"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, RotateCcw } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** US-13.6: one-click recovery for a run whose worker went silent —
 * release the claim and return it to the pool for another worker. */
export function RequeueButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function requeue() {
    setBusy(true);
    setError(null);
    try {
      await apiCall(`/api/v1/runs/${runId}/force-requeue`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <span className="flex items-center gap-1.5">
      <Button
        size="sm"
        variant="outline"
        className="h-7 gap-1 px-2 text-xs"
        disabled={busy}
        onClick={requeue}
        title="Release the claim and return this run to the pool"
      >
        {busy ? (
          <Loader2 className="size-3 animate-spin" />
        ) : (
          <RotateCcw className="size-3" />
        )}
        Requeue
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
