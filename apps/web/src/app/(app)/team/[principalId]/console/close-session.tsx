"use client";

/**
 * US-78.10 AC7: closing a session releases the agent, ends the ACP session, and
 * says what became of the workspace.
 */

import { useState } from "react";
import { Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { apiCall } from "@/lib/api";
import { useRouter } from "@/lib/router-with-progress";

export function CloseSession({ sessionId }: { sessionId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        variant="outline"
        size="sm"
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await apiCall(`/api/v1/agent-sessions/${sessionId}/close`, {
              method: "POST",
            });
            router.refresh();
          } catch (e) {
            setError(e instanceof Error ? e.message : "Could not close the session.");
          } finally {
            setBusy(false);
          }
        }}
      >
        <Square className="size-3.5" />
        {busy ? "Closing…" : "Close session"}
      </Button>
      <p className="text-xs text-muted-foreground">
        Releases the agent so it can claim runs again. The checkout is kept, so
        reopening a session on this project picks up where this one left off.
      </p>
      {error && (
        <p className="text-xs text-amber-700 dark:text-amber-400">{error}</p>
      )}
    </div>
  );
}
