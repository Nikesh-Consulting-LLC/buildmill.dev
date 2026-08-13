"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Archive } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { StatusBadge, type IssueStatus } from "@/components/status-badge";
import { EmptyState } from "@/components/empty-state";
import { Checkbox } from "@/components/ui/checkbox";
import { BulkDeleteBar } from "@/components/bulk-delete-bar";

// Mirrors issue-actions.tsx: items mid-run can't be deleted.
const RUNNING_STATUSES = ["queued", "running"];

export function AbandonedIssueList({
  issues,
}: {
  issues: Array<{ id: string; title: string; status: string; updated_at: string }>;
}) {
  const router = useRouter();
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const selectedVisible = useMemo(
    () => issues.filter((i) => selected.has(i.id)),
    [issues, selected]
  );
  const blocked = selectedVisible.filter((i) =>
    RUNNING_STATUSES.includes(i.status)
  );
  const deletable = selectedVisible.filter(
    (i) => !RUNNING_STATUSES.includes(i.status)
  );
  const allSelected =
    issues.length > 0 && issues.every((i) => selected.has(i.id));
  const someSelected = issues.some((i) => selected.has(i.id));

  function toggleSelected(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleBulkDelete() {
    const supabase = createClient();

    // Force path: queued/running items are guarded server-side; the user
    // confirmed the force warning, so use the force_delete_issues RPC.
    if (blocked.length) {
      const ids = selectedVisible.map((i) => i.id);
      const { data, error } = await supabase.rpc("force_delete_issues", {
        p_issue_ids: ids,
      });
      if (error) throw new Error(error.message);
      const deleted = data ?? 0;
      setSelected(new Set());
      router.refresh();
      if (deleted < ids.length) {
        throw new Error(
          `Only ${deleted} of ${ids.length} work items were deleted — refresh and retry.`
        );
      }
      return;
    }

    const ids = deletable.map((i) => i.id);
    const { data, error } = await supabase
      .from("issues")
      .delete()
      .in("id", ids)
      .select("id");
    if (error) throw new Error(error.message);
    const deletedCount = data?.length ?? 0;
    setSelected(new Set());
    router.refresh();
    if (deletedCount < ids.length) {
      throw new Error(
        `Only ${deletedCount} of ${ids.length} work items were deleted — refresh and retry.`
      );
    }
  }

  if (!issues.length) {
    return (
      <EmptyState
        icon={Archive}
        title="No abandoned work items"
        description="Work items you abandon will show up here until restored or deleted."
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <label className="flex w-fit cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
        <Checkbox
          checked={allSelected}
          indeterminate={someSelected && !allSelected}
          onCheckedChange={() =>
            setSelected(
              allSelected ? new Set() : new Set(issues.map((i) => i.id))
            )
          }
          aria-label="Select all abandoned work items"
        />
        Select all
      </label>

      <BulkDeleteBar
        count={selectedVisible.length}
        onClear={() => setSelected(new Set())}
        notice={
          blocked.length
            ? `${blocked.length} of these ${blocked.length === 1 ? "is" : "are"} queued or running — deleting will force-delete ${blocked.length === 1 ? "it" : "them"}.`
            : undefined
        }
        confirmTitle={
          blocked.length
            ? `Force delete ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"}?`
            : `Delete ${selectedVisible.length} work item${selectedVisible.length === 1 ? "" : "s"}?`
        }
        confirmDescription={
          blocked.length
            ? `This permanently deletes ${selectedVisible.length} abandoned work item${selectedVisible.length === 1 ? "" : "s"} and their events, runs, and reviews. Queued or running and force-deleted: ${blocked.map((i) => i.title).join(", ")} — any run in flight is discarded and a worker still on it will fail to hand back. Linked test cases and documents are detached, not deleted. This can't be undone.`
            : `This permanently deletes ${selectedVisible.length} abandoned work item${selectedVisible.length === 1 ? "" : "s"} and their events, runs, and reviews. This can't be undone.`
        }
        confirmLabel={blocked.length ? "Force delete work items" : "Delete work items"}
        requireText={blocked.length ? "force delete" : undefined}
        onDelete={handleBulkDelete}
      />

      <ul className="grid gap-1.5">
        {issues.map((i) => (
          <li key={i.id} className="flex items-center gap-2">
            <Checkbox
              checked={selected.has(i.id)}
              onCheckedChange={() => toggleSelected(i.id)}
              aria-label={`Select ${i.title}`}
            />
            <Link
              href={`/issues/${i.id}?from=work-items`}
              className="flex min-w-0 flex-1 items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm transition-colors hover:border-ring/60"
            >
              <span className="truncate font-medium">{i.title}</span>
              <StatusBadge status={i.status as IssueStatus} />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
