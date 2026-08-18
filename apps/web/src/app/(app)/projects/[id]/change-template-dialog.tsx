"use client";

// US-114.3: switch a project to a different org template. Replaces every
// instruction file's content the way the seed would have written it —
// the document from the template's document, each task file from its
// section or, where the template has none, the factory default
// (`default_worker_instruction`, exactly what `seed_worker_instructions`
// coalesces to) — then re-links projects.org_template_id so the banner and
// drift refer to the new template. The plan is shown before anything is
// written; cancel writes nothing.

import { useMemo, useRef, useState } from "react";
import { Loader2 } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { TemplateCard } from "@/components/template-card";
import { AGENTS_KEY, contentFor, totalFileCount, type TemplateContents } from "@/lib/template-files";
import { KIND_FILES } from "@/lib/instruction-files";
import { planImport, type ZipFile } from "@/lib/template-zip";

export type OrgTemplateOption = {
  id: string;
  name: string;
  template_key: string | null;
  is_default: boolean;
  /** Filled files: the document (if any) plus worker_instruction sections. */
  fileCount: number;
  // US-118.4: the face — the row shows the same card New project chose from.
  description?: string | null;
  image_path?: string | null;
  updated_at?: string | null;
};

/** The seventeen texts a template would write into a project. */
async function resolveTemplateFiles(templateId: string): Promise<ZipFile[]> {
  const supabase = createClient();
  const { data: row, error } = await supabase
    .from("org_project_templates")
    .select("agent_instructions")
    .eq("id", templateId)
    .maybeSingle();
  if (error || !row) throw new Error(error?.message ?? "template not found");
  const { data: sections, error: sErr } = await supabase
    .from("org_project_template_sections")
    .select("section_key, content")
    .eq("org_template_id", templateId)
    .eq("section_type", "worker_instruction");
  if (sErr) throw new Error(sErr.message);
  const byKind = Object.fromEntries((sections ?? []).map((s) => [s.section_key, s.content ?? ""]));

  const files: ZipFile[] = [{ key: AGENTS_KEY, path: "AGENTS.md", text: row.agent_instructions ?? "" }];
  const missing: string[] = [];
  for (const kind of Object.keys(KIND_FILES)) {
    const text = byKind[kind];
    if (text && text.trim()) files.push({ key: kind, path: `.buildmill/${KIND_FILES[kind]}`, text });
    else missing.push(kind);
  }
  // The seed's own rule: a kind the template leaves empty gets the factory
  // default. Immutable SQL functions, so the fan-out is cheap.
  const defaults = await Promise.all(
    missing.map(async (kind) => {
      const { data, error: dErr } = await supabase.rpc("default_worker_instruction", { p_kind: kind });
      if (dErr) throw new Error(`${kind}: ${dErr.message}`);
      return { key: kind, path: `.buildmill/${KIND_FILES[kind]}`, text: data ?? "" };
    }),
  );
  return [...files, ...defaults];
}

export function ChangeTemplateDialog({
  open,
  onOpenChange,
  projectId,
  currentTemplateId,
  templates,
  contents,
  writeFile,
  onApplied,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  currentTemplateId: string | null;
  templates: OrgTemplateOption[];
  contents: TemplateContents;
  /** The tab's own writer, so this dialog stores exactly what Save would. */
  writeFile: (key: string, text: string) => Promise<string | null>;
  onApplied: () => void;
}) {
  const [chosen, setChosen] = useState<string | null>(null);
  const [files, setFiles] = useState<ZipFile[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [applying, setApplying] = useState(false);
  // Guards a slow read for template A landing after the manager picked B.
  const pickSeq = useRef(0);

  function handleOpenChange(next: boolean) {
    if (!next) {
      pickSeq.current += 1;
      setChosen(null);
      setFiles(null);
      setLoading(false);
    }
    onOpenChange(next);
  }

  async function choose(templateId: string) {
    const seq = ++pickSeq.current;
    setChosen(templateId);
    setFiles(null);
    setLoading(true);
    try {
      const f = await resolveTemplateFiles(templateId);
      if (seq === pickSeq.current) setFiles(f);
    } catch (e) {
      if (seq === pickSeq.current) toastError("Could not read the template", (e as Error).message);
    } finally {
      if (seq === pickSeq.current) setLoading(false);
    }
  }

  const plan = useMemo(() => (files ? planImport(contents, files) : null), [files, contents]);
  const chosenTemplate = templates.find((t) => t.id === chosen) ?? null;

  async function apply() {
    if (!chosen || !plan || !files) return;
    setApplying(true);
    try {
      // Every file the template defines is written — a file that already
      // matches is skipped only because writing it would be a no-op edit
      // that stamps updated_by for nothing.
      for (const f of [...plan.overwrite, ...plan.cleared]) {
        const message = await writeFile(f.key, plan.cleared.includes(f) ? "" : f.text);
        if (message) throw new Error(`${f.path}: ${message}`);
      }
      const supabase = createClient();
      const { error } = await supabase
        .from("projects")
        .update({ org_template_id: chosen })
        .eq("id", projectId);
      if (error) throw new Error(`re-linking the template: ${error.message}`);
      toastSuccess(
        "Template changed",
        `${chosenTemplate?.name} now backs this project — ${plan.overwrite.length} overwritten, ${plan.cleared.length} cleared, ${plan.unchanged.length} unchanged. Publish to push the files.`,
      );
      handleOpenChange(false);
      onApplied();
    } catch (e) {
      toastError("Change stopped", (e as Error).message);
      onApplied();
    } finally {
      setApplying(false);
    }
  }

  const changes = plan ? plan.overwrite.length + plan.cleared.length : 0;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Change template</DialogTitle>
          <DialogDescription>
            Replaces the content of every instruction file in this project with
            the template&apos;s files, the way a new project is seeded. Files the
            template leaves empty get the factory default. Your local edits are
            lost — export first if you want to keep them.
          </DialogDescription>
        </DialogHeader>
        <div className="flex max-h-72 flex-col gap-1.5 overflow-y-auto">
          {templates.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No templates in this org yet — copy one from the catalog under
              Settings → Project templates.
            </p>
          )}
          {templates.map((t) => {
            const isCurrent = t.id === currentTemplateId;
            const selected = t.id === chosen;
            return (
              <TemplateCard
                key={t.id}
                variant="row"
                template={t}
                disabled={isCurrent || applying}
                selected={selected}
                aria-pressed={selected}
                onClick={() => void choose(t.id)}
                keyBadge={
                  <Badge variant="outline" className="font-mono text-[10.5px]">
                    {t.template_key ?? "custom"}
                  </Badge>
                }
                meta={isCurrent ? "current" : `${t.fileCount} of ${totalFileCount()} files`}
              />
            );
          })}
        </div>
        {chosen && (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            {loading || !plan ? (
              <span className="flex items-center gap-2">
                <Loader2 className="size-3.5 animate-spin" /> Reading {chosenTemplate?.name}…
              </span>
            ) : (
              <>
                <span className="font-medium">{chosenTemplate?.name}</span> will overwrite{" "}
                {plan.overwrite.length}, clear {plan.cleared.length}, and leave{" "}
                {plan.unchanged.length} unchanged
                {contentFor(contents, AGENTS_KEY).trim() && plan.cleared.some((f) => f.key === AGENTS_KEY)
                  ? " — including AGENTS.md, which this template leaves empty"
                  : ""}
                . Publish to repo afterwards to push the new files.
              </>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" size="sm" disabled={applying} onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            size="sm"
            disabled={!chosen || !plan || applying}
            onClick={() => void apply()}
          >
            {applying ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {changes === 0 && plan ? "Files already match — link template" : "Replace all files"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
