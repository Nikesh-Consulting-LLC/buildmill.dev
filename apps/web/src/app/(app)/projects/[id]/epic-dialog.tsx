"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Pencil, Plus } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import type { EpicRow } from "@/lib/epics";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownEditor } from "@/components/markdown-editor";

export type EpicFormData = Pick<EpicRow, "id" | "title" | "description">;

/** Epic CRUD goes straight through the SDK under RLS — no API (us-2.8). */
export function EpicDialog({
  orgId,
  projectId,
  epic,
}: {
  orgId: string;
  projectId: string;
  epic?: EpicFormData;
}) {
  const router = useRouter();
  const isEdit = !!epic;
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(epic?.title ?? "");
  const [description, setDescription] = useState(epic?.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!title.trim()) {
      setError("Title is required.");
      return;
    }

    setSaving(true);
    const supabase = createClient();
    try {
      const values = {
        title: title.trim(),
        description: description.trim() || null,
      };

      if (isEdit) {
        const { error: dbError } = await supabase
          .from("epics")
          .update(values)
          .eq("id", epic.id);
        if (dbError) {
          setError(dbError.message);
          return;
        }
      } else {
        const { error: dbError } = await supabase.from("epics").insert({
          ...values,
          org_id: orgId,
          project_id: projectId,
          status: "open",
        });
        if (dbError) {
          setError(dbError.message);
          return;
        }
        setTitle("");
        setDescription("");
      }

      setOpen(false);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={isEdit ? <Button variant="outline" size="sm" /> : <Button />}
      >
        {isEdit ? (
          <>
            <Pencil className="size-4" />
            Edit
          </>
        ) : (
          <>
            <Plus className="size-4" />
            New epic
          </>
        )}
      </DialogTrigger>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Edit epic" : "New epic"}</DialogTitle>
          <DialogDescription>
            A larger initiative — related work items group under it and roll up to
            one progress view.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSave} className="grid gap-4">
          <div className="grid gap-2">
            <Label htmlFor="epic-title">Title</Label>
            <Input
              id="epic-title"
              placeholder="Overhaul the billing system"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="epic-description">Description</Label>
            <MarkdownEditor
              id="epic-description"
              orgId={orgId}
              rows={5}
              placeholder={"What this initiative covers and why.\n\nMarkdown supported."}
              value={description}
              onChange={setDescription}
            />
          </div>
          {error && (
            <p className="text-sm font-medium text-destructive">{error}</p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={saving}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              {isEdit ? "Save changes" : "Create epic"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
