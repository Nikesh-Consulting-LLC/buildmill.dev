"use client";

import { useEffect, useState } from "react";
import { ExternalLink, GitBranch, GitPullRequest, Loader2 } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { DocsTreeCard } from "./docs-tree-card";
import { WireframesCard } from "./wireframes-card";
import { ReleaseBranchesCard } from "./release-branches-card";

type PullRequest = {
  number: number;
  title: string;
  author: string;
  url: string;
  updated_at: string;
};

// US-7.6: the GitHub tab holds the repo connection, its release branches +
// branching strategy, and its open PRs. Issue/Project sync is retired — all
// work-item requirements live in Build Mill.
export function GithubTab({
  projectId,
  repoFullName,
  defaultBranch,
  uatBranch,
  productionBranch,
  devBranchStrategy,
  docsTreeEnabled,
}: {
  projectId: string;
  repoFullName: string;
  defaultBranch: string;
  uatBranch: string | null;
  productionBranch: string | null;
  devBranchStrategy: string;
  docsTreeEnabled: boolean;
}) {
  const [pulls, setPulls] = useState<PullRequest[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const [owner, repo] = repoFullName.split("/");
    apiFetch(`/api/v1/github/repos/${owner}/${repo}/pulls`)
      .then((p) => setPulls(p))
      .catch((e: Error) => setError(e.message));
  }, [repoFullName]);

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Repository</CardTitle>
          <CardDescription>
            The connected GitHub repository — the source of truth for code.
            Work-item requirements live in Build Mill; GitHub Issue and Project
            sync has been retired.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3 text-sm">
          <a
            href={`https://github.com/${repoFullName}`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 font-mono text-xs underline-offset-4 hover:underline"
          >
            {repoFullName}
            <ExternalLink className="size-3" />
          </a>
          <Badge variant="secondary" className="gap-1 font-normal">
            <GitBranch className="size-3" />
            {defaultBranch}
          </Badge>
        </CardContent>
      </Card>

      <ReleaseBranchesCard
        projectId={projectId}
        repoFullName={repoFullName}
        uatBranch={uatBranch}
        productionBranch={productionBranch}
        devBranchStrategy={devBranchStrategy}
      />

      <DocsTreeCard projectId={projectId} enabled={docsTreeEnabled} />
      <WireframesCard projectId={projectId} />

      {error ? (
        <p className="text-sm text-muted-foreground">
          Couldn&apos;t load GitHub data ({error}). Check your GitHub connection
          in Settings.
        </p>
      ) : !pulls ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading…
        </p>
      ) : (
        <div>
          <h3 className="mb-2 text-sm font-medium">Open pull requests</h3>
          {!pulls.length ? (
            <EmptyState
              icon={GitPullRequest}
              title="No open PRs"
              description="Nothing open right now."
            />
          ) : (
            <ul className="grid gap-1.5">
              {pulls.map((p) => (
                <li key={p.number}>
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
                  >
                    <span className="truncate">
                      #{p.number} {p.title}
                    </span>
                    <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                      {p.author}
                      <ExternalLink className="size-3" />
                    </span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
