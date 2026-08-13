"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, XCircle } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/** US-27.10: retire a run that should not have been dispatched.
 *
 * The three neighbouring actions are easy to confuse and the difference
 * matters, so the dialog states all three: pause keeps a run in the queue,
 * reset sends it back to the pool to be claimed again, cancel ends it. A
 * reason is required — the queue is a shared surface and "why did this
 * disappear" gets asked days later. */
export function CancelRunDialog({
  runId,
  runKind,
  running,
  trigger,
}: {
  runId: string;
  runKind: string;
  /** a claimed run is asked to stop cooperatively; it is never killed. */
  running: boolean;
  trigger?: React.ReactNode;
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function cancel() {
    setError(null);
    setBusy(true);
    try {
      await apiCall(`/api/v1/runs/${runId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      setOpen(false);
      setReason("");
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) setReason("");
      }}
    >
      <DialogTrigger
        render={
          (trigger as React.ReactElement) ?? (
            <Button
              size="sm"
              variant="outline"
              className="h-7 gap-1 px-2 text-xs"
              title="Cancel — this run should not have been dispatched"
            >
              <XCircle className="size-3" />
              Cancel
            </Button>
          )
        }
      />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Cancel this {runKind} run?</DialogTitle>
          <DialogDescription>
            {running
              ? "The agent holding it is asked to stop and clean up; the run lands cancelled when it hands the claim back. Work is never killed mid-flight."
              : "The run ends now and its work item goes back to the status it had before the dispatch."}{" "}
            It is not a failure and it will not count against any agent. The
            run stays readable in the item&rsquo;s history with your reason.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-2">
          <Textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why should this run not have been dispatched?"
            rows={3}
          />
          <p className="text-xs text-muted-foreground">
            <span className="font-medium">Pause</span> keeps its place in the
            queue · <span className="font-medium">Reset</span> sends it back to
            the pool for another attempt ·{" "}
            <span className="font-medium">Cancel</span> ends it.
          </p>
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => setOpen(false)}
            disabled={busy}
          >
            Keep it
          </Button>
          <Button onClick={cancel} disabled={busy || !reason.trim()}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            {running ? "Ask it to stop and cancel" : "Cancel run"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
