"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, XCircle } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/** US-23.1: abandon a release whose run hasn't been picked up.
 *
 * Only rendered for a `queued` release. Once an agent holds the run the API
 * refuses, and the honest routes are the existing ones — stop the run, or let
 * it reach UAT and reject it. */
export function CancelReleaseButton({
  releaseId,
  version,
  size = "sm",
}: {
  releaseId: string;
  version: string;
  size?: "sm" | "icon-sm";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function cancel() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/api/v1/releases/${releaseId}/cancel`, { method: "POST" });
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button variant="outline" size={size} />}>
        <XCircle className="size-4" />
        {size === "sm" ? "Cancel" : ""}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel release {version}?</DialogTitle>
          <DialogDescription>
            Its queued run is removed and the release is marked cancelled. The
            project is freed immediately, so you can cut a replacement at once.
            The version {version} is not reused — a version names exactly one
            build — and the git tag is left alone.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>
            Keep it
          </Button>
          <Button disabled={busy} onClick={cancel}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Cancel release
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
