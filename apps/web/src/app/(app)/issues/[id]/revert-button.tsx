"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Undo2 } from "lucide-react";
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

export function RevertButton({ issueId }: { issueId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleRevert() {
    setError(null);
    setBusy(true);
    try {
      await apiFetch(`/api/v1/issues/${issueId}/revert`, { method: "POST" });
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
      <DialogTrigger render={<Button variant="outline" />}>
        <Undo2 className="size-4" />
        Revert
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Revert this merge?</DialogTitle>
          <DialogDescription>
            Opens a new pull request that reverses the merged PR&apos;s
            changes — you still review and merge it separately.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={handleRevert} disabled={busy}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Open revert PR
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
