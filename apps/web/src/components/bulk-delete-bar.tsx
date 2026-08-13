"use client";

import { Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/confirm-dialog";

/** US-2.26: shown while rows are selected; Delete goes through ConfirmDialog. */
export function BulkDeleteBar({
  count,
  onClear,
  notice,
  deleteDisabled,
  confirmTitle,
  confirmDescription,
  confirmLabel,
  requireText,
  onDelete,
}: {
  count: number;
  onClear: () => void;
  /** Extra context, e.g. why some/all selected rows can't be deleted. */
  notice?: string;
  deleteDisabled?: boolean;
  confirmTitle: string;
  confirmDescription: string;
  confirmLabel: string;
  /** Demand this exact text be typed before confirm enables (us-1.41 pattern). */
  requireText?: string;
  onDelete: () => Promise<void>;
}) {
  if (!count) return null;

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-muted/40 px-3 py-2">
      <span className="text-sm font-medium tabular-nums">{count} selected</span>
      <Button variant="ghost" size="sm" onClick={onClear}>
        <X className="size-4" />
        Clear
      </Button>
      {notice && <span className="text-xs text-muted-foreground">{notice}</span>}
      <div className="ml-auto">
        <ConfirmDialog
          trigger={
            <Button variant="destructive" size="sm" disabled={deleteDisabled}>
              <Trash2 className="size-4" />
              Delete
            </Button>
          }
          title={confirmTitle}
          description={confirmDescription}
          confirmLabel={confirmLabel}
          requireText={requireText}
          onConfirm={onDelete}
        />
      </div>
    </div>
  );
}
