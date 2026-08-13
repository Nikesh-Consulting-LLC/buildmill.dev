"use client";

import { useState } from "react";
import { HeartPulse, Loader2 } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** US-1.40: run the configured health check on demand — no deploy. */
export function HealthCheckButton({ deploymentId }: { deploymentId: string }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);

  async function handleCheck() {
    setBusy(true);
    setResult(null);
    try {
      const resp = (await apiCall(
        `/api/v1/deployments/${deploymentId}/health-check`,
        { method: "POST" }
      )) as { ok: boolean; last: string };
      setResult(resp.ok ? `Healthy (${resp.last})` : `Unhealthy: ${resp.last}`);
    } catch (e) {
      setResult(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="outline" size="sm" onClick={handleCheck} disabled={busy}>
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <HeartPulse className="size-3.5" />
        )}
        Health check
      </Button>
      {result && (
        <span
          className={
            result.startsWith("Healthy")
              ? "text-xs text-emerald-600"
              : "text-xs text-destructive"
          }
        >
          {result}
        </span>
      )}
    </span>
  );
}
