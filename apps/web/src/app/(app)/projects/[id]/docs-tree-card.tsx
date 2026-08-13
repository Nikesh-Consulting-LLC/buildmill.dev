"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { BookOpen, Loader2, RefreshCw } from "lucide-react";
import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** US-13.4 / US-15.1: the factory-owned docs tree — approved PRDs, stories
 * and plans written into the repo under docs/factory/ on every approval.
 * On by default (US-15.1) so the repo and GitHub stay in sync with what has
 * been approved; a project with sensitive requirements can turn it off. */
export function DocsTreeCard({
  projectId,
  enabled,
}: {
  projectId: string;
  enabled: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function setEnabled(next: boolean) {
    setBusy(true);
    setError(null);
    setMessage(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("projects")
      .update({ docs_tree_enabled: next })
      .eq("id", projectId);
    if (dbError) {
      setError(dbError.message);
      setBusy(false);
      return;
    }
    if (next) {
      await syncNow(true);
    } else {
      setMessage("Disabled — existing files stay in the repo; nothing new is written.");
      setBusy(false);
    }
    router.refresh();
  }

  async function syncNow(fromEnable = false) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = (await apiCall(
        `/api/v1/projects/${projectId}/docs-tree/sync`,
        { method: "POST" }
      )) as { commit_sha?: string; unchanged?: boolean; skipped?: string };
      if (result.skipped) setMessage(`Skipped: ${result.skipped}`);
      else if (result.unchanged)
        setMessage("Already up to date — nothing to commit.");
      else
        setMessage(
          `${fromEnable ? "Enabled and scaffolded" : "Synced"} — commit ${(
            result.commit_sha ?? ""
          ).slice(0, 7)}.`
        );
    } catch (e) {
      setError((e as Error).message);
    }
    setBusy(false);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <BookOpen className="size-4 text-muted-foreground" />
          Docs tree in the repo
        </CardTitle>
        <CardDescription>
          Build Mill writes each <em>approved</em> PRD, story and plan into the
          repo under <code>docs/factory/</code> (with an index and an AGENTS.md
          pointer) on every approval, so the repo stays in sync with what has
          been approved. The app owns that tree — outside edits are overwritten.
          On by default; turn it off if requirement text should not be visible
          to everyone with repo access.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          variant={enabled ? "outline" : "default"}
          disabled={busy}
          onClick={() => setEnabled(!enabled)}
        >
          {busy && <Loader2 className="size-3.5 animate-spin" />}
          {enabled ? "Disable" : "Enable & scaffold"}
        </Button>
        {enabled && (
          <Button
            size="sm"
            variant="outline"
            disabled={busy}
            onClick={() => syncNow()}
          >
            <RefreshCw className="size-3.5" />
            Sync now
          </Button>
        )}
        {message && <p className="text-sm text-muted-foreground">{message}</p>}
        {error && <p className="text-sm text-destructive">{error}</p>}
      </CardContent>
    </Card>
  );
}
