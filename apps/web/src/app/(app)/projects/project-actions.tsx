"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  Archive,
  ArchiveRestore,
  Loader2,
  MoreHorizontal,
  Trash2,
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

export function ProjectActions({
  projectId,
  name,
  archivedAt,
  redirectOnDelete = false,
}: {
  projectId: string;
  name: string;
  archivedAt: string | null;
  redirectOnDelete?: boolean;
}) {
  const router = useRouter();
  const [toggling, setToggling] = useState(false);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  // US-92.6: the phone's overflow for Archive/Delete.
  const [moreOpen, setMoreOpen] = useState(false);

  async function toggleArchive() {
    setArchiveError(null);
    setToggling(true);
    const supabase = createClient();
    try {
      const { error } = await supabase
        .from("projects")
        .update({ archived_at: archivedAt ? null : new Date().toISOString() })
        .eq("id", projectId);
      if (error) {
        setArchiveError(error.message);
        return;
      }
      router.refresh();
    } finally {
      setToggling(false);
    }
  }

  async function handleDelete() {
    const supabase = createClient();
    // Deleting a project cascades into deleting its issues, and a issue-level
    // trigger blocks deletion of queued/running issues. Without this check
    // the user gets a confusing issue-level error while trying to delete a project.
    const { data: activeIssues, error: activeIssuesError } = await supabase
      .from("issues")
      .select("id")
      .eq("project_id", projectId)
      .in("status", ["queued", "running", "planning"])
      .limit(1);
    if (activeIssuesError) throw new Error(activeIssuesError.message);
    if (activeIssues && activeIssues.length > 0) {
      throw new Error(
        "Cancel active runs on this project's work items before deleting it."
      );
    }
    const { error } = await supabase.from("projects").delete().eq("id", projectId);
    if (error) throw new Error(error.message);
    if (redirectOnDelete) {
      router.push("/projects");
    } else {
      router.refresh();
    }
  }

  // US-92.6: one definition, two placements — the phone disclosure and the
  // desktop row must always offer the same two actions.
  const actionButtons = (
    <>
        <Button variant="outline" size="sm" onClick={toggleArchive} disabled={toggling}>
          {toggling ? (
            <Loader2 className="size-4 animate-spin" />
          ) : archivedAt ? (
            <ArchiveRestore className="size-4" />
          ) : (
            <Archive className="size-4" />
          )}
          {archivedAt ? "Restore" : "Archive"}
        </Button>
        <ConfirmDialog
          trigger={
            <Button variant="outline" size="sm">
              <Trash2 className="size-4" />
              Delete
            </Button>
          }
          title={`Delete "${name}"?`}
          description="This permanently deletes the project and all of its work items, runs, and reviews. This can't be undone."
          confirmLabel="Delete project"
          onConfirm={handleDelete}
        />
    </>
  );

  return (
    <div className="flex flex-col gap-2">
      {/* US-92.6: two destructive buttons at full prominence above the
          content is a desktop affordance. Below `md` they fold behind a
          disclosure — they are rare, and one of them is irreversible. */}
      <div className="md:hidden">
        <button
          type="button"
          onClick={() => setMoreOpen((v) => !v)}
          aria-expanded={moreOpen}
          aria-label="Project actions"
          className="flex w-full items-center justify-center rounded-md border px-2 py-1.5 text-xs text-muted-foreground"
        >
          <MoreHorizontal className="size-4" />
        </button>
        {/* Conditional render, not a closed `details`: a closed details still
            laid its children out here, so "hidden" destructive buttons were
            still on screen at 34px. */}
        {moreOpen && (
          <div className="mt-2 flex flex-col gap-2 [&_button]:h-10 [&_button]:w-full">
            {actionButtons}
          </div>
        )}
      </div>
      <div className="hidden shrink-0 items-center gap-2 md:flex">
        {actionButtons}
      </div>
      {archiveError && (
        <p className="text-sm font-medium text-destructive">{archiveError}</p>
      )}
    </div>
  );
}
