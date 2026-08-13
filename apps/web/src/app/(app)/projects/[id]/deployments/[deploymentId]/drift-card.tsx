"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, GitCommitHorizontal, Loader2 } from "lucide-react";
import { apiCall } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export type CurrentRun = {
  source: string;
  branch: string | null;
  commit_sha: string | null;
  commit_message: string | null;
  zip_filename: string | null;
  finished_at: string | null;
  started_by_email: string;
  is_override: boolean | null;
} | null;

export type DriftCommit = {
  sha: string;
  message: string;
  author: string;
  date: string;
  /** US-1.48: the factory issue that shipped this commit, when known. */
  issue?: { id: string; title: string };
};

export type Drift =
  | { state: "never" | "zip" | "up-to-date" | "diverged" }
  | { state: "behind"; behind_by: number; commits: DriftCommit[] };

/** US-1.34: what is deployed right now, and what would ship next. The
 * deployed payload renders from local data; only drift needs GitHub. */
export function DriftCard({
  deploymentId,
  currentRun,
  externalTargetBranch,
  backTo,
}: {
  deploymentId: string;
  currentRun: CurrentRun;
  /** US-50.3: on an external deployment the comparison changes meaning rather
   * than disappearing — source branch against target branch, which needs no
   * run history, so the card answers before anything has ever run. */
  externalTargetBranch?: string | null;
  /** Query string (no leading `?`) so a linked commit's issue returns here. */
  backTo: string;
}) {
  const [drift, setDrift] = useState<Drift | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const external = !!externalTargetBranch;

  useEffect(() => {
    let cancelled = false;
    if (!currentRun && !external) return;
    (async () => {
      try {
        const d = (await apiCall(
          `/api/v1/deployments/${deploymentId}/drift`
        )) as Drift;
        if (!cancelled) setDrift(d);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [deploymentId, currentRun, external]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Currently deployed</CardTitle>
        <CardDescription>
          {external ? (
            <>
              What was last merged, and how far the source branch has moved
              past <span className="font-mono">{externalTargetBranch}</span>{" "}
              since — the commits the next run would carry.
            </>
          ) : (
            "Taken from the last successful run — the durable record of what is live on this environment."
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 text-sm">
        {!currentRun ? (
          <p className="text-muted-foreground">
            {external ? "Never merged." : "Never deployed."}
          </p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            {currentRun.source === "zip" ? (
              <>
                <Badge variant="secondary" className="font-mono font-normal">
                  zip {currentRun.zip_filename ?? ""}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  branch comparison n/a for zip payloads
                </span>
              </>
            ) : (
              <>
                <Badge variant="secondary" className="font-mono font-normal">
                  {currentRun.commit_sha?.slice(0, 7)}
                </Badge>
                {currentRun.commit_message && (
                  <span className="truncate">{currentRun.commit_message}</span>
                )}
              </>
            )}
            {currentRun.is_override && (
              // US-1.50/US-2.13: the live payload is a one-off ref override,
              // not the configured branch — flag it distinctly.
              <Badge className="gap-1 border-amber-300 bg-amber-100 font-normal text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300">
                <GitCommitHorizontal className="size-3" />
                one-off override: {currentRun.branch ?? currentRun.commit_sha?.slice(0, 7)}
              </Badge>
            )}
            <span className="text-xs text-muted-foreground">
              deployed{" "}
              {currentRun.finished_at &&
                new Date(currentRun.finished_at).toLocaleString(undefined, {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}{" "}
              by {currentRun.started_by_email}
            </span>
          </div>
        )}

        {(external || (currentRun && currentRun.source !== "zip")) && (
          <div className="text-sm">
            {error ? (
              <p className="text-xs text-destructive">
                Drift unavailable: {error}
              </p>
            ) : !drift ? (
              <p className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3 animate-spin" />
                {external
                  ? `Comparing ${externalTargetBranch} with the source branch…`
                  : "Comparing with the branch head…"}
              </p>
            ) : drift.state === "up-to-date" ? (
              <Badge className="bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300 border-transparent">
                {external ? "Nothing to merge" : "Up to date"}
              </Badge>
            ) : drift.state === "diverged" ? (
              <Badge variant="secondary">
                {external
                  ? "History diverged — the source branch is not ahead of the target"
                  : "History diverged — the deployed commit is no longer on the branch"}
              </Badge>
            ) : drift.state === "behind" ? (
              <div>
                <button
                  type="button"
                  onClick={() => setExpanded((v) => !v)}
                  className="inline-flex items-center gap-1 text-sm font-medium"
                >
                  {expanded ? (
                    <ChevronDown className="size-3.5" />
                  ) : (
                    <ChevronRight className="size-3.5" />
                  )}
                  {drift.behind_by} commit{drift.behind_by === 1 ? "" : "s"}{" "}
                  {external
                    ? `would land on ${externalTargetBranch}`
                    : "behind the branch head"}
                </button>
                {expanded && (
                  <ul className="mt-2 grid gap-1">
                    {drift.commits.map((c) => (
                      <li key={c.sha} className="flex items-start gap-2 text-xs">
                        <GitCommitHorizontal className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                        <span className="font-mono text-muted-foreground">
                          {c.sha.slice(0, 7)}
                        </span>
                        {c.issue && (
                          <a
                            href={`/issues/${c.issue.id}?${backTo}`}
                            className="shrink-0 rounded bg-violet-100 px-1 font-medium text-violet-700 hover:underline dark:bg-violet-950 dark:text-violet-300"
                            title={c.issue.title}
                          >
                            issue
                          </a>
                        )}
                        <span className="min-w-0 flex-1 truncate">{c.message}</span>
                        <span className="shrink-0 text-muted-foreground">
                          {c.author}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
