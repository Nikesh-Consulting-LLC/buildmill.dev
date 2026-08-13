"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
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

/** US-70.1: remove a dead release record — rejected, failed or cancelled.
 *
 * Plain CRUD under RLS: the delete policy is the enforcement (owner/admin,
 * terminal-unsuccessful status only) — this button is just the reachable
 * surface. Children cascade; deployment history keeps its rows; the version
 * name is never reused either way. */
export function DeleteReleaseButton({
  releaseId,
  version,
  status,
}: {
  releaseId: string;
  version: string;
  status: string;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function remove() {
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError, count } = await supabase
      .from("releases")
      .delete({ count: "exact" })
      .eq("id", releaseId);
    setBusy(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    if (!count) {
      // RLS answered "no rows" — not an error to Postgres, but the record
      // did not go. Say so instead of silently refreshing.
      setError("Not deleted — only an owner or admin can delete this record.");
      return;
    }
    setOpen(false);
    router.refresh();
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={<Button variant="outline" size="sm" />}
      >
        <Trash2 className="size-4" />
        Delete
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete release {version}?</DialogTitle>
          <DialogDescription>
            This removes the {status} record and its attached test results
            from the hub. Deployment history keeps its rows, and the version
            name {version} is not reused — a version names exactly one build,
            even a dead one.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
        <DialogFooter>
          <Button variant="outline" disabled={busy} onClick={() => setOpen(false)}>
            Keep it
          </Button>
          <Button variant="destructive" disabled={busy} onClick={remove}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            Delete record
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
