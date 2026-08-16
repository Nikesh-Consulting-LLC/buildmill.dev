"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Archive, ArchiveRestore, Loader2, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { canMarkFixed } from "@/lib/mark-fixed";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { MarkFixedButton } from "@/components/mark-fixed-button";

const RUNNING_STATUSES = ["queued", "running"];

export function IssueActions({
  issueId,
  title,
  type,
  status,
  abandonedAt,
}: {
  issueId: string;
  title: string;
  /** us-107.1: a feature is never marked fixed by hand. */
  type: string;
  status: string;
  abandonedAt: string | null;
}) {
  const router = useRouter();
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const blocked = RUNNING_STATUSES.includes(status);

  async function toggleAbandon() {
    setError(null);
    setToggling(true);
    const supabase = createClient();
    try {
      const { error: dbError } = await supabase
        .from("issues")
        .update({ abandoned_at: abandonedAt ? null : new Date().toISOString() })
        .eq("id", issueId);
      if (dbError) {
        setError(dbError.message);
        return;
      }
      router.refresh();
    } finally {
      setToggling(false);
    }
  }

  async function handleDelete() {
    const supabase = createClient();
    if (blocked) {
      // Queued/running: the guard trigger rejects a plain delete — the user
      // confirmed the force warning, so use the force_delete_issues RPC.
      const { data, error: rpcError } = await supabase.rpc(
        "force_delete_issues",
        { p_issue_ids: [issueId] }
      );
      if (rpcError) throw new Error(rpcError.message);
      if ((data ?? 0) < 1) {
        throw new Error("The work item could not be deleted — refresh and retry.");
      }
    } else {
      const { error: dbError } = await supabase
        .from("issues")
        .delete()
        .eq("id", issueId);
      if (dbError) throw new Error(dbError.message);
    }
    router.push(`/issues`);
  }

  return (
    <div className="flex shrink-0 items-center gap-2">
      {error && <p className="text-xs font-medium text-destructive">{error}</p>}
      {blocked && (
        <p className="text-xs text-muted-foreground">
          A run is queued or running — abandoning is blocked; deleting will
          force-delete.
        </p>
      )}
      {/* us-107.1: sits before Abandon deliberately — it is the outcome the
          manager wants more often, and the two read as the pair they are. */}
      {canMarkFixed(type, status, abandonedAt) && (
        <MarkFixedButton issueId={issueId} />
      )}
      <Button
        variant="outline"
        size="sm"
        onClick={toggleAbandon}
        disabled={toggling || (blocked && !abandonedAt)}
      >
        {toggling ? (
          <Loader2 className="size-4 animate-spin" />
        ) : abandonedAt ? (
          <ArchiveRestore className="size-4" />
        ) : (
          <Archive className="size-4" />
        )}
        {abandonedAt ? "Restore" : "Abandon"}
      </Button>
      <ConfirmDialog
        trigger={
          <Button variant="outline" size="sm">
            <Trash2 className="size-4" />
            Delete
          </Button>
        }
        title={blocked ? `Force delete "${title}"?` : `Delete "${title}"?`}
        description={
          blocked
            ? "This work item is queued or running. Force-deleting permanently removes it and its events, runs, and reviews — the run in flight is discarded and a worker still on it will fail to hand back. Linked test cases and documents are detached, not deleted. This can't be undone."
            : "This permanently deletes the work item and its events, runs, and reviews. This can't be undone."
        }
        confirmLabel={blocked ? "Force delete work item" : "Delete work item"}
        requireText={blocked ? "force delete" : undefined}
        onConfirm={handleDelete}
      />
    </div>
  );
}
