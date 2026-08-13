"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, Undo2 } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { MarkdownView } from "@/components/markdown-view";
import { toastError, toastSuccess } from "@/components/ui/toast";

export type ElaborationProposal = {
  story: string;
  acceptance_criteria: string[];
  open_questions: string[];
  proposes_change: boolean;
};

/** US-44.1: the elaboration gate.
 *
 * Current text on the left, the agent's proposal on the right, so the manager
 * is judging a rewrite rather than reading one. Approving applies the text —
 * and, on a story still at `draft`, curates it: a manager who has read the
 * proposal closely enough to accept it has already done what us-15.3's gate
 * asks for, and a second click meaning the same thing is friction us-41.2
 * spent a story removing. */
export function ElaborationReview({
  issueId,
  proposal,
  currentBody,
  currentCriteria,
  isDraft,
}: {
  issueId: string;
  proposal: ElaborationProposal;
  currentBody: string;
  currentCriteria: string[];
  isDraft: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [sendingBack, setSendingBack] = useState(false);
  const [comment, setComment] = useState("");

  async function decide(path: string, payload?: Record<string, unknown>) {
    setBusy(true);
    try {
      await apiCall(`/api/v1/issues/${issueId}/elaboration/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload ?? {}),
      });
      toastSuccess(
        path === "approve"
          ? isDraft
            ? "Applied and curated — ready to plan"
            : "Applied to the story"
          : "Sent back — a fresh pass is queued"
      );
      router.push(`/issues/${issueId}`);
      router.refresh();
    } catch (e) {
      toastError(
        e instanceof ApiError
          ? String(e.message)
          : (e as Error).message || "Could not record that"
      );
    } finally {
      setBusy(false);
    }
  }

  // "This story is fine as written" is a real answer, and it has no
  // before/after to show — only a decision to dismiss.
  if (!proposal.proposes_change) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            The agent proposes no change
          </CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-sm text-muted-foreground">
            It read the story against the repository and found it good enough
            to plan as written. Nothing has been applied.
          </p>
          {proposal.open_questions.length > 0 ? (
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                Open questions
              </p>
              <ul className="list-inside list-disc text-sm">
                {proposal.open_questions.map((q, i) => (
                  <li key={i}>{q}</li>
                ))}
              </ul>
            </div>
          ) : null}
          <div className="flex justify-end">
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => setSendingBack(true)}
            >
              Send it back with direction
            </Button>
          </div>
          {sendingBack ? (
            <div className="flex flex-col gap-2">
              <Textarea
                rows={3}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="What did you want it to look at?"
              />
              <div className="flex justify-end">
                <Button
                  size="sm"
                  disabled={busy || !comment.trim()}
                  onClick={() => decide("send-back", { comment })}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                  Send back
                </Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {proposal.open_questions.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              The agent could not settle these
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="list-inside list-disc text-sm">
              {proposal.open_questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base text-muted-foreground">
              Story now
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {currentBody ? (
              <MarkdownView>{currentBody}</MarkdownView>
            ) : (
              <p className="text-sm text-muted-foreground">No story text.</p>
            )}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                Acceptance criteria ({currentCriteria.length})
              </p>
              <ul className="list-inside list-disc text-sm">
                {currentCriteria.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>

        <Card className="border-primary/40">
          <CardHeader>
            <CardTitle className="text-base">Proposed</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {proposal.story ? (
              <MarkdownView>{proposal.story}</MarkdownView>
            ) : (
              <p className="text-sm text-muted-foreground">
                Story text unchanged.
              </p>
            )}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                Acceptance criteria ({proposal.acceptance_criteria.length})
              </p>
              <ul className="list-inside list-disc text-sm">
                {proposal.acceptance_criteria.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 pt-6">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted-foreground">
              {isDraft
                ? "Approving applies this text and curates the story — it becomes ready to plan."
                : "Approving applies this text. The story keeps its current status."}
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => setSendingBack((v) => !v)}
              >
                <Undo2 className="size-4" />
                Send back
              </Button>
              <Button
                size="sm"
                disabled={busy}
                onClick={() => decide("approve")}
              >
                {busy ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Check className="size-4" />
                )}
                {isDraft ? "Approve and curate" : "Approve"}
              </Button>
            </div>
          </div>
          {sendingBack ? (
            <div className="flex flex-col gap-2">
              <Textarea
                rows={3}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="What should the next pass do differently? Required — it is carried into the run as feedback."
              />
              <div className="flex justify-end">
                <Button
                  size="sm"
                  disabled={busy || !comment.trim()}
                  onClick={() => decide("send-back", { comment })}
                >
                  {busy ? <Loader2 className="size-4 animate-spin" /> : null}
                  Send back and re-run
                </Button>
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
