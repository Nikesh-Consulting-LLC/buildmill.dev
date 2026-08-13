"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { ChevronDown, ChevronUp, FlaskConical, Loader2 } from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

/** US-13.11: send the submitted branch to a worker that CAN execute the
 * test cases — a `test` pool run offered only to workers granted the
 * `test` capability. Explicit dispatch; results land on this same
 * review surface through report_test_results. */
export function SendForVerification({
  issueId,
  activeTestRun,
  hasTestCases,
  projectId,
}: {
  issueId: string;
  activeTestRun: boolean;
  /** No active test_cases means a dispatched run could never legally
   * complete (submit_test_run refuses with zero results reported) — block
   * before that dead end instead of letting it queue forever. */
  hasTestCases: boolean;
  projectId?: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showInstructions, setShowInstructions] = useState(false);
  const [instructions, setInstructions] = useState("");

  async function dispatch() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await apiCall(`/api/v1/issues/${issueId}/test-run/dispatch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          instructions: instructions.trim() || undefined,
        }),
      });
      setMessage("Verification run queued — it shows in the pool.");
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError ? e.message : (e as Error).message
      );
    } finally {
      setBusy(false);
    }
  }

  if (!hasTestCases && !activeTestRun) {
    return (
      <div className="flex flex-col items-end gap-1">
        <Button variant="outline" size="sm" disabled title="This issue has no active test cases — author some first">
          <FlaskConical className="size-3.5" />
          Send for verification
        </Button>
        <span className="text-xs text-muted-foreground">
          No test cases yet —{" "}
          <Link
            href={projectId ? `/tests?project=${projectId}` : "/tests"}
            className="underline-offset-2 hover:underline"
          >
            author some
          </Link>{" "}
          before sending for verification.
        </span>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-end gap-1">
      {!activeTestRun && (
        <button
          type="button"
          onClick={() => setShowInstructions((v) => !v)}
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          {showInstructions ? (
            <ChevronUp className="size-3" />
          ) : (
            <ChevronDown className="size-3" />
          )}
          Anything specific to check? (optional)
        </button>
      )}
      {!activeTestRun && showInstructions && (
        <Textarea
          rows={3}
          placeholder="e.g. focus on cases 3 and 5, also check the mobile layout doesn't break."
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          className="w-72"
        />
      )}
      <Button
        variant="outline"
        size="sm"
        disabled={busy || activeTestRun}
        onClick={dispatch}
        title={
          activeTestRun
            ? "A verification run is already queued or running"
            : "Dispatch a test run over the submitted branch to a worker granted the test capability"
        }
      >
        {busy ? (
          <Loader2 className="size-3.5 animate-spin" />
        ) : (
          <FlaskConical className="size-3.5" />
        )}
        {activeTestRun ? "Verification in flight" : "Send for verification"}
      </Button>
      {message && (
        <span className="text-xs text-muted-foreground">{message}</span>
      )}
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
