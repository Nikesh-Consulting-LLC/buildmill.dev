"use client";

import { confirmDialog } from "@/components/ui/confirm-dialog";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import {
  ChevronDown,
  ChevronUp,
  Eye,
  GripVertical,
  Loader2,
  Pencil,
  Sparkles,
  Trash2,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";

export type GuidelineSectionRow = {
  id: string;
  section_key: string;
  title: string;
  content: string;
  sort_order: number;
  updated_at: string;
};

function formatUpdatedAt(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export function GuidelineSectionCard({
  section,
  orgId,
  essential,
  isFirst,
  isLast,
  isDragging,
  pendingDraft,
  onMove,
  onDelete,
  onDragHandleStart,
  onDragHandleEnd,
}: {
  section: GuidelineSectionRow;
  orgId: string;
  essential: boolean;
  isFirst: boolean;
  isLast: boolean;
  isDragging?: boolean;
  /** AI-drafted content awaiting review (US-1.52) — applied once per
   * `batchId`, then the manager is free to edit without it snapping back. */
  pendingDraft?: { content: string; batchId: number };
  onMove: (id: string, direction: "up" | "down") => void;
  onDelete: (id: string) => void;
  onDragHandleStart?: (e: React.DragEvent) => void;
  onDragHandleEnd?: (e: React.DragEvent) => void;
}) {
  const router = useRouter();
  const [content, setContent] = useState(section.content);
  // Sections that already have saved content open in preview by default —
  // editing is opt-in via the Preview/Edit toggle. A freshly-added empty
  // section has nothing to preview, so it starts in edit mode.
  const [previewing, setPreviewing] = useState(section.content.trim().length > 0);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [appliedDraftContent, setAppliedDraftContent] = useState<string | null>(null);
  const appliedBatchId = useRef<number | null>(null);
  const dirty = content !== section.content;
  const isAiSuggested = dirty && appliedDraftContent === content;

  useEffect(() => {
    if (!pendingDraft || pendingDraft.batchId === appliedBatchId.current) return;
    appliedBatchId.current = pendingDraft.batchId;
    setContent(pendingDraft.content);
    setAppliedDraftContent(pendingDraft.content);
  }, [pendingDraft]);

  function handleDiscardDraft() {
    setContent(section.content);
    setAppliedDraftContent(null);
  }

  async function handleSave() {
    setError(null);
    setSaving(true);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("project_guidelines")
      .update({ content })
      .eq("id", section.id);
    setSaving(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  async function handleDelete() {
    if (
      !(await confirmDialog({
        title: "Delete section?",
        description: `"${section.title}" will be deleted. This cannot be undone.`,
        confirmLabel: "Delete",
        destructive: true,
      }))
    )
      return;
    setDeleting(true);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("project_guidelines")
      .delete()
      .eq("id", section.id);
    if (dbError) {
      setError(dbError.message);
      setDeleting(false);
      return;
    }
    onDelete(section.id);
    router.refresh();
  }

  return (
    <div
      className={cn(
        "rounded-lg border p-4 transition-opacity",
        isDragging && "opacity-40"
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <div
            draggable
            onDragStart={onDragHandleStart}
            onDragEnd={onDragHandleEnd}
            aria-label="Drag to reorder"
            title="Drag to reorder"
            className="flex shrink-0 cursor-grab items-center text-muted-foreground active:cursor-grabbing"
          >
            <GripVertical className="size-4" />
          </div>
          <p className="truncate text-sm font-medium">{section.title}</p>
          {essential && (
            <Badge variant="secondary" className="shrink-0">
              Essential
            </Badge>
          )}
          {isAiSuggested && (
            <Badge variant="outline" className="shrink-0 gap-1">
              <Sparkles className="size-3" />
              AI-suggested — not yet saved
            </Badge>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Move up"
            disabled={isFirst}
            onClick={() => onMove(section.id, "up")}
          >
            <ChevronUp className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Move down"
            disabled={isLast}
            onClick={() => onMove(section.id, "down")}
          >
            <ChevronDown className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={previewing ? "Edit" : "Preview"}
            onClick={() => setPreviewing((p) => !p)}
          >
            {previewing ? <Pencil className="size-4" /> : <Eye className="size-4" />}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Delete section"
            disabled={deleting}
            onClick={handleDelete}
          >
            {deleting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Trash2 className="size-4" />
            )}
          </Button>
        </div>
      </div>

      <p className="mt-1 text-xs text-muted-foreground">
        Last updated {formatUpdatedAt(section.updated_at)}
      </p>

      <div className="mt-3">
        {previewing ? (
          <div className="rounded-md border bg-muted/30 p-3">
            {content.trim() ? (
              <MarkdownView>{content}</MarkdownView>
            ) : (
              <p className="text-sm text-muted-foreground">Nothing to preview yet.</p>
            )}
          </div>
        ) : (
          <MarkdownEditor
            rows={6}
            value={content}
            onChange={setContent}
            orgId={orgId}
          />
        )}
      </div>

      {error && <p className="mt-2 text-sm font-medium text-destructive">{error}</p>}

      {dirty && !previewing && (
        <div className="mt-3 flex justify-end gap-2">
          {isAiSuggested && (
            <Button
              size="sm"
              variant="ghost"
              disabled={saving}
              onClick={handleDiscardDraft}
            >
              Discard
            </Button>
          )}
          <Button size="sm" disabled={saving} onClick={handleSave}>
            {saving && <Loader2 className="size-4 animate-spin" />}
            Save
          </Button>
        </div>
      )}
    </div>
  );
}
