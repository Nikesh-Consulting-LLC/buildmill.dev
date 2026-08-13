"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Sparkles } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { toastError, toastSuccess } from "@/components/ui/toast";

/** US-44.1: put an agent on fleshing out one story against the codebase.
 *
 * The story a breakdown wrote was inferred from a PRD by something that had
 * never read the repository, and the first pass anyone makes over it with the
 * source open is the plan run — at $5–15 each. This is the cheap pass in
 * front of that.
 *
 * Never automatic: nothing queues it but a manager pressing it. */
export function ElaborateButton({
  issueId,
  status,
  inFlight,
  hasDraft,
}: {
  issueId: string;
  status: string;
  inFlight: boolean;
  hasDraft: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  // Worth doing while the story is still ahead of its build. Past that the
  // plan is written and rewriting the story underneath it is a send-back, not
  // an elaboration.
  if (!["draft", "ready", "failed", "needs-fixes"].includes(status)) return null;

  if (hasDraft) {
    return (
      <Button
        type="button"
        variant="outline"
        size="sm"
        render={<Link href={`/review/${issueId}`} />}
      >
        <Sparkles className="size-4" />
        Review the proposal
      </Button>
    );
  }

  if (inFlight) {
    return (
      <Button type="button" variant="ghost" size="sm" disabled>
        <Loader2 className="size-4 animate-spin" />
        An agent is fleshing this out
      </Button>
    );
  }

  async function start() {
    setBusy(true);
    try {
      await apiCall(`/api/v1/issues/${issueId}/elaboration/dispatch`, {
        method: "POST",
      });
      toastSuccess("An agent will read the repo and propose a rewrite");
      router.refresh();
    } catch (e) {
      toastError(
        e instanceof ApiError
          ? String(e.message)
          : (e as Error).message || "Could not start it"
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      disabled={busy}
      onClick={start}
      title="Have an agent read the codebase and propose a sharper story — before a plan run spends real money on this one"
    >
      {busy ? (
        <Loader2 className="size-4 animate-spin" />
      ) : (
        <Sparkles className="size-4" />
      )}
      Flesh out with an agent
    </Button>
  );
}
