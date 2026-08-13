"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { CheckCircle2, Loader2, RotateCcw, Trash2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { closeEpic, reopenEpic } from "@/lib/close-epic";
import type { EpicStatus } from "@/lib/epics";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

export function EpicActions({
  orgId,
  epicId,
  projectId,
  title,
  status,
  active = false,
  memberCount,
  redirectOnDelete = false,
}: {
  orgId: string;
  epicId: string;
  projectId: string;
  title: string;
  status: EpicStatus;
  /** US-71.1: closing the active epic must drop the flag (epics_active_open)
   * and hand the default to the newest remaining open epic. */
  active?: boolean;
  memberCount: number;
  redirectOnDelete?: boolean;
}) {
  const router = useRouter();
  const [toggling, setToggling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Completing an epic is always a manual action — the rollup only ever
  // nudges, it never flips the status on its own. US-71.1: the shared
  // helper handles the active-flag hand-off that used to make this button
  // violate epics_active_open on the active epic.
  async function toggleStatus() {
    setError(null);
    setToggling(true);
    const supabase = createClient();
    try {
      const dbError =
        status === "open"
          ? await closeEpic(supabase, { id: epicId, projectId, active })
          : await reopenEpic(supabase, epicId);
      if (dbError) {
        setError(dbError);
        return;
      }
      router.refresh();
    } finally {
      setToggling(false);
    }
  }

  // The epic goes, its issues stay: unassign every member first, then
  // delete the epic, then log one epic-removed event per issue.
  async function handleDelete() {
    const supabase = createClient();

    const { data: members, error: membersError } = await supabase
      .from("issues")
      .select("id")
      .eq("epic_id", epicId);
    if (membersError) throw new Error(membersError.message);

    if (members?.length) {
      const { error: unassignError } = await supabase
        .from("issues")
        .update({ epic_id: null })
        .eq("epic_id", epicId);
      if (unassignError) throw new Error(unassignError.message);
    }

    const { error: deleteError } = await supabase
      .from("epics")
      .delete()
      .eq("id", epicId);
    if (deleteError) throw new Error(deleteError.message);

    if (members?.length) {
      const { error: eventsError } = await supabase.from("issue_events").insert(
        members.map((m) => ({
          org_id: orgId,
          issue_id: m.id,
          type: "epic-removed",
          payload: { epic_id: epicId, epic_title: title, reason: "epic-deleted" },
        }))
      );
      // The epic is already gone and the issues are already un-assigned — a
      // failed event write is a logging gap, not a failed delete.
      if (eventsError) console.error("epic-removed events failed", eventsError);
    }

    if (redirectOnDelete) {
      router.push(`/projects/${projectId}/epics`);
    } else {
      router.refresh();
    }
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex shrink-0 items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={toggleStatus}
          disabled={toggling}
        >
          {toggling ? (
            <Loader2 className="size-4 animate-spin" />
          ) : status === "open" ? (
            <CheckCircle2 className="size-4" />
          ) : (
            <RotateCcw className="size-4" />
          )}
          {status === "open" ? "Complete epic" : "Reopen epic"}
        </Button>
        <ConfirmDialog
          trigger={
            <Button variant="outline" size="sm">
              <Trash2 className="size-4" />
              Delete
            </Button>
          }
          title={`Delete "${title}"?`}
          description={
            memberCount === 0
              ? "This epic has no work items assigned. Deleting it can't be undone."
              : `${memberCount} ${
                  memberCount === 1 ? "work item" : "work items"
                } will be un-assigned from this epic. The ${
                  memberCount === 1 ? "work item is" : "work items are"
                } not deleted — only the epic is. This can't be undone.`
          }
          confirmLabel="Delete epic"
          onConfirm={handleDelete}
        />
      </div>
      {error && (
        <p className="text-sm font-medium text-destructive">{error}</p>
      )}
    </div>
  );
}
