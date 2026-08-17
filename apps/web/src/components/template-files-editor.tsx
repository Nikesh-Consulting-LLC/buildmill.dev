"use client";

// US-100.4: the two halves of a project-template editor, shared by the
// superadmin catalog (/admin/project-templates) and the org's copies
// (/settings/project-templates) so both present the same files, in the same
// order, under the same names as the project surface they seed.
//
// A template is "the contents of the files a project will publish": the
// AGENTS.md body (Agent Instructions) and one `.buildmill/*.md` per task
// kind. The tree lists them; the editor edits exactly one at a time. Data
// loading and saving stay with the page — the admin page goes through the
// api, the org page through Supabase under RLS — which is why this component
// takes `onSave` rather than a client.

import { useState, type ReactNode } from "react";
import { FileText } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MarkdownEditor } from "@/components/markdown-editor";
import { cn } from "@/lib/utils";
import {
  AGENTS_KEY,
  agentsFile,
  templateFileGroups,
  type TemplateContents,
  type TemplateFile,
} from "@/lib/template-files";

/** Whether a file has content — drives the muted style for empty rows. */
function filled(contents: TemplateContents, key: string): boolean {
  const text =
    key === AGENTS_KEY ? contents.agentInstructions : contents.instructions[key];
  return (text ?? "").trim() !== "";
}

export function TemplateFileTree({
  contents,
  activeKey,
  onSelect,
}: {
  contents: TemplateContents;
  activeKey: string | null;
  onSelect: (key: string) => void;
}) {
  const doc = agentsFile();
  return (
    <div className="pb-2 pl-8 pr-2">
      <FileGroup label={doc.title}>
        <FileRow
          file={doc}
          filled={filled(contents, doc.key)}
          active={activeKey === doc.key}
          onClick={() => onSelect(doc.key)}
        />
      </FileGroup>
      {templateFileGroups().map((g) => (
        <FileGroup key={g.key} label={g.label}>
          {g.files.map((f) => (
            <FileRow
              key={f.key}
              file={f}
              filled={filled(contents, f.key)}
              active={activeKey === f.key}
              onClick={() => onSelect(f.key)}
            />
          ))}
        </FileGroup>
      ))}
    </div>
  );
}

function FileGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-2 first:mt-1">
      <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="flex flex-col">{children}</div>
    </div>
  );
}

function FileRow({
  file,
  filled,
  active,
  onClick,
}: {
  file: TemplateFile;
  filled: boolean;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={file.path}
      className={cn(
        "flex w-full min-w-0 items-center gap-1.5 rounded px-2 py-1 text-left text-xs hover:bg-muted/50",
        !filled && "text-muted-foreground",
        active && "bg-primary/10 font-medium text-primary",
      )}
    >
      <FileText className="size-3 shrink-0 opacity-70" />
      <span className="min-w-0 flex-1 truncate">{file.title}</span>
      <span className="shrink-0 font-mono text-[10px] opacity-70">
        {file.path.replace(/^\.buildmill\//, "")}
      </span>
    </button>
  );
}

/** One file's editor. Keyed by the caller on `file.key` so a switch remounts
 * it with the new file's text rather than carrying a draft across files. */
export function TemplateFileEditor({
  file,
  value,
  canManage,
  onSave,
  orgId,
  note,
  actions,
}: {
  file: TemplateFile;
  value: string;
  canManage: boolean;
  onSave: (text: string) => Promise<boolean>;
  orgId?: string;
  /** US-114.2: what a project knows about this file that a template does
   * not — who edited it, whether it differs from its template. Rendered
   * under the description. */
  note?: ReactNode;
  /** US-114.3: controls beside Save (Reset to template, …). */
  actions?: ReactNode;
}) {
  const [draft, setDraft] = useState(value);
  const [saving, setSaving] = useState(false);
  const dirty = draft !== value;

  async function save() {
    setSaving(true);
    try {
      await onSave(draft);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{file.title}</span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {file.path}
            </span>
          </div>
          {file.description && (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {file.description}
            </p>
          )}
          {note}
        </div>
        {(canManage || actions) && (
          <div className="flex shrink-0 items-center gap-2">
            {actions}
            {canManage && (
              <Button
                size="sm"
                disabled={!dirty || saving}
                onClick={() => void save()}
              >
                {saving ? "Saving…" : "Save"}
              </Button>
            )}
          </div>
        )}
      </div>
      <MarkdownEditor
        value={draft}
        onChange={setDraft}
        placeholder={
          file.key === AGENTS_KEY
            ? "The conventions every agent working in this project reads first."
            : "(blank — a project created from this template falls back to the factory default for this kind)"
        }
        rows={22}
        defaultTab={value.trim() ? "preview" : "write"}
        disabled={!canManage}
        orgId={orgId}
        className="min-h-0 flex-1 overflow-y-auto"
      />
    </div>
  );
}
