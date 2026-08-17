"use client";

// US-114.1: Export and Import for a set of instruction files, shared by the
// superadmin catalog (/admin/project-templates), the org's copies
// (/settings/project-templates) and a project's Instructions tab (us-114.3),
// the same way `template-files-editor.tsx` is. This component owns the
// browser half — building the download and reading the picked file — and
// hands the page an ImportPlan to apply, so the writes stay where the page
// already makes them (the admin api on one side, Supabase under RLS on the
// others).
//
// Import is destructive to whatever is selected, so nothing is written until
// the manager has seen the plan and chosen what to take: one checkbox per
// group — AGENTS.md, then the phase groups the tree draws — each naming the
// files it would overwrite, clear, or leave. AGENTS.md starts unchecked:
// it is the file most often tuned by hand, so replacing it is a choice.
// Cancel writes nothing.

import { useRef, useState } from "react";
import { Download, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast, toastError, toastSuccess } from "@/components/ui/toast";
import type { TemplateContents } from "@/lib/template-files";
import {
  buildTemplateZip,
  defaultSelectedGroups,
  filterPlan,
  groupPlan,
  oversizeFile,
  planImport,
  readTemplateZip,
  templateZipFilename,
  type ImportGroup,
  type ImportPlan,
  type ZipFile,
} from "@/lib/template-zip";

type Pending = {
  fileName: string;
  plan: ImportPlan;
  groups: ImportGroup[];
  ignored: string[];
};

export function TemplateZipButtons({
  contents,
  name,
  filename,
  onImport,
  canImport = true,
}: {
  /** The selected template's files; null while they load (buttons disabled). */
  contents: TemplateContents | null;
  /** What the archive is named after — the catalog key, or the org copy's name. */
  name: string;
  /** US-114.3: override the archive's file name (a project exports as
   * `<slug>-instructions.zip`, not `-template.zip`). */
  filename?: string;
  /** Apply a confirmed plan. Throw to stop; the message reaches the manager. */
  onImport: (plan: ImportPlan) => Promise<void>;
  /** Hide Import (a viewer may export a project's files but not overwrite them). */
  canImport?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function exportZip() {
    if (!contents) return;
    const bytes = buildTemplateZip(contents);
    // A fresh ArrayBuffer-backed copy: fflate may hand back a view over a
    // larger buffer, and Blob would take the whole thing.
    const blob = new Blob([new Uint8Array(bytes)], { type: "application/zip" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename ?? templateZipFilename(name);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  async function pickZip(file: File) {
    if (!contents) return;
    let files: ZipFile[];
    let ignored: string[];
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      ({ files, ignored } = readTemplateZip(bytes));
    } catch {
      toastError("Not a zip", `${file.name} could not be read as a zip archive.`);
      return;
    }
    if (files.length === 0) {
      toastError(
        "No template files found",
        `${file.name} carries no AGENTS.md and no .buildmill/*.md` +
          (ignored.length ? ` — only ${describeIgnored(ignored)}.` : "."),
      );
      return;
    }
    const big = oversizeFile(files);
    if (big) {
      toastError(
        "File too large",
        `${big.path} is ${big.text.length.toLocaleString()} characters; the limit is ${
          big.key === "agents" ? "200,000" : "20,000"
        }. Nothing was written.`,
      );
      return;
    }
    const plan = planImport(contents, files);
    if (plan.overwrite.length + plan.cleared.length === 0) {
      toast({
        title: "Nothing to import",
        description:
          `Every file in ${file.name} already matches` +
          (ignored.length ? `; ${describeIgnored(ignored)} ignored.` : "."),
      });
      return;
    }
    const groups = groupPlan(plan);
    setSelected(defaultSelectedGroups(groups));
    setPending({ fileName: file.name, plan, groups, ignored });
  }

  const chosen = pending ? filterPlan(pending.plan, selected) : null;
  const changes = chosen ? chosen.overwrite.length + chosen.cleared.length : 0;

  async function confirmImport() {
    if (!pending || !chosen) return;
    setBusy(true);
    try {
      await onImport(chosen);
      toastSuccess(
        "Imported",
        [
          chosen.overwrite.length ? `${chosen.overwrite.length} overwritten` : null,
          chosen.cleared.length ? `${chosen.cleared.length} cleared` : null,
          chosen.unchanged.length ? `${chosen.unchanged.length} unchanged` : null,
        ]
          .filter(Boolean)
          .join(", ") + ".",
      );
      setPending(null);
    } catch (e) {
      toastError("Import stopped", (e as Error).message);
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  function toggle(key: string, on: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (on) next.add(key);
      else next.delete(key);
      return next;
    });
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        disabled={!contents || busy}
        title="Download these files as a zip"
        onClick={exportZip}
      >
        <Download className="size-3.5" />
        Export
      </Button>
      {canImport && (
        <Button
          variant="outline"
          size="sm"
          disabled={!contents || busy}
          title="Overwrite these files from a zip"
          onClick={() => inputRef.current?.click()}
        >
          <Upload className="size-3.5" />
          {busy ? "Importing…" : "Import"}
        </Button>
      )}
      <input
        ref={inputRef}
        type="file"
        accept=".zip,application/zip,application/x-zip-compressed"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          // Reset so picking the same file again re-fires onChange.
          e.target.value = "";
          if (f) void pickZip(f);
        }}
      />

      <Dialog
        open={pending !== null}
        onOpenChange={(open) => {
          if (!open && !busy) setPending(null);
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Import {pending?.fileName} into {name}?</DialogTitle>
            <DialogDescription>
              Choose what to take. Checked groups are overwritten from the zip;
              unchecked groups and files not in the archive are left as they
              are. Nothing is written until you confirm.
            </DialogDescription>
          </DialogHeader>
          <div className="flex max-h-80 flex-col gap-1.5 overflow-y-auto">
            {pending?.groups.map((g) => {
              const on = selected.has(g.key);
              const touched = g.overwrite.length + g.cleared.length;
              return (
                <label
                  key={g.key}
                  className="flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 text-sm hover:bg-muted/50"
                >
                  <Checkbox
                    checked={on}
                    disabled={busy}
                    onCheckedChange={(c) => toggle(g.key, c)}
                    aria-label={`Import ${g.label}`}
                    className="mt-0.5"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-x-2">
                      <span className={g.key === "agents" ? "font-mono font-medium" : "font-medium"}>
                        {g.label}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        {touched === 0
                          ? "already matches"
                          : [
                              g.overwrite.length ? `${g.overwrite.length} overwritten` : null,
                              g.cleared.length ? `${g.cleared.length} cleared` : null,
                              g.unchanged.length ? `${g.unchanged.length} unchanged` : null,
                            ]
                              .filter(Boolean)
                              .join(" · ")}
                      </span>
                    </span>
                    <FileList label="Overwritten" files={g.overwrite} tone="text-foreground" />
                    <FileList label="Cleared" files={g.cleared} tone="text-destructive" />
                    <FileList label="Unchanged" files={g.unchanged} tone="text-muted-foreground" />
                  </span>
                </label>
              );
            })}
            {pending && pending.ignored.length > 0 && (
              <p className="px-1 pt-1 text-xs text-muted-foreground">
                Ignored ({pending.ignored.length}):{" "}
                <span className="font-mono">{describeIgnored(pending.ignored)}</span>
              </p>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => setPending(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              size="sm"
              disabled={busy || changes === 0}
              onClick={() => void confirmImport()}
            >
              {busy
                ? "Importing…"
                : changes === 0
                  ? "Nothing selected"
                  : `Import ${changes} ${changes === 1 ? "file" : "files"}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function describeIgnored(ignored: string[]): string {
  const shown = ignored.slice(0, 3).join(", ");
  const more = ignored.length - 3;
  return more > 0 ? `${shown} and ${more} more` : shown;
}

function FileList({ label, files, tone }: { label: string; files: ZipFile[]; tone: string }) {
  if (files.length === 0) return null;
  return (
    <span className={`block font-mono text-[11px] ${tone}`}>
      <span className="text-muted-foreground">{label}: </span>
      {files.map((f) => f.path.replace(/^\.buildmill\//, "")).join(", ")}
    </span>
  );
}
