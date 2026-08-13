"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { useRememberedToggle } from "@/lib/use-remembered-toggle";
import { notifyDocsWrite } from "@/lib/docs-tree-outcome";
import { useActivitySession } from "@/lib/use-activity-session";
import { BREAKDOWN_MODE_ITEMS } from "@/app/(app)/issues/[id]/prd-panel";

/** US-12.2: approve or send back a draft PRD, from the same review surface
 * that already hosts plan and code review. Reviewing a PRD, a plan, and a
 * changeset are the same act, so they now happen in the same place with
 * the same controls in the same position.
 *
 * The breakdown mode + instructions are still captured here at approval —
 * they persist onto the feature and steer the split that the manager
 * dispatches afterwards (us-2.33, us-11.1). */
export function PrdReviewActions({
  issueId,
  projectId,
  breakdownMode = "automatic",
  breakdownInstructions = "",
}: {
  issueId: string;
  /** US-49.1 follow-up: to read the project's own breakdown instruction. */
  projectId?: string;
  breakdownMode?: string;
  breakdownInstructions?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"approve" | "send-back" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [sendBackOpen, setSendBackOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [mode, setMode] = useState(breakdownMode || "automatic");
  const [instructions, setInstructions] = useState(breakdownInstructions);
  // US-62.6: this component only renders while the issue is in prd-review —
  // its mount lifetime already IS "how long this item sat in front of a
  // manager for PRD review," no separate page-level wrapper needed.
  useActivitySession(true, "prd", issueId);
  // us-12.4: approving and starting the split were two separate manager
  // actions with a hunt in between. The second click carried no new
  // judgement — approving already was the decision.
  const [alsoDispatch, setAlsoDispatch] = useRememberedToggle(
    "sf-continue-after-prd-approve",
    true
  );
  /** Whether the box is showing the default rather than something written
   * for this feature — worth saying, so an untouched default does not read
   * as a deliberate instruction somebody typed. */
  const [prefilled, setPrefilled] = useState(false);
  const [loadingDefault, setLoadingDefault] = useState(false);

  /** The breakdown agent's instructions, opened with the ones it will
   * actually get rather than an empty box.
   *
   * A feature's instruction set is seeded at its FIRST dispatch, which is the
   * PRD run — so `## Expectations — prd run` is what a later breakdown run
   * reads (migration 053 seeds once). The project's own breakdown instruction
   * otherwise reaches that agent nowhere. Putting it here, editable, is what
   * makes the split steerable rather than a blank the manager has to guess at.
   *
   * A feature that already carries its own instructions keeps them untouched:
   * the manager's words win over the default, always. */
  async function loadDefaultInstructions() {
    if (instructions.trim() || !projectId) return;
    setLoadingDefault(true);
    const supabase = createClient();
    const { data: row } = await supabase
      .from("worker_instructions")
      .select("content")
      .eq("project_id", projectId)
      .eq("run_kind", "breakdown")
      .maybeSingle();
    let text = (row?.content ?? "").trim();
    if (!text) {
      // No project row — fall back to the factory default the Reset control
      // on the project's Instructions tab uses (us-5.14).
      const { data } = await supabase.rpc("default_worker_instruction", {
        p_kind: "breakdown",
      });
      text = ((data as string | null) ?? "").trim();
    }
    if (text) {
      setInstructions(text);
      setPrefilled(true);
    }
    setLoadingDefault(false);
  }

  async function approve() {
    setError(null);
    setBusy("approve");
    try {
      // US-15.1: the approval response carries the repo-write outcome; surface
      // it so the manager sees the approved PRD reached the repo (or didn't).
      const res = await apiFetch(`/api/v1/issues/${issueId}/prd/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          breakdown_mode: mode,
          breakdown_instructions: instructions.trim() || null,
        }),
      });
      notifyDocsWrite(res?.docs_tree, "PRD");
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
      return;
    }

    // us-12.4: the approval has landed. A failure past this point is a
    // failure to *continue*, not to approve — say exactly that rather than
    // letting it read as a failed approval, and stay on the page so the
    // manager can retry the dispatch deliberately.
    if (alsoDispatch) {
      try {
        await apiFetch(`/api/v1/issues/${issueId}/breakdown/dispatch`, {
          method: "POST",
        });
      } catch (e) {
        setError(
          `PRD approved, but the breakdown could not be dispatched: ${
            (e as Error).message
          }`
        );
        setBusy(null);
        router.refresh();
        return;
      }
    }

    setApproveOpen(false);
    // us-11.1: when the split was declined, land on the control that
    // starts it rather than leaving the manager to find it.
    // US-14.3: push only. router.refresh() here refreshed the route
    // being left — the review page, whose gate this action just
    // closed — and that re-render is what produced the 404. The
    // destination is a dynamic server route; navigating fetches it
    // fresh without help.
    router.push(`/issues/${issueId}?panel=stories&from=dashboard`);

  }

  async function sendBack() {
    if (!comment.trim()) {
      setError("A comment is required — it becomes the redraft's feedback.");
      return;
    }
    setError(null);
    setBusy("send-back");
    try {
      await apiFetch(`/api/v1/issues/${issueId}/prd/send-back`, {
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
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Dialog
          open={approveOpen}
          onOpenChange={(o) => {
            setApproveOpen(o);
            if (o) void loadDefaultInstructions();
          }}
        >
          <DialogTrigger render={<Button disabled={busy !== null} />}>
            <Check className="size-4" />
            Approve PRD
          </DialogTrigger>
          {/* The base carries `sm:max-w-sm`, which beats an unprefixed max-w
              above 640px — the override has to be prefixed too. This dialog
              now holds a full instruction document, so it gets the room. */}
          <DialogContent className="sm:max-w-3xl">
            <DialogHeader>
              <DialogTitle>Approve the PRD</DialogTitle>
              <DialogDescription>
                How should this feature break into stories? Your choice is
                saved on the feature and steers the split.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="prd-review-breakdown">Breakdown</Label>
                <Select
                  items={BREAKDOWN_MODE_ITEMS}
                  value={mode}
                  onValueChange={(v) => {
                    if (typeof v === "string") setMode(v);
                  }}
                >
                  <SelectTrigger id="prd-review-breakdown" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {BREAKDOWN_MODE_ITEMS.map((item) => (
                      <SelectItem key={item.value} value={item.value}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="prd-review-instructions">
                  Instructions for the breakdown agent
                  <span className="ml-1 font-normal text-muted-foreground">
                    {loadingDefault
                      ? "(loading the default…)"
                      : prefilled
                        ? "(the project default — edit it for this feature)"
                        : "(saved on this feature)"}
                  </span>
                </Label>
                <Textarea
                  id="prd-review-instructions"
                  rows={14}
                  className="max-h-[45vh] font-mono text-xs"
                  placeholder="e.g. Keep the API and UI in one story."
                  value={instructions}
                  onChange={(e) => {
                    setInstructions(e.target.value);
                    setPrefilled(false);
                  }}
                />
              </div>
              {/* us-12.4: name what will happen before the manager
                  commits. Declining leaves the feature ready with the
                  split un-started — the curation window us-2.33 exists
                  to protect. */}
              <label className="flex cursor-pointer items-start gap-2 rounded-md border bg-muted/30 px-3 py-2">
                <Checkbox
                  checked={alsoDispatch}
                  onCheckedChange={(v) => setAlsoDispatch(Boolean(v))}
                  aria-label="Dispatch the breakdown now"
                  className="mt-0.5"
                />
                <span className="text-sm">
                  <span className="font-medium">
                    Dispatch the breakdown now
                  </span>
                  <span className="block text-muted-foreground">
                    {alsoDispatch
                      ? "An agent will start splitting this PRD into stories as soon as you approve."
                      : "Approve only — nothing is dispatched, and you can start the split from the Stories panel whenever you're ready."}
                  </span>
                </span>
              </label>
            </div>
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setApproveOpen(false)}
                disabled={busy === "approve"}
              >
                Cancel
              </Button>
              <Button onClick={approve} disabled={busy === "approve"}>
                {busy === "approve" && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                <Check className="size-4" />
                {alsoDispatch ? "Approve & break down" : "Approve only"}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={sendBackOpen} onOpenChange={setSendBackOpen}>
          <DialogTrigger
            render={<Button variant="outline" disabled={busy !== null} />}
          >
            <X className="size-4" />
            Send back
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Send the PRD back</DialogTitle>
              <DialogDescription>
                Your comment becomes the feedback for the redraft, which is
                dispatched automatically.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2">
              <Label htmlFor="prd-review-sendback">What needs to change?</Label>
              <Textarea
                id="prd-review-sendback"
                rows={4}
                placeholder="The acceptance criteria don't cover the admin-only case."
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
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
