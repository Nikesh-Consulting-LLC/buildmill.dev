import { CheckCircle2, FlaskConical, XCircle } from "lucide-react";
import { cn } from "@/lib/utils";

export type TestEvidence = {
  command: string;
  exit_code: number | null;
  passed: number | null;
  failed: number | null;
  skipped: number | null;
  output_tail: string;
};

/** US-81.6: the worker's pre-submit test run, beside the diff. Worker-
 * reported — a review signal, not factory-observed proof — and labeled so.
 * Absence is itself information: the project declared a gate and nothing
 * was reported against it. */
export function TestEvidenceStrip({
  evidence,
}: {
  evidence: TestEvidence | null;
}) {
  if (!evidence) {
    return (
      <p className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm text-amber-700 dark:text-amber-400">
        <FlaskConical className="size-4 shrink-0" />
        No pre-submit test evidence was reported for this run — the worker
        didn&apos;t say whether the tests ran.
      </p>
    );
  }
  const ok = evidence.exit_code === 0;
  return (
    <div
      className={cn(
        "rounded-md border px-3 py-2 text-sm",
        ok ? "border-emerald-600/30 bg-emerald-500/5" : "border-destructive/40 bg-destructive/5"
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        {ok ? (
          <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
        ) : (
          <XCircle className="size-4 shrink-0 text-destructive" />
        )}
        <span className="font-medium">
          Pre-submit tests {ok ? "passed" : `failed (exit ${evidence.exit_code})`}
        </span>
        {(evidence.passed !== null || evidence.failed !== null) && (
          <span className="text-xs text-muted-foreground">
            {evidence.passed ?? 0} passed
            {evidence.failed ? `, ${evidence.failed} failed` : ""}
            {evidence.skipped ? `, ${evidence.skipped} skipped` : ""}
          </span>
        )}
        <code className="text-xs text-muted-foreground">{evidence.command}</code>
        <span
          className="ml-auto text-xs text-muted-foreground"
          title="Run in the worker's own workspace and self-reported — a signal, not factory-observed proof"
        >
          worker-reported
        </span>
      </div>
      {evidence.output_tail && (
        <details className="mt-1.5">
          <summary className="cursor-pointer select-none text-xs text-muted-foreground">
            Output tail
          </summary>
          <pre className="mt-1 max-h-64 overflow-auto rounded-md bg-muted/50 p-2 text-xs leading-5 whitespace-pre-wrap">
            {evidence.output_tail}
          </pre>
        </details>
      )}
    </div>
  );
}
