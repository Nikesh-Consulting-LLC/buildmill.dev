"use client";

// US-114.2 / US-114.3: the project's Instructions tab, in the templates'
// shape. Agent Instructions and Task Instructions used to be two tabs with
// two layouts for what is, on disk, one folder — AGENTS.md and
// .buildmill/*.md. This tab draws them the way /admin/project-templates and
// /settings/project-templates do (`template-files-editor.tsx`): Task
// processing and the grouped file tree on the left, one editor on the right,
// so a template and the project it seeded look the same.
//
// What a project knows that a template does not sits above and around the
// editor: the two mark-ready stamps, the publish bar, History for the active
// file's audit surface, the refresh dialog — and (us-114.3) which org
// template the project came from, which files differ from it, Reset to
// template per file, Export/Import of the whole set, and Change template.
//
// Writes go straight to Supabase under RLS, exactly as the two tabs did: the
// document to projects.agent_instructions, a task file to its
// worker_instructions row.

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { History, Loader2, RotateCcw, Workflow } from "lucide-react";

import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import {
  AGENTS_KEY,
  contentFor,
  templateFileForKey,
  type TemplateContents,
} from "@/lib/template-files";
import { KIND_FILES } from "@/lib/instruction-files";
import {
  TemplateFileEditor,
  TemplateFileTree,
} from "@/components/template-files-editor";
import { TemplateZipButtons } from "@/components/template-zip-buttons";
import { templateZipFilename, type ImportPlan } from "@/lib/template-zip";
import { MarkReadyControl } from "./mark-ready-control";
import { PublishInstructionsBar } from "./publish-instructions-bar";
import { RefreshGuidelinesDialog } from "./refresh-guidelines-dialog";
import { TaskProcessingCard } from "./task-processing-card";
import { ChangeTemplateDialog, type OrgTemplateOption } from "./change-template-dialog";

export type WorkerInstructionRow = {
  id: string;
  run_kind: string;
  content: string;
  updated_by: string | null;
  updated_at: string;
};

/** The org template the project came from, with the files it holds. */
export type ProjectTemplate = {
  id: string;
  name: string;
  template_key: string | null;
  agent_instructions: string;
  /** worker_instruction sections by kind — only kinds the template has a file for. */
  sections: Record<string, string>;
};

const TASK_PROCESSING_KEY = "task-processing";

function formatWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** What the template says this file should hold, or null when the template
 * has no file for it (the seed gave it the factory default — there is nothing
 * of the template's to differ from). */
export function templateTextFor(t: ProjectTemplate | null, key: string): string | null {
  if (!t) return null;
  if (key === AGENTS_KEY) return t.agent_instructions.trim() ? t.agent_instructions : null;
  const s = t.sections[key];
  return s && s.trim() ? s : null;
}

/** Keys whose project text differs from the template's. */
export function driftedKeys(contents: TemplateContents, t: ProjectTemplate | null): string[] {
  if (!t) return [];
  const out: string[] = [];
  for (const key of [AGENTS_KEY, ...Object.keys(KIND_FILES)]) {
    const want = templateTextFor(t, key);
    if (want !== null && want !== contentFor(contents, key)) out.push(key);
  }
  return out;
}

export function InstructionsTab({
  projectId,
  projectName,
  projectSlug,
  orgId,
  canEdit,
  repoFullName,
  agentInstructions,
  rows,
  actorNames,
  template,
  orgTemplates,
  guidelinesReadyAt,
  guidelinesReadyByName,
  guidelinesEditedSince,
  workerReadyAt,
  workerReadyByName,
  initialFile,
  followBuildOrder,
  routeFeatureAsOne,
  autoApprovePrd,
  autoApprovePlan,
  autoApproveCode,
}: {
  projectId: string;
  projectName: string;
  projectSlug: string;
  orgId: string;
  canEdit: boolean;
  repoFullName: string | null;
  agentInstructions: string;
  rows: WorkerInstructionRow[];
  actorNames: Record<string, string>;
  template: ProjectTemplate | null;
  orgTemplates: OrgTemplateOption[];
  guidelinesReadyAt: string | null;
  guidelinesReadyByName: string | null;
  guidelinesEditedSince: boolean;
  workerReadyAt: string | null;
  workerReadyByName: string | null;
  /** `?file=` deep link: `agents`, a run kind, or `task-processing`. */
  initialFile?: string;
  followBuildOrder: boolean;
  routeFeatureAsOne: boolean;
  autoApprovePrd: boolean;
  autoApprovePlan: boolean;
  autoApproveCode: boolean;
}) {
  const router = useRouter();
  const [active, setActive] = useState<string>(() => {
    if (initialFile === TASK_PROCESSING_KEY) return TASK_PROCESSING_KEY;
    return templateFileForKey(initialFile ?? null) ? (initialFile as string) : AGENTS_KEY;
  });
  const [refreshKey, setRefreshKey] = useState(0);
  const [resetting, setResetting] = useState(false);
  const [changeOpen, setChangeOpen] = useState(false);

  const contents: TemplateContents = useMemo(
    () => ({
      agentInstructions,
      instructions: Object.fromEntries(rows.map((r) => [r.run_kind, r.content ?? ""])),
    }),
    [agentInstructions, rows],
  );
  const rowByKind = useMemo(
    () => Object.fromEntries(rows.map((r) => [r.run_kind, r])),
    [rows],
  );

  // US-7.5: any task file touched after the stamp.
  const workerEditedSince =
    !!workerReadyAt && rows.some((r) => r.updated_at && r.updated_at > workerReadyAt);

  const drifted = useMemo(() => driftedKeys(contents, template), [contents, template]);

  const activeFile = active === TASK_PROCESSING_KEY ? null : templateFileForKey(active);
  const historySurface = active === AGENTS_KEY ? "guidelines" : "worker_instructions";

  /** Write one file. Returns an error message, or null. */
  async function writeFile(key: string, text: string): Promise<string | null> {
    const supabase = createClient();
    if (key === AGENTS_KEY) {
      const { error } = await supabase
        .from("projects")
        .update({ agent_instructions: text })
        .eq("id", projectId);
      return error?.message ?? null;
    }
    // A project row, unlike a template section, IS the value an agent
    // reads — blank stays blank, nothing is deleted. Upsert so a kind that
    // somehow has no row yet gains one; the conflict path is the UPDATE
    // that stamps updated_by.
    const { error } = await supabase.from("worker_instructions").upsert(
      { org_id: orgId, project_id: projectId, run_kind: key, content: text },
      { onConflict: "project_id,run_kind" },
    );
    return error?.message ?? null;
  }

  function afterWrite() {
    setRefreshKey((k) => k + 1);
    router.refresh();
  }

  async function saveFile(key: string, text: string): Promise<boolean> {
    const message = await writeFile(key, text);
    if (message) {
      toastError("Could not save", message);
      return false;
    }
    const file = templateFileForKey(key);
    toastSuccess("Saved", `${file?.path ?? key} updated.`);
    afterWrite();
    return true;
  }

  /** US-114.3: put a file back to what the template says — or, for a kind
   * the template has no file for, to the factory default. */
  async function resetFile(key: string) {
    const file = templateFileForKey(key);
    const want = templateTextFor(template, key);
    let text: string;
    if (want !== null) {
      text = want;
    } else {
      if (key === AGENTS_KEY) return;
      const supabase = createClient();
      const { data, error } = await supabase.rpc("default_worker_instruction", { p_kind: key });
      if (error || !data) {
        toastError("Could not load the factory default", error?.message);
        return;
      }
      text = data;
    }
    const ok = await confirmDialog({
      title: want !== null ? `Reset ${file?.path} to the template?` : `Reset ${file?.path} to the factory default?`,
      description:
        want !== null
          ? `Replaces this project's copy with what ${template?.name} holds. Your edits to this file are lost.`
          : `The template has no file for this kind, so this puts back the factory's own text. Your edits to this file are lost.`,
      confirmLabel: "Reset",
      destructive: true,
    });
    if (!ok) return;
    setResetting(true);
    try {
      const message = await writeFile(key, text);
      if (message) toastError("Could not reset", message);
      else {
        toastSuccess("Reset", `${file?.path} now matches ${want !== null ? "the template" : "the factory default"}.`);
        afterWrite();
      }
    } finally {
      setResetting(false);
    }
  }

  /** US-114.3: apply a confirmed zip import — every changed file, then one
   * refresh. A failure names the file it stopped on. */
  async function applyImport(plan: ImportPlan) {
    try {
      for (const f of [...plan.overwrite, ...plan.cleared]) {
        const text = plan.cleared.includes(f) ? "" : f.text;
        const message = await writeFile(f.key, text);
        if (message) throw new Error(`${f.path}: ${message}`);
      }
    } finally {
      afterWrite();
    }
  }

  const activeRow = activeFile && activeFile.key !== AGENTS_KEY ? rowByKind[activeFile.key] : null;
  const activeTemplateText = activeFile ? templateTextFor(template, activeFile.key) : null;
  const activeDiffers = !!activeFile && drifted.includes(activeFile.key);

  const editorNote: ReactNode = activeFile ? (
    <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
      {activeRow ? (
        activeRow.updated_by ? (
          <span>
            Last edited {formatWhen(activeRow.updated_at)} by{" "}
            {actorNames[activeRow.updated_by] ?? "a member"}
          </span>
        ) : (
          <Badge variant="secondary" className="font-normal">factory default</Badge>
        )
      ) : null}
      {template && activeTemplateText === null && activeFile.key !== AGENTS_KEY && (
        <span>· {template.name} has no file for this kind — the factory default applies.</span>
      )}
      {template && activeDiffers && (
        <span className="text-amber-700 dark:text-amber-400">· Differs from template</span>
      )}
      {template && activeTemplateText !== null && !activeDiffers && (
        <span>· Matches template</span>
      )}
    </p>
  ) : null;

  const editorActions: ReactNode =
    canEdit && activeFile && (activeTemplateText !== null || activeFile.key !== AGENTS_KEY) ? (
      <Button
        variant="outline"
        size="sm"
        disabled={resetting || (activeTemplateText !== null && !activeDiffers)}
        title={
          activeTemplateText !== null
            ? "Put this file back to what the template holds"
            : "Put this file back to the factory's own text"
        }
        onClick={() => void resetFile(activeFile.key)}
      >
        {resetting ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCcw className="size-3.5" />}
        {activeTemplateText !== null ? "Reset to template" : "Reset to factory default"}
      </Button>
    ) : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <MarkReadyControl
              projectId={projectId}
              prefix="guidelines"
              readyAt={guidelinesReadyAt}
              readyByName={guidelinesReadyByName}
              editedSince={guidelinesEditedSince}
            />
            <MarkReadyControl
              projectId={projectId}
              prefix="worker_instructions"
              readyAt={workerReadyAt}
              readyByName={workerReadyByName}
              editedSince={workerEditedSince}
            />
          </div>
          <PublishInstructionsBar
            projectId={projectId}
            repoFullName={repoFullName}
            canPublish={canEdit}
            refreshKey={refreshKey}
          />
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            render={<Link href={`/projects/${projectId}/audit?surface=${historySurface}`} />}
            title="Who changed this file, and what it said before"
          >
            <History className="size-4" />
            History
          </Button>
          {canEdit ? (
            <RefreshGuidelinesDialog projectId={projectId} hasRepo={!!repoFullName} />
          ) : null}
        </div>
      </div>

      {/* US-114.3: the template banner. */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-md border bg-muted/30 px-3 py-2 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-muted-foreground">Template</span>
          {template ? (
            <>
              <span className="font-medium">{template.name}</span>
              <Badge variant="outline" className="font-mono text-[11px]">
                {template.template_key ?? "custom"}
              </Badge>
              {drifted.length > 0 ? (
                <Badge
                  variant="outline"
                  className="border-amber-300 bg-amber-50 text-[11px] text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-300"
                  title={drifted.map((k) => templateFileForKey(k)?.path ?? k).join(", ")}
                >
                  {drifted.length} {drifted.length === 1 ? "file differs" : "files differ"} from template
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">all files match</span>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">No template</span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <TemplateZipButtons
            contents={contents}
            name={projectName}
            filename={templateZipFilename(projectSlug).replace(/-template\.zip$/, "-instructions.zip")}
            onImport={applyImport}
            canImport={canEdit}
          />
          {canEdit && (
            <Button variant="outline" size="sm" onClick={() => setChangeOpen(true)}>
              Change template
            </Button>
          )}
        </div>
      </div>

      <div className="flex min-h-[600px] min-w-0 flex-col gap-4 md:flex-row">
        <aside className="w-full shrink-0 overflow-y-auto rounded-md border md:w-80">
          <div className="px-2 pt-2">
            <button
              type="button"
              onClick={() => setActive(TASK_PROCESSING_KEY)}
              className={cn(
                "flex w-full items-center gap-1.5 rounded px-2 py-1.5 text-left text-xs hover:bg-muted/50",
                active === TASK_PROCESSING_KEY && "bg-primary/10 font-medium text-primary",
              )}
              title="How this project's work is routed, and which gates the factory passes on its own"
            >
              <Workflow className="size-3 shrink-0 opacity-70" />
              <span className="min-w-0 flex-1 truncate">Task processing</span>
              <span className="shrink-0 font-mono text-[10px] opacity-70">settings</span>
            </button>
          </div>
          <TemplateFileTree
            contents={contents}
            activeKey={activeFile?.key ?? null}
            onSelect={(key) => setActive(key)}
          />
        </aside>

        <div className="min-w-0 flex-1 overflow-y-auto rounded-md border p-4">
          {active === TASK_PROCESSING_KEY ? (
            <TaskProcessingCard
              projectId={projectId}
              followBuildOrder={followBuildOrder}
              routeFeatureAsOne={routeFeatureAsOne}
              autoApprovePrd={autoApprovePrd}
              autoApprovePlan={autoApprovePlan}
              autoApproveCode={autoApproveCode}
            />
          ) : activeFile ? (
            <TemplateFileEditor
              key={`${activeFile.key}:${contentFor(contents, activeFile.key)}`}
              file={activeFile}
              value={contentFor(contents, activeFile.key)}
              canManage={canEdit}
              orgId={orgId}
              onSave={(text) => saveFile(activeFile.key, text)}
              note={editorNote}
              actions={editorActions}
            />
          ) : null}
        </div>
      </div>

      <ChangeTemplateDialog
        open={changeOpen}
        onOpenChange={setChangeOpen}
        projectId={projectId}
        currentTemplateId={template?.id ?? null}
        templates={orgTemplates}
        contents={contents}
        writeFile={writeFile}
        onApplied={afterWrite}
      />
    </div>
  );
}
