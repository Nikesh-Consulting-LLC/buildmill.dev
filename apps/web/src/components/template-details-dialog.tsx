"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ImagePlus, Loader2, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownEditor } from "@/components/markdown-editor";
import { TemplateCard, TemplateCover } from "@/components/template-card";
import { cn } from "@/lib/utils";
import {
  BUILTIN_COVERS,
  TEMPLATE_IMAGE_TYPES,
  TEMPLATE_IMAGE_TYPES_LABEL,
  builtinCoverPath,
  templateImageProblem,
} from "@/lib/template-cover";

/** US-118.1: one form for a template's face — the platform admin on the
 * catalog, an org manager on a copy, and the org's "New custom template".
 * It never touches Storage or the row itself: it hands the caller the
 * values and the cover *intent* (keep / built-in / upload this file / remove),
 * because the object path and the row write differ by scope. The live card
 * on the right is what a project creator will see, re-rendered on every
 * keystroke, so a description that runs long looks wrong before it is saved. */

export type TemplateDetailsValues = {
  name: string;
  key: string;
  category: string;
  description: string;
};

export type CoverChange =
  | { kind: "keep" }
  | { kind: "builtin"; name: string }
  | { kind: "upload"; file: File }
  | { kind: "remove" };

export const DESCRIPTION_MAX = 2000;
/** Past this the counter turns amber — roughly where three card lines end. */
export const DESCRIPTION_SOFT = 300;

export function TemplateDetailsDialog({
  open,
  onOpenChange,
  scope,
  mode,
  initial,
  imagePath,
  updatedAt,
  isDefault = false,
  categories,
  onSave,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** `catalog` shows the Key field; `org` does not (a copy's key is provenance). */
  scope: "catalog" | "org";
  mode: "create" | "edit";
  initial: TemplateDetailsValues;
  /** The row's current image_path — what "keep" means. */
  imagePath?: string | null;
  updatedAt?: string | null;
  isDefault?: boolean;
  /** Categories already in use, for the datalist. */
  categories: string[];
  onSave: (values: TemplateDetailsValues, cover: CoverChange) => Promise<void>;
}) {
  const [values, setValues] = useState<TemplateDetailsValues>(initial);
  const [cover, setCover] = useState<CoverChange>({ kind: "keep" });
  const [fileProblem, setFileProblem] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  // Reset on every open so a cancelled edit leaves nothing behind (AC5).
  useEffect(() => {
    if (!open) return;
    setValues(initial);
    setCover({ kind: "keep" });
    setFileProblem(null);
    setError(null);
    setSaving(false);
    // `initial` is a fresh object per open from every caller; keying on
    // `open` alone is deliberate so typing does not get clobbered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // The picked file's object URL, for the preview only; revoked on change.
  const uploadPreviewUrl = useMemo(
    () => (cover.kind === "upload" ? URL.createObjectURL(cover.file) : null),
    [cover],
  );
  useEffect(() => {
    return () => {
      if (uploadPreviewUrl) URL.revokeObjectURL(uploadPreviewUrl);
    };
  }, [uploadPreviewUrl]);

  // What the preview shows: the intent applied over the current row.
  const previewFace = {
    name: values.name.trim() || "Untitled template",
    description: values.description,
    is_default: isDefault,
    image_path:
      cover.kind === "keep"
        ? (imagePath ?? null)
        : cover.kind === "builtin"
          ? builtinCoverPath(cover.name)
          : cover.kind === "upload"
            ? "upload:preview"
            : null,
    updated_at: updatedAt ?? null,
    cover_url: cover.kind === "upload" ? uploadPreviewUrl : undefined,
  };
  const previewHasImage = previewFace.image_path !== null;

  const canSave =
    !saving &&
    values.name.trim().length > 0 &&
    (scope !== "catalog" || values.key.trim().length > 0);

  function pickFile(file: File | undefined) {
    if (!file) return;
    const problem = templateImageProblem(file);
    setFileProblem(problem);
    if (problem) return;
    setCover({ kind: "upload", file });
  }

  async function save() {
    if (!canSave) return;
    setSaving(true);
    setError(null);
    try {
      await onSave(
        {
          name: values.name.trim(),
          key: values.key.trim(),
          category: values.category.trim(),
          description: values.description,
        },
        cover,
      );
      onOpenChange(false);
    } catch (e) {
      setError((e as Error).message || "Could not save.");
    } finally {
      setSaving(false);
    }
  }

  const descLen = values.description.length;
  const selectedBuiltin =
    cover.kind === "builtin"
      ? cover.name
      : cover.kind === "keep" && imagePath?.startsWith("builtin/")
        ? imagePath.slice("builtin/".length)
        : null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{mode === "create" ? "New template" : "Template details"}</DialogTitle>
          <DialogDescription>
            Name, description and cover — what a project creator sees on the card.
            {mode === "edit" ? " Files are edited from the tree as before." : ""}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-6 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          {/* ---- form ---- */}
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="tpl-name">Name</Label>
              <Input
                id="tpl-name"
                value={values.name}
                maxLength={200}
                autoComplete="off"
                placeholder="Web app"
                onChange={(e) => setValues((v) => ({ ...v, name: e.target.value }))}
              />
            </div>

            <div className={cn("grid gap-4", scope === "catalog" && "sm:grid-cols-2")}>
              {scope === "catalog" && (
                <div className="grid gap-2">
                  <Label htmlFor="tpl-key">
                    Key <span className="text-xs font-normal text-muted-foreground">· admin only</span>
                  </Label>
                  <Input
                    id="tpl-key"
                    value={values.key}
                    maxLength={64}
                    autoComplete="off"
                    placeholder="web-app"
                    className="font-mono text-xs"
                    onChange={(e) => setValues((v) => ({ ...v, key: e.target.value }))}
                  />
                </div>
              )}
              <div className="grid gap-2">
                <Label htmlFor="tpl-category">Category</Label>
                <Input
                  id="tpl-category"
                  value={values.category}
                  maxLength={100}
                  autoComplete="off"
                  placeholder="General"
                  list="tpl-category-options"
                  onChange={(e) => setValues((v) => ({ ...v, category: e.target.value }))}
                />
                <datalist id="tpl-category-options">
                  {categories.map((c) => (
                    <option key={c} value={c} />
                  ))}
                </datalist>
              </div>
            </div>

            <div className="grid gap-2">
              <Label htmlFor="tpl-description">Description</Label>
              <MarkdownEditor
                id="tpl-description"
                value={values.description}
                onChange={(next) =>
                  setValues((v) => ({ ...v, description: next.slice(0, DESCRIPTION_MAX) }))
                }
                rows={4}
                placeholder="What this template is for, in a sentence or two."
              />
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs text-muted-foreground">
                  Markdown. Keep it short — the card shows about the first three lines.
                </span>
                <span
                  className={cn(
                    "text-xs tabular-nums",
                    descLen > DESCRIPTION_SOFT ? "text-create" : "text-muted-foreground",
                  )}
                >
                  {descLen} / {DESCRIPTION_MAX}
                </span>
              </div>
            </div>

            <div className="grid gap-2">
              <span className="text-sm font-medium">Cover image</span>
              <div className="grid grid-cols-[128px_minmax(0,1fr)] items-center gap-3 rounded-md border border-dashed p-2.5">
                <span className="overflow-hidden rounded-sm border">
                  <TemplateCover template={previewFace} initialsClassName="text-2xl" />
                </span>
                <div className="grid gap-1.5">
                  <div className="flex flex-wrap gap-1.5">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      <ImagePlus className="size-3.5" />
                      {previewHasImage ? "Replace" : "Upload image"}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={!previewHasImage}
                      onClick={() => {
                        setCover({ kind: "remove" });
                        setFileProblem(null);
                      }}
                    >
                      <Trash2 className="size-3.5" />
                      Remove
                    </Button>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept={TEMPLATE_IMAGE_TYPES.join(",")}
                      className="hidden"
                      onChange={(e) => {
                        pickFile(e.target.files?.[0]);
                        e.target.value = "";
                      }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {TEMPLATE_IMAGE_TYPES_LABEL} up to 2 MB. Shown as a 2:1 banner — 800×400 works
                    well; taller images are cropped to the middle.
                  </span>
                  <span className={cn("text-xs", fileProblem ? "text-destructive" : "text-muted-foreground")}>
                    {fileProblem
                      ? fileProblem
                      : cover.kind === "upload"
                        ? `${cover.file.name} · ${Math.max(1, Math.round(cover.file.size / 1024))} KB — uploads on Save`
                        : cover.kind === "remove"
                          ? "No image — a generated cover from the name is used."
                          : previewHasImage
                            ? previewFace.image_path?.startsWith("builtin/")
                              ? "Built-in cover"
                              : "Uploaded cover"
                            : "No image — a generated cover from the name is used."}
                  </span>
                </div>
              </div>
              <div className="grid gap-1.5">
                <span className="text-xs text-muted-foreground">Or pick a built-in cover</span>
                <div className="flex flex-wrap gap-1.5" role="listbox" aria-label="Built-in covers">
                  {BUILTIN_COVERS.map((c) => {
                    const on = selectedBuiltin === c.name;
                    return (
                      <button
                        key={c.name}
                        type="button"
                        role="option"
                        aria-selected={on}
                        title={c.label}
                        onClick={() => {
                          setCover({ kind: "builtin", name: c.name });
                          setFileProblem(null);
                        }}
                        className={cn(
                          "grid w-[76px] gap-1 rounded-sm text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50",
                        )}
                      >
                        <span
                          className={cn(
                            "block overflow-hidden rounded-sm border",
                            on ? "border-primary ring-1 ring-primary" : "hover:border-ring/70",
                          )}
                        >
                          <TemplateCover
                            template={{ name: c.label, image_path: builtinCoverPath(c.name) }}
                          />
                        </span>
                        {/* us-118.5: past a handful, an unlabeled thumbnail is a
                            guessing game — the label is the name, one line. */}
                        <span
                          className={cn(
                            "line-clamp-2 text-[10px] leading-tight",
                            on ? "font-medium text-foreground" : "text-muted-foreground",
                          )}
                        >
                          {c.label}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          </div>

          {/* ---- live preview ---- */}
          <div className="grid content-start gap-2.5">
            <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              How it appears to project creators
            </span>
            <div className="max-w-[300px]">
              <TemplateCard
                template={previewFace}
                selected={false}
                tabIndex={-1}
                aria-hidden="true"
                className="pointer-events-none"
              />
            </div>
            <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs text-muted-foreground">
              <dt className="font-semibold text-foreground">Where</dt>
              <dd>New project, Change template, the templates page</dd>
              <dt className="font-semibold text-foreground">No image</dt>
              <dd>A generated cover from the name&apos;s initials</dd>
              {scope === "catalog" && (
                <>
                  <dt className="font-semibold text-foreground">Copies</dt>
                  <dd>An org copy carries description and cover; the org can replace either</dd>
                </>
              )}
            </dl>
          </div>
        </div>

        {error && <p className="text-sm font-medium text-destructive">{error}</p>}

        <DialogFooter>
          <Button type="button" variant="outline" size="sm" disabled={saving} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="button" size="sm" disabled={!canSave} onClick={() => void save()}>
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            {mode === "create" ? "Create" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
