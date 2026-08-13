"use client";

import { useEffect, useState } from "react";
import { GitCommitHorizontal, Loader2, Rocket } from "lucide-react";
import { apiCall } from "@/lib/api";
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
import type { Drift } from "./drift-card";

/** Run confirmation (US-1.32): says exactly what will happen before it does. */
export function RunDeploymentDialog({
  deploymentId,
  deploymentName,
  projectId,
  branch,
  externalTargetBranch,
  serverLabel,
  targetFolder,
  sourceFolder,
  isProtected,
  configChangedAreas,
  disabled,
  disabledReason,
  onStarted,
}: {
  deploymentId: string;
  deploymentName: string;
  projectId: string;
  branch: string;
  /** US-50.2: set on an external deployment — the run merges into this
   * branch and stops there, so the dialog must not promise a transfer. */
  externalTargetBranch?: string | null;
  serverLabel: string;
  targetFolder: string;
  sourceFolder?: string;
  /** US-1.41: typed confirmation + warning styling. */
  isProtected?: boolean;
  /** US-1.49: config areas changed since the last successful run. */
  configChangedAreas?: string[];
  disabled?: boolean;
  disabledReason?: string;
  onStarted: (runId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [typed, setTyped] = useState("");
  const typeBlocked = !!isProtected && typed !== deploymentName;
  // US-1.34: the dialog answers "what exactly will ship?"
  const [drift, setDrift] = useState<Drift | null>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setDrift(null);
    (async () => {
      try {
        const d = (await apiCall(
          `/api/v1/deployments/${deploymentId}/drift`
        )) as Drift;
        if (!cancelled) setDrift(d);
      } catch {
        if (!cancelled) setDrift(null); // degrade silently — deploy still works
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, deploymentId]);

  async function handleRun() {
    setError(null);
    setBusy(true);
    try {
      const resp = (await apiCall(`/api/v1/deployments/${deploymentId}/run`, {
        method: "POST",
      })) as { run_id: string };
      setOpen(false);
      onStarted(resp.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={<Button size="sm" disabled={disabled} title={disabledReason} />}
      >
        <Rocket className="size-3.5" />
        Run
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Run &quot;{deploymentName}&quot;?</DialogTitle>
          <DialogDescription>
            {externalTargetBranch ? (
              <>
                This merges the current head of{" "}
                <span className="font-mono">{branch}</span> into{" "}
                <span className="font-mono">{externalTargetBranch}</span> on
                GitHub — through a pull request, with a merge commit — and
                stops there. Nothing is copied to any machine. Whatever
                pipeline watches{" "}
                <span className="font-mono">{externalTargetBranch}</span> takes
                it from there; the factory neither triggers nor watches it.
              </>
            ) : (
              <>
                This fetches the current head of{" "}
                <span className="font-mono">{branch}</span> from GitHub
                {sourceFolder ? (
                  <>
                    {" "}
                    (source folder{" "}
                    <span className="font-mono">{sourceFolder}</span>)
                  </>
                ) : null}
                , transfers the files to{" "}
                <span className="font-mono">{serverLabel}</span> directly into{" "}
                <span className="font-mono">{targetFolder}</span> (existing
                files with the same paths are overwritten), then runs the
                deployment script there.
              </>
            )}
          </DialogDescription>
        </DialogHeader>
        {drift?.state === "up-to-date" && (
          <p className="text-sm text-muted-foreground">
            {externalTargetBranch
              ? `${externalTargetBranch} already contains this commit — the run will succeed saying there was nothing to merge.`
              : "The branch head is already deployed — this re-ships the same commit."}
          </p>
        )}
        {drift?.state === "behind" && (
          <div className="text-sm">
            {(() => {
              const issues = drift.commits
                .map((c) => c.issue)
                .filter((t): t is NonNullable<typeof t> => !!t)
                .filter((t, i, arr) => arr.findIndex((x) => x.id === t.id) === i);
              return issues.length > 0 ? (
                <p className="mb-1">
                  <span className="font-medium">Ships:</span>{" "}
                  {issues.map((t, i) => (
                    <span key={t.id}>
                      {i > 0 && ", "}
                      <a
                        href={`/issues/${t.id}?from=${encodeURIComponent(`/projects/${projectId}/deployments/${deploymentId}`)}&fromLabel=${encodeURIComponent(deploymentName)}`}
                        className="text-violet-700 underline-offset-4 hover:underline dark:text-violet-300"
                      >
                        {t.title}
                      </a>
                    </span>
                  ))}
                </p>
              ) : null;
            })()}
            <p className="mb-1 font-medium">
              {externalTargetBranch ? "Merges" : "Ships"} {drift.behind_by}{" "}
              commit{drift.behind_by === 1 ? "" : "s"}:
            </p>
            <ul className="grid max-h-40 gap-1 overflow-y-auto">
              {drift.commits.map((c) => (
                <li key={c.sha} className="flex items-start gap-2 text-xs">
                  <GitCommitHorizontal className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                  <span className="font-mono text-muted-foreground">
                    {c.sha.slice(0, 7)}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{c.message}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {configChangedAreas && configChangedAreas.length > 0 && (
          <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300">
            Configuration changed since the last successful run:{" "}
            {configChangedAreas.join(", ")} — review the History tab if that
            surprises you.
          </p>
        )}
        {isProtected && (
          <div className="grid gap-1.5 rounded-md border border-destructive/40 bg-destructive/5 p-3">
            <p className="text-sm font-medium text-destructive">
              Protected deployment — type{" "}
              <span className="font-mono">{deploymentName}</span> to confirm:
            </p>
            <input
              className="h-8 rounded-md border bg-background px-2 font-mono text-sm"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              placeholder={deploymentName}
              autoComplete="off"
              spellCheck={false}
            />
          </div>
        )}
        {error && <p className="text-sm font-medium text-destructive">{error}</p>}
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={isProtected ? "destructive" : "default"}
            onClick={handleRun}
            disabled={busy || typeBlocked}
          >
            {busy && <Loader2 className="size-4 animate-spin" />}
            {externalTargetBranch ? "Merge now" : "Deploy now"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
