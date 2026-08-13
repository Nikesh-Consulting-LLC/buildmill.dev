"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { AlertTriangle, Check, Loader2, X } from "lucide-react";
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
import { toastSuccess } from "@/components/ui/toast";
import type { TestGateState } from "@/lib/test-state";
import { useActivitySession } from "@/lib/use-activity-session";

/** US-40.3: a refusal shown where the manager is looking.
 *
 * This used to render on the page BEHIND the open dialog, so a hard 409 read
 * as a dead button — and the rational response, clicking again, re-fired an
 * irreversible GitHub merge. The server's message is passed through verbatim:
 * these refusals name the offending stories and are written to be read.
 */
function ErrorNote({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss?: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0 flex-1 break-words">{message}</span>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 text-xs font-medium underline underline-offset-2"
        >
          Dismiss
        </button>
      )}
    </div>
  );
}

export function ReviewActions({
  runId,
  issueId,
  testState,
  canReview = true,
  mergedUnapproved = false,
  defaultRejectComment,
}: {
  runId: string;
  issueId: string;
  /** Soft merge gate (us-2.6): when set and needsOverride, Approve requires
   * a confirmed reason recorded via merge-override before the normal
   * approve path runs. */
  testState?: TestGateState;
  /** US-9.10: only a review_work member can approve or send back; others see
   * the review read-only. */
  canReview?: boolean;
  /** US-40.1: the PR merged but the approval did not land. The normal approve
   * path cannot repair this — it fails EARLIER, at the merge, because GitHub
   * will not merge an already-merged PR. */
  mergedUnapproved?: boolean;
  /** A failed/blocked verification pass's detail, prefilled into the Reject
   * dialog so "send back to coding agent" is a one-click confirm. */
  defaultRejectComment?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<
    "approve" | "reject" | "finish" | "conflict" | null
  >(null);
  const [error, setError] = useState<string | null>(null);
  const [approveOpen, setApproveOpen] = useState(false);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [comment, setComment] = useState(defaultRejectComment ?? "");
  const [overrideReason, setOverrideReason] = useState("");
  const [conflict, setConflict] = useState<{
    baseBranch: string;
    files: string[];
  } | null>(null);
  const [conflictDirection, setConflictDirection] = useState("");
  // US-62.6: see prd-review-actions.tsx — this component's mount lifetime
  // already is "how long this run sat in front of a manager for code review."
  useActivitySession(true, "code-review", issueId);

  const needsOverride = testState?.needsOverride ?? false;

  /** us-11.4: name the states that actually apply. A blocked case is not a
   * failure — telling a manager their tests are failing when nobody could
   * run one is both wrong and erodes trust in the gate. */
  const gateStates = [
    testState?.failing.length ? "failing" : null,
    testState?.blocked.length ? "blocked" : null,
    testState?.unrun.length ? "unrun" : null,
  ].filter(Boolean) as string[];
  const gateStateText =
    gateStates.length > 1
      ? `${gateStates.slice(0, -1).join(", ")} or ${gateStates[gateStates.length - 1]}`
      : (gateStates[0] ?? "unverified");

  async function approve() {
    if (needsOverride && !overrideReason.trim()) {
      setError(`A reason is required to override ${gateStateText} tests.`);
      return;
    }
    setError(null);
    setConflict(null);
    setBusy("approve");
    try {
      if (needsOverride) {
        await apiFetch(`/api/v1/issues/${issueId}/merge-override`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            reason: overrideReason.trim(),
            run_id: runId,
          }),
        });
      }
      const result = await apiFetch(`/api/v1/runs/${runId}/approve`, {
        method: "POST",
      });
      if (result?.merge_conflict) {
        // A real merge conflict — not an error, a decision the manager
        // needs to make. approve_run never ran; the issue is untouched.
        setApproveOpen(false);
        setConflict({
          baseBranch: result.base_branch,
          files: result.files ?? [],
        });
        setBusy(null);
        return;
      }
      if (result?.merge === "already-merged") {
        // US-79.2: the PR was merged by hand on GitHub; approve reconciled
        // instead of erroring, and the approval is now recorded.
        toastSuccess(
          "Already merged on GitHub",
          "The PR had been merged by hand — the approval is now recorded.",
        );
      }
      // US-14.3: push only. router.refresh() here refreshed the route
    // being left — the review page, whose gate this action just
    // closed — and that re-render is what produced the 404. The
    // destination is a dynamic server route; navigating fetches it
    // fresh without help.
    router.push(`/issues/${issueId}?from=dashboard`);

    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  /** Send a PR that hit a real merge conflict back to the coding agent, with
   * an optional manager direction. Reuses reject_run/needs-fixes under the
   * hood (server-side) — the manager just sees "resolve conflict." */
  async function sendBackForConflict() {
    setError(null);
    setBusy("conflict");
    try {
      await apiFetch(`/api/v1/runs/${runId}/reject-conflict`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          direction: conflictDirection.trim() || undefined,
        }),
      });
      router.push(`/issues/${issueId}?from=dashboard`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  /** US-40.1: record an approval whose merge already happened. GitHub is not
   * called — the PR is merged and cannot be merged twice, which is precisely
   * why Approve cannot repair this state. */
  async function finishApproval() {
    setError(null);
    setBusy("finish");
    try {
      await apiFetch(`/api/v1/runs/${runId}/finish-approval`, {
        method: "POST",
      });
      router.push(`/issues/${issueId}?from=dashboard`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  async function reject() {
    if (!comment.trim()) {
      setError("A comment is required — it becomes the retry's feedback.");
      return;
    }
    setError(null);
    setBusy("reject");
    try {
      await apiFetch(`/api/v1/runs/${runId}/reject`, {
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

  if (!canReview) {
    return (
      <span className="rounded-md border px-3 py-1.5 text-xs font-medium text-muted-foreground">
        Review only — you don&apos;t have review access
      </span>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Dialog
          open={approveOpen}
          onOpenChange={(open) => {
            setApproveOpen(open);
            if (open) setError(null); // a fresh attempt starts clean
          }}
        >
          <DialogTrigger render={<Button />}>
            <Check className="size-4" />
            Approve
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Approve and merge?</DialogTitle>
              <DialogDescription>
                Records your approval and merges the pull request — GitHub
                stays the source of truth.
              </DialogDescription>
            </DialogHeader>
            {needsOverride && (
              <div className="grid gap-2">
                <p className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" />
                  This issue has {gateStateText} linked tests. Approving
                  requires a recorded reason (merge override).
                </p>
                <Label htmlFor="override-reason">Override reason</Label>
                <Textarea
                  id="override-reason"
                  rows={3}
                  placeholder="Why it's safe to merge despite the test state."
                  value={overrideReason}
                  onChange={(e) => setOverrideReason(e.target.value)}
                />
              </div>
            )}
            {error && (
              <ErrorNote message={error} onDismiss={() => setError(null)} />
            )}
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setApproveOpen(false)}
                disabled={busy === "approve"}
              >
                Cancel
              </Button>
              {/* US-40.3: a refused action does not re-arm as if nothing
                  happened — dismissing the message is the deliberate act
                  that allows another attempt. */}
              <Button
                onClick={approve}
                disabled={busy === "approve" || !!error}
              >
                {busy === "approve" && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Approve &amp; merge
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog
          open={rejectOpen}
          onOpenChange={(open) => {
            setRejectOpen(open);
            if (open) setError(null);
          }}
        >
          <DialogTrigger render={<Button variant="outline" />}>
            <X className="size-4" />
            Reject
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Reject with feedback</DialogTitle>
              <DialogDescription>
                Your comment is attached to the retry so the next attempt is
                informed, not blind.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2">
              <Label htmlFor="reject-comment">What needs to change?</Label>
              <Textarea
                id="reject-comment"
                rows={4}
                placeholder="The version constant is hard-coded — read it from package metadata."
                value={comment}
                onChange={(e) => setComment(e.target.value)}
              />
            </div>
            {error && (
              <ErrorNote message={error} onDismiss={() => setError(null)} />
            )}
            <DialogFooter>
              <Button
                variant="outline"
                onClick={() => setRejectOpen(false)}
                disabled={busy === "reject"}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={reject}
                disabled={busy === "reject" || !!error}
              >
                {busy === "reject" && (
                  <Loader2 className="size-4 animate-spin" />
                )}
                Reject issue
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>

      {/* US-40.1: the PR is on the default branch and the factory never
          recorded it. Approve cannot fix this — it would fail at the merge,
          on a PR GitHub already considers merged. */}
      {mergedUnapproved && (
        <div className="flex flex-col gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          <p className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              This pull request was merged, but the approval was never
              recorded — the code is on the default branch and the factory
              does not know it. Finishing the approval records it. GitHub is
              not called again.
            </span>
          </p>
          <div>
            <Button onClick={finishApproval} disabled={busy === "finish"}>
              {busy === "finish" && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Finish approval
            </Button>
          </div>
        </div>
      )}

      {/* A real merge conflict — GitHub refused the merge because the PR is
          dirty against its base. This is a decision, not an error: send it
          back to the coding agent with direction, or resolve it manually
          and retry Approve. */}
      {conflict && (
        <div className="flex flex-col gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
          <p className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" />
            <span>
              GitHub could not merge this PR — it conflicts with{" "}
              <code>{conflict.baseBranch}</code>.
            </span>
          </p>
          {conflict.files.length > 0 && (
            <div className="ml-6">
              <p className="text-xs font-medium">
                Files this PR touched (most likely where conflicts are):
              </p>
              <ul className="mt-1 list-disc pl-4 text-xs">
                {conflict.files.map((f) => (
                  <li key={f} className="break-all">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="ml-6 grid gap-1.5">
            <Label htmlFor="conflict-direction" className="text-xs font-medium">
              Direction for the agent (optional)
            </Label>
            <Textarea
              id="conflict-direction"
              rows={3}
              placeholder="e.g. keep the version bump on our side, take their copy edits."
              value={conflictDirection}
              onChange={(e) => setConflictDirection(e.target.value)}
              className="bg-background text-foreground"
            />
          </div>
          {error && (
            <div className="ml-6">
              <ErrorNote message={error} onDismiss={() => setError(null)} />
            </div>
          )}
          <div className="ml-6 flex items-center gap-2">
            <Button
              onClick={sendBackForConflict}
              disabled={busy === "conflict"}
            >
              {busy === "conflict" && (
                <Loader2 className="size-4 animate-spin" />
              )}
              Send back to resolve conflict
            </Button>
            <Button
              variant="outline"
              onClick={() => setConflict(null)}
              disabled={busy === "conflict"}
            >
              Dismiss
            </Button>
          </div>
        </div>
      )}

      {/* Only when no dialog is open — otherwise the message belongs inside
          the dialog that produced it (us-40.3). */}
      {error && !approveOpen && !rejectOpen && !conflict && (
        <ErrorNote message={error} onDismiss={() => setError(null)} />
      )}
    </div>
  );
}
