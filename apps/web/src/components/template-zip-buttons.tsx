"use client";

// US-114.1: Export and Import for a project template, shared by the
// superadmin catalog (/admin/project-templates) and the org's copies
// (/settings/project-templates), the same way `template-files-editor.tsx`
// is. This component owns the browser half — building the download and
// reading the picked file — and hands the page an ImportPlan to apply, so
// the writes stay where the page already makes them (the admin api on one
// side, Supabase under RLS on the other).
//
// Import is destructive to the selected template, so nothing is written
// until the manager has seen the plan: per file, overwritten / cleared /
// unchanged, plus what in the archive was ignored. Cancel writes nothing.

import { useRef, useState } from "react";
import { Download, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { toast, toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import type { TemplateContents } from "@/lib/template-files";
import {
  buildTemplateZip,
  oversizeFile,
  planImport,
  readTemplateZip,
  templateZipFilename,
  type ImportPlan,
  type ZipFile,
} from "@/lib/template-zip";

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

  async function importZip(file: File) {
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
    const changes = plan.overwrite.length + plan.cleared.length;
    if (changes === 0) {
      toast({
        title: "Nothing to import",
        description:
          `Every file in ${file.name} already matches this template` +
          (ignored.length ? `; ${describeIgnored(ignored)} ignored.` : "."),
      });
      return;
    }
    const ok = await confirmDialog({
      title: `Import ${file.name} into ${name}?`,
      description: <PlanSummary plan={plan} ignored={ignored} />,
      confirmLabel: `Import ${changes} ${changes === 1 ? "file" : "files"}`,
      destructive: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      await onImport(plan);
      toastSuccess(
        "Imported",
        [
          plan.overwrite.length ? `${plan.overwrite.length} overwritten` : null,
          plan.cleared.length ? `${plan.cleared.length} cleared` : null,
          plan.unchanged.length ? `${plan.unchanged.length} unchanged` : null,
        ]
          .filter(Boolean)
          .join(", ") + ".",
      );
    } catch (e) {
      toastError("Import stopped", (e as Error).message);
    } finally {
      setBusy(false);
    }
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
          if (f) void importZip(f);
        }}
      />
    </>
  );
}

function describeIgnored(ignored: string[]): string {
  const shown = ignored.slice(0, 3).join(", ");
  const more = ignored.length - 3;
  return more > 0 ? `${shown} and ${more} more` : shown;
}

function PlanSummary({ plan, ignored }: { plan: ImportPlan; ignored: string[] }) {
  return (
    <span className="mt-2 block space-y-2 text-left">
      <PlanGroup label="Overwritten" files={plan.overwrite} tone="text-foreground" />
      <PlanGroup label="Cleared" files={plan.cleared} tone="text-destructive" />
      <PlanGroup label="Unchanged" files={plan.unchanged} tone="text-muted-foreground" />
      {ignored.length > 0 && (
        <span className="block">
          <span className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Ignored ({ignored.length})
          </span>
          <span className="block font-mono text-xs text-muted-foreground">
            {describeIgnored(ignored)}
          </span>
        </span>
      )}
      <span className="block text-xs">
        Files not in the archive are left as they are. Nothing is written until you confirm.
      </span>
    </span>
  );
}

function PlanGroup({
  label,
  files,
  tone,
}: {
  label: string;
  files: ZipFile[];
  tone: string;
}) {
  if (files.length === 0) return null;
  return (
    <span className="block">
      <span className="block text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label} ({files.length})
      </span>
      <span className={`block font-mono text-xs ${tone}`}>
        {files.map((f) => f.path).join(", ")}
      </span>
    </span>
  );
}
