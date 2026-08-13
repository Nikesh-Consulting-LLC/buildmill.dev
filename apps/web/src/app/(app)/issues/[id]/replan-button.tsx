"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, RotateCcw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

const REPLANNABLE = new Set(["draft", "ready", "failed", "planned", "needs-fixes"]);

/** Supersedes the approved plan/test_plan and starts a fresh plan run
 * (us-2.5). Not shown while a run is active. */
export function ReplanButton({
  issueId,
  status,
}: {
  issueId: string;
  status: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!REPLANNABLE.has(status)) return null;

  async function handleReplan() {
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/issues/${issueId}/replan`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button variant="outline" size="sm" onClick={handleReplan} disabled={busy}>
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <RotateCcw className="size-3.5" />
        )}
        Re-plan
      </Button>
      {error && (
        <p className="max-w-64 text-right text-xs font-medium text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}
