"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  ExternalLink,
  FolderGit2,
  GitFork,
  Loader2,
  Plus,
  RotateCw,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useGithubRepos } from "@/lib/use-github-repos";
import { githubRepoUrl } from "@/lib/factory-git";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { GithubTokenConnect } from "./github-token-connect";

export type GithubConnection = {
  id: string;
  method: "app" | "pat";
  installation_id: number | null;
  account_login: string;
  account_type: string;
  pat_last4: string | null;
  pat_expires_at: string | null;
  repos: { full_name: string; default_branch: string }[] | null;
};

export type LinkedProject = {
  id: string;
  name: string;
  repo_full_name: string;
  archived_at: string | null;
};

type Repo = { full_name: string; default_branch: string };

// US-3.15: PAT rows carry their own expiry; surface a warning as it nears
// (or passes) the deadline so the manager can rotate it before access breaks.
function expiryWarning(expiresAt: string | null): string | null {
  if (!expiresAt) return null;
  const days = Math.floor(
    (new Date(expiresAt).getTime() - Date.now()) / 86_400_000
  );
  if (days < 0) return "Token expired — replace it to keep GitHub access.";
  if (days <= 30)
    return `Token expires in ${days} day${days === 1 ? "" : "s"}.`;
  return null;
}

export function GithubSettings({
  connections,
  projects,
}: {
  connections: GithubConnection[];
  projects: LinkedProject[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [connecting, setConnecting] = useState(false);
  const [disconnectingId, setDisconnectingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const status = searchParams.get("github");

  // US-2.25: repos the connection reaches, loaded from GitHub via the api
  // (cached — see useGithubRepos). Only "app" connections need this — "pat"
  // rows carry their granted repos directly on the row.
  const hasAppConnection = connections.some((c) => c.method === "app");
  const {
    repos,
    loading: reposLoading,
    error: reposError,
    reload: reloadRepos,
  } = useGithubRepos(hasAppConnection);

  useEffect(() => {
    if (status) router.refresh();
    // Only re-run when the query param itself changes, not on every refresh.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  // repo_full_name → linked project (prefer an active project over an
  // archived one when a repo has both).
  const projectByRepo = new Map<string, LinkedProject>();
  for (const p of projects) {
    const key = p.repo_full_name.toLowerCase();
    const existing = projectByRepo.get(key);
    if (!existing || (existing.archived_at && !p.archived_at)) {
      projectByRepo.set(key, p);
    }
  }
  const reposForOwner = (login: string) =>
    (repos ?? []).filter(
      (r) => r.full_name.split("/")[0].toLowerCase() === login.toLowerCase()
    );

  async function handleConnect() {
    setError(null);
    setConnecting(true);
    try {
      const { url } = await apiFetch("/api/v1/github/connect-url");
      window.location.href = url;
    } catch (e) {
      setError((e as Error).message);
      setConnecting(false);
    }
  }

  async function handleDisconnect(conn: GithubConnection) {
    const message =
      conn.method === "app"
        ? "Disconnect this GitHub installation? Projects linked to its repos will need to be reconnected."
        : "Remove this token connection? Its stored token is deleted and projects using its repos will need another connection.";
    if (
      !(await confirmDialog({
        title: "Disconnect GitHub?",
        description: message,
        confirmLabel: "Disconnect",
        destructive: true,
      }))
    )
      return;
    setError(null);
    setDisconnectingId(conn.id);
    try {
      await apiFetch(`/api/v1/github/connections/${conn.id}/disconnect`, {
        method: "POST",
      });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDisconnectingId(null);
    }
  }

  return (
    <div className="grid gap-3">
      {status === "connected" && (
        <p className="text-sm font-medium text-emerald-600 dark:text-emerald-400">
          GitHub connected.
        </p>
      )}
      {status === "error" && (
        <p className="text-sm font-medium text-destructive">
          Could not complete the GitHub connection. Try again.
        </p>
      )}

      {connections.length === 0 ? (
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            No GitHub account connected yet.
          </p>
          <div className="flex items-center gap-2">
            <Button onClick={handleConnect} disabled={connecting}>
              {connecting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <GitFork className="size-4" />
              )}
              Connect GitHub
            </Button>
            <GithubTokenConnect />
          </div>
        </div>
      ) : (
        <div className="grid gap-4">
          {connections.map((conn) => {
            const ownerRepos =
              conn.method === "app"
                ? reposForOwner(conn.account_login)
                : conn.repos ?? [];
            const linkedCount = ownerRepos.filter((r) =>
              projectByRepo.has(r.full_name.toLowerCase())
            ).length;
            const countKnown = conn.method === "pat" || repos !== null;
            const warning =
              conn.method === "pat"
                ? expiryWarning(conn.pat_expires_at)
                : null;
            return (
              <div key={conn.id} className="rounded-md border">
                <div className="border-b px-3 py-2">
                  <div className="flex items-center justify-between gap-3 text-sm">
                    <span className="flex items-center gap-2">
                      <GitFork className="size-4 text-muted-foreground" />
                      <span className="font-medium">
                        {conn.account_login}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        ({conn.account_type})
                      </span>
                      <Badge variant="secondary">
                        {conn.method === "app"
                          ? "GitHub App"
                          : `Token · …${conn.pat_last4}`}
                      </Badge>
                      {countKnown && (
                        <span className="text-xs text-muted-foreground">
                          · {ownerRepos.length} repositor
                          {ownerRepos.length === 1 ? "y" : "ies"} ·{" "}
                          {linkedCount} linked
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-2">
                      {conn.method === "app" && (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 gap-1 px-2 text-xs text-muted-foreground"
                          disabled={reposLoading}
                          onClick={reloadRepos}
                          title="Reload repositories from GitHub"
                        >
                          <RotateCw
                            className={`size-3 ${reposLoading ? "animate-spin" : ""}`}
                          />
                          Reload
                        </Button>
                      )}
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={disconnectingId === conn.id}
                        onClick={() => handleDisconnect(conn)}
                      >
                        {disconnectingId === conn.id && (
                          <Loader2 className="size-4 animate-spin" />
                        )}
                        Disconnect
                      </Button>
                    </span>
                  </div>
                  {conn.method === "pat" && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Acts as @{conn.account_login} — pushes and merges
                      attribute to this user.
                    </p>
                  )}
                  {warning && (
                    <p className="mt-1 text-xs font-medium text-amber-600 dark:text-amber-500">
                      {warning}{" "}
                      {conn.pat_expires_at &&
                        `(${new Date(
                          conn.pat_expires_at
                        ).toLocaleDateString()})`}
                    </p>
                  )}
                </div>

                <div className="px-3 py-2">
                  {conn.method === "app" && reposLoading && repos === null ? (
                    <p className="flex items-center gap-2 text-xs text-muted-foreground">
                      <Loader2 className="size-3.5 animate-spin" />
                      Loading repositories…
                    </p>
                  ) : conn.method === "app" && reposError && repos === null ? (
                    <p className="text-xs text-muted-foreground">
                      Couldn&apos;t load repositories ({reposError}). The
                      connection is still active.
                    </p>
                  ) : ownerRepos.length === 0 ? (
                    <p className="text-xs text-muted-foreground">
                      No repositories granted to the factory through this
                      connection.
                    </p>
                  ) : (
                    <ul className="grid gap-1">
                      {ownerRepos.map((r) => {
                        const project = projectByRepo.get(
                          r.full_name.toLowerCase()
                        );
                        return (
                          <li
                            key={r.full_name}
                            className="flex items-center justify-between gap-3 py-1 text-sm"
                          >
                            <a
                              href={githubRepoUrl(r.full_name)}
                              target="_blank"
                              rel="noreferrer"
                              title="Open on GitHub"
                              className="flex min-w-0 items-center gap-1 font-mono text-xs text-muted-foreground hover:text-foreground hover:underline"
                            >
                              <span className="truncate">{r.full_name}</span>
                              <ExternalLink className="size-3 shrink-0" />
                            </a>
                            {project ? (
                              <Link
                                href={`/projects/${project.id}`}
                                className="flex shrink-0 items-center gap-1 text-xs hover:underline"
                                title="Open the linked project"
                              >
                                <FolderGit2 className="size-3.5 text-muted-foreground" />
                                <span className="truncate">
                                  {project.name}
                                </span>
                                {project.archived_at && (
                                  <Badge
                                    variant="secondary"
                                    className="ml-1 font-normal"
                                  >
                                    Archived
                                  </Badge>
                                )}
                              </Link>
                            ) : (
                              <Link
                                href="/projects"
                                className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground hover:text-foreground hover:underline"
                                title="No factory project yet — create one"
                              >
                                <Plus className="size-3.5" />
                                No project
                              </Link>
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                  {conn.method === "app" && reposError && repos !== null && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      Showing cached repositories — reload failed ({reposError}).
                    </p>
                  )}
                </div>
              </div>
            );
          })}

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={handleConnect}
              disabled={connecting}
            >
              {connecting ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <GitFork className="size-4" />
              )}
              Add GitHub App
            </Button>
            <GithubTokenConnect />
          </div>
        </div>
      )}

      {error && (
        <p className="text-sm font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}
