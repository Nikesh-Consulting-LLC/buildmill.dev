"use client";

// US-85.1: Prepare Agent Workspace. The button on a Project access row opens
// this popup and starts (or reattaches to) a background preparation job on
// the agent's runner: directory, latest code, agent + MCP config, tool
// servers, verification. The job is a `workspace_prep_jobs` row written by
// the API as the runner streams progress over the control socket; this
// dialog only *watches* it over Realtime — closing the popup cancels
// nothing, and reopening attaches to the same job (AC3/AC6).

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CheckCircle2,
  Circle,
  Loader2,
  Wrench,
  XCircle,
} from "lucide-react";
import { apiCall, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type PrepStep = {
  key: string;
  label: string;
  status: "pending" | "running" | "ok" | "failed";
  detail: string;
};

export type PrepJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  steps: PrepStep[];
  error: string | null;
  prepared_commit: string | null;
  finished_at: string | null;
};

// Rendered while the job row hasn't arrived yet, so the popup never opens
// empty. Keys/labels mirror workspace_prep.STEPS on the API.
const PLACEHOLDER_STEPS: PrepStep[] = [
  { key: "invoke", label: "Invoke agent", status: "pending", detail: "" },
  { key: "workdir", label: "Prepare working directory", status: "pending", detail: "" },
  { key: "fetch", label: "Fetch latest code", status: "pending", detail: "" },
  { key: "configure", label: "Configure agent settings", status: "pending", detail: "" },
  { key: "mcp", label: "Install / configure MCP servers", status: "pending", detail: "" },
  { key: "tools", label: "Register tool servers", status: "pending", detail: "" },
  { key: "checks", label: "Check configuration", status: "pending", detail: "" },
  { key: "settings", label: "Check agent settings", status: "pending", detail: "" },
];

function StepIcon({ status }: { status: PrepStep["status"] }) {
  if (status === "ok")
    return <CheckCircle2 className="size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />;
  if (status === "failed")
    return <XCircle className="size-4 shrink-0 text-red-600 dark:text-red-400" />;
  if (status === "running")
    return <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />;
  return <Circle className="size-4 shrink-0 text-muted-foreground/40" />;
}

export function PrepareWorkspaceButton({
  workerId,
  projectId,
  projectName,
  agentName,
  runnerLive,
  onFinished,
}: {
  workerId: string;
  projectId: string;
  projectName: string;
  agentName?: string;
  /** AC1: without a connected runner there is no machine to prepare. */
  runnerLive: boolean;
  /** Lets the row refresh its "Prepared … ago" caption (AC5). */
  onFinished?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [job, setJob] = useState<PrepJob | null>(null);
  const [startError, setStartError] = useState<string | null>(null);
  const startedRef = useRef(false);

  const attach = useCallback((jobId: string) => {
    const supabase = createClient();
    let cancelled = false;
    supabase
      .from("workspace_prep_jobs")
      .select("id, status, steps, error, prepared_commit, finished_at")
      .eq("id", jobId)
      .maybeSingle()
      .then(({ data }) => {
        if (!cancelled && data) setJob(data as unknown as PrepJob);
      });
    const channel = supabase
      .channel(`prep-job-${jobId}`)
      .on(
        "postgres_changes",
        {
          event: "UPDATE",
          schema: "public",
          table: "workspace_prep_jobs",
          filter: `id=eq.${jobId}`,
        },
        (payload) => {
          if (!cancelled) setJob(payload.new as unknown as PrepJob);
        }
      )
      .subscribe();
    return () => {
      cancelled = true;
      void supabase.removeChannel(channel);
    };
  }, []);

  // Opening starts the job (or reattaches to the live one). Closing only
  // stops watching.
  useEffect(() => {
    if (!open || startedRef.current) return;
    startedRef.current = true;
    let detach: (() => void) | undefined;
    let cancelled = false;

    (async () => {
      try {
        const res = await apiCall(
          `/api/v1/runner/${workerId}/prepare-workspace-job`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ project_id: projectId }),
          }
        );
        if (!cancelled) detach = attach(res.job_id);
      } catch (e) {
        // 409 with a job_id = one is already running; watch it instead (AC6).
        const existing =
          e instanceof ApiError &&
          typeof e.detail === "object" &&
          e.detail !== null
            ? (e.detail as { job_id?: string }).job_id
            : undefined;
        if (existing) {
          if (!cancelled) detach = attach(existing);
        } else if (!cancelled) {
          setStartError(
            e instanceof ApiError ? String(e.message) : "request failed"
          );
        }
      }
    })();

    return () => {
      cancelled = true;
      detach?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (!next) {
      startedRef.current = false;
      setStartError(null);
      if (job && (job.status === "succeeded" || job.status === "failed")) {
        onFinished?.();
      }
      setJob(null);
    }
  }

  const steps = job?.steps?.length ? job.steps : PLACEHOLDER_STEPS;
  const failedStep = steps.find((s) => s.status === "failed");
  const running = !job || job.status === "queued" || job.status === "running";

  return (
    <>
      <button
        type="button"
        disabled={!runnerLive}
        title={
          runnerLive
            ? undefined
            : "The agent's runner is not connected — there is no machine to prepare"
        }
        onClick={(e) => {
          // Inside the row's <label>: don't toggle the access checkbox.
          e.preventDefault();
          e.stopPropagation();
          setOpen(true);
        }}
        className="flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50"
      >
        <Wrench className="size-3" />
        Prepare workspace
      </button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              Prepare workspace — {projectName}
            </DialogTitle>
            <DialogDescription>
              {agentName ? `${agentName} is getting` : "Getting"} its working
              directory ready for {projectName}: latest code, agent + MCP
              configuration, tool servers, and a verification pass. This runs
              on the agent&apos;s machine — closing this popup does not stop
              it.
            </DialogDescription>
          </DialogHeader>

          {startError ? (
            <p className="text-sm font-medium text-destructive">{startError}</p>
          ) : (
            <ul className="grid gap-2">
              {steps.map((s) => (
                <li key={s.key} className="flex items-start gap-2 text-sm">
                  <span className="mt-0.5">
                    <StepIcon status={s.status} />
                  </span>
                  <span className="min-w-0">
                    <span
                      className={cn(
                        s.status === "pending" && "text-muted-foreground"
                      )}
                    >
                      {s.label}
                    </span>
                    {s.detail && (
                      <span className="block break-words text-xs text-muted-foreground">
                        {s.detail}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {!startError && (
            <p
              className={cn(
                "text-sm font-medium",
                job?.status === "succeeded" &&
                  "text-emerald-600 dark:text-emerald-400",
                job?.status === "failed" && "text-destructive",
                running && "text-muted-foreground"
              )}
            >
              {job?.status === "succeeded" && (
                <>
                  Workspace ready
                  {job.prepared_commit
                    ? ` — ${job.prepared_commit.slice(0, 10)}`
                    : ""}
                </>
              )}
              {job?.status === "failed" && (
                <>
                  Preparation failed
                  {failedStep ? ` at “${failedStep.label}”` : ""}
                  {job.error ? ` — ${job.error}` : ""}
                </>
              )}
              {running && "Preparing…"}
            </p>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
