"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, X } from "lucide-react";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { useRememberedToggle } from "@/lib/use-remembered-toggle";
import { notifyDocsWrite } from "@/lib/docs-tree-outcome";
import { useActivitySession } from "@/lib/use-activity-session";

/** Approve or send back a draft plan/test_plan (us-2.5). Approve
 * materializes the test plan into test_cases server-side. */
export function PlanReviewActions({ issueId }: { issueId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"approve" | "send-back" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sendBackOpen, setSendBackOpen] = useState(false);
  const [comment, setComment] = useState("");
  // US-62.6: see prd-review-actions.tsx — this component's mount lifetime
  // already is "how long this item sat in front of a manager for plan review."
  useActivitySession(true, "plan", issueId);
  // us-12.4: approving a plan and starting the build were two manager
  // actions on two different pages, with the second carrying no new
  // judgement.
  const [alsoDispatch, setAlsoDispatch] = useRememberedToggle(
    "sf-continue-after-plan-approve",
    true
  );

  async function approve() {
    setError(null);
    setBusy("approve");
    try {
      // US-15.1: surface the repo-write outcome carried in the approval
      // response, so the approved plan reaching the repo is never silent.
      const res = await apiFetch(`/api/v1/issues/${issueId}/plan/approve`, {
        method: "POST",
      });
      notifyDocsWrite(res?.docs_tree, "Plan");
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
      return;
    }

    // us-12.4: past this point the plan IS approved. A dispatch failure
    // must not read as a failed approval.
    if (alsoDispatch) {
      try {
        await apiFetch(`/api/v1/issues/${issueId}/dispatch`, {
          method: "POST",
        });
      } catch (e) {
        setError(
          `Plan approved, but the code run could not be dispatched: ${
            (e as Error).message
          }`
        );
        setBusy(null);
        router.refresh();
        return;
      }
    }

    // US-14.3: push only. router.refresh() here refreshed the route
    // being left — the review page, whose gate this action just
    // closed — and that re-render is what produced the 404. The
    // destination is a dynamic server route; navigating fetches it
    // fresh without help.
    router.push(`/issues/${issueId}?from=dashboard`);

  }

  async function sendBack() {
    if (!comment.trim()) {
      setError("A comment is required — it becomes the re-plan's feedback.");
      return;
    }
    setError(null);
    setBusy("send-back");
    try {
      await apiFetch(`/api/v1/issues/${issueId}/plan/send-back`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ comment: comment.trim() }),
      });
      router.push(`/issues/${issueId}?from=dashboard`);

    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex items-center gap-2">
        <Button onClick={approve} disabled={busy !== null}>
          {busy === "approve" && <Loader2 className="size-4 animate-spin" />}
          <Check className="size-4" />
          {alsoDispatch ? "Approve & build" : "Approve plan"}
        </Button>

        <Dialog open={sendBackOpen} onOpenChange={setSendBackOpen}>
          <DialogTrigger render={<Button variant="outline" disabled={busy !== null} />}>
            <X className="size-4" />
            Send back
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Send the plan back</DialogTitle>
              <DialogDescription>
                Your comment becomes the feedback for the next plan attempt.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2">
              <Label htmlFor="plan-sendback-comment">What needs to change?</Label>
              <Textarea
                id="plan-sendback-comment"
                rows={4}
                placeholder="Missing an approach for the migration rollback."
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setSendBackOpen(false)}
                disabled={busy === "send-back"}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={sendBack}
                disabled={busy === "send-back"}
              >
                {busy === "send-back" && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Send back
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
      {/* us-12.4: state what Approve will do, before it is pressed.
          Unchecking leaves the story `planned` with the build un-started. */}
      <label className="flex cursor-pointer items-center gap-2 text-xs text-muted-foreground">
        <Checkbox
          checked={alsoDispatch}
          onCheckedChange={(v) => setAlsoDispatch(Boolean(v))}
          aria-label="Dispatch the code run on approval"
          disabled={busy !== null}
        />
        {alsoDispatch
          ? "Dispatches the code run on approval"
          : "Approve only — dispatch the build yourself"}
      </label>
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
