"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Hand, Loader2 } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** US-15.15: ask the working agent to stop and clean up after itself — the
 * cooperative path. The request rides the agent's next heartbeat; it undoes
 * its own partial work and releases the claim. Distinct from us-15.14's
 * "Reset run" (the forced fallback when the agent doesn't cooperate). */
export function StopWorkButton({
  runId,
  alreadyRequested,
}: {
  runId: string;
  alreadyRequested: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function requestStop() {
    setBusy(true);
    setError(null);
    try {
      await apiCall(`/api/v1/runs/${runId}/request-stop`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (alreadyRequested) {
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-400">
        <Hand className="size-3.5" />
        Stop requested — waiting for the agent to acknowledge
      </span>
    );
  }

  return (
    <span className="flex items-center gap-1.5">
      <Button
        size="sm"
        variant="outline"
        className="h-8 gap-1"
        disabled={busy}
        onClick={requestStop}
        title="Ask the working agent to stop and undo its partial work — it acknowledges on its next heartbeat"
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <Hand className="size-4" />
        )}
        Stop work
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </span>
  );
}
