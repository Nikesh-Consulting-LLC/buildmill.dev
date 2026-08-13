"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, RefreshCcw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";

/** US-90.1: a failed release retries; a rejected one is final.
 *
 * Rendered only for `failed` / `uat-deploy-failed` — the attempt died before
 * anything shipped. The API re-runs just the failed leg (a fresh notes prep,
 * or a fresh UAT deploy) against the same version and pinned commit; which
 * leg is a fact on the release row, not a choice made here. */
export function RetryReleaseButton({
  releaseId,
  size = "sm",
}: {
  releaseId: string;
  size?: "sm" | "icon-sm";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function retry() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const r = (await apiFetch(`/api/v1/releases/${releaseId}/retry`, {
        method: "POST",
      })) as { leg?: string; attempt?: number };
      setNotice(
        r.leg === "deploy"
          ? `Re-running the UAT deploy of the pinned commit (attempt ${r.attempt}).`
          : `Queued a fresh notes prep — same version, same pinned commit (attempt ${r.attempt}).`
      );
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <span className="inline-flex flex-col items-end gap-1">
      <Button
        variant="outline"
        size={size}
        disabled={busy}
        onClick={retry}
        title="Re-runs the failed leg — same version, same pinned commit. A rejected build never retries."
      >
        {busy ? (
          <Loader2 className="size-4 animate-spin" />
        ) : (
          <RefreshCcw className="size-4" />
        )}
        {size === "sm" ? "Retry" : ""}
      </Button>
      {notice && <span className="text-xs text-amber-600">{notice}</span>}
      {error && (
        <span className="text-xs font-medium text-destructive">{error}</span>
      )}
    </span>
  );
}
