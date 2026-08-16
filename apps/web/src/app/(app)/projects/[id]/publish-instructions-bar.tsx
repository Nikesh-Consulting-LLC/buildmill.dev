"use client";

// us-99.4 (the UI half, built with the Phase 100 close): an edit that has not
// reached the repository says so — and the button that gets it there sits
// beside the words.
//
// The endpoint (`GET /projects/{id}/instructions/status`) already computes
// what a publish would write and remove and whether that differs from what
// was last committed. This is the badge, the publish button and the standing
// ownership line the story asked for; nothing here decides anything the
// server did not already say.

import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, RefreshCw } from "lucide-react";

import { apiFetch } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { toastError, toastSuccess } from "@/components/ui/toast";

type Status = {
  unpublished: boolean;
  has_repo: boolean;
  files: string[];
  deletes: string[];
  published_at: string | null;
  published_sha: string | null;
  ownership_notice: string;
};

type PublishResult = {
  commit_sha: string | null;
  unchanged: boolean;
  agents_md: { html_url: string };
};

export function PublishInstructionsBar({
  projectId,
  repoFullName,
  canPublish,
  /** Bumped by the parent when the document is saved, so the badge
   * refreshes without a page load. */
  refreshKey = 0,
}: {
  projectId: string;
  repoFullName: string | null;
  canPublish: boolean;
  refreshKey?: number;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = (await apiFetch(
        `/api/v1/projects/${projectId}/instructions/status`,
      )) as Status;
      setStatus(body);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not read publish status.");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function publish() {
    if (!status) return;
    const ok = await confirmDialog({
      title: "Publish instructions to the repository?",
      description:
        `${status.files.length} file${status.files.length === 1 ? "" : "s"} written` +
        (status.deletes.length
          ? `, ${status.deletes.length} removed`
          : "") +
        ` on ${repoFullName ?? "the default branch"}. ${status.ownership_notice}`,
      confirmLabel: "Publish",
    });
    if (!ok) return;
    setPublishing(true);
    try {
      const res = (await apiFetch(
        `/api/v1/projects/${projectId}/guidelines/save-instructions`,
        { method: "POST" },
      )) as PublishResult;
      if (res.unchanged) toastSuccess("Nothing to publish", "The repository already matches.");
      else
        toastSuccess(
          "Published",
          `Committed ${res.commit_sha?.slice(0, 7) ?? ""} — AGENTS.md, CLAUDE.md and .buildmill/.`,
        );
      await load();
    } catch (e) {
      toastError("Publish failed", e instanceof Error ? e.message : "unknown error");
    } finally {
      setPublishing(false);
    }
  }

  const changed = status ? status.files.length + status.deletes.length : 0;

  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        {loading && !status ? (
          <Badge variant="outline" className="gap-1 text-muted-foreground">
            <Loader2 className="size-3 animate-spin" /> checking repository
          </Badge>
        ) : error ? (
          <Badge variant="outline" className="text-destructive">
            {error}
          </Badge>
        ) : status && !status.has_repo ? (
          <Badge variant="outline" className="text-muted-foreground">
            No repository linked — nothing to publish to
          </Badge>
        ) : status?.unpublished ? (
          <Badge className="bg-amber-500/15 text-amber-800 hover:bg-amber-500/15 dark:text-amber-200">
            Unpublished — {changed} file{changed === 1 ? "" : "s"} differ
          </Badge>
        ) : (
          <Badge variant="outline" className="text-muted-foreground">
            Published
            {status?.published_sha ? ` · ${status.published_sha.slice(0, 7)}` : ""}
          </Badge>
        )}
        {canPublish && status?.has_repo ? (
          <Button
            size="sm"
            variant={status.unpublished ? "default" : "outline"}
            disabled={publishing || loading}
            onClick={() => void publish()}
          >
            {publishing ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <GitBranch className="size-4" />
            )}
            Publish to repository
          </Button>
        ) : null}
        <Button
          variant="ghost"
          size="icon-sm"
          title="Re-check"
          aria-label="Re-check publish status"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshCw className="size-3.5" />
        </Button>
      </div>
      {status?.has_repo ? (
        <p className="text-xs text-muted-foreground">
          {status.published_at
            ? `Last published ${new Date(status.published_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}` +
              (status.published_sha && repoFullName
                ? ` · `
                : "")
            : "Never published to this repository. "}
          {status.published_sha && repoFullName ? (
            <a
              className="underline underline-offset-2 hover:text-foreground"
              href={`https://github.com/${repoFullName}/commit/${status.published_sha}`}
              target="_blank"
              rel="noreferrer"
            >
              {status.published_sha.slice(0, 7)}
            </a>
          ) : null}
          {status.published_at ? ". " : ""}
          {status.ownership_notice}
        </p>
      ) : null}
    </div>
  );
}
