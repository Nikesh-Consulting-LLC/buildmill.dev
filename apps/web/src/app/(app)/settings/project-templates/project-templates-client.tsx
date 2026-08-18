"use client";

// Phase 67 (us-67.3) → Phase 100 (us-100.4): the org's project templates.
// Mirrors the superadmin's layout — a template list down the left, each
// expandable to its file tree, with a single full-height Write/Preview
// editor on the right for whichever file is selected — and renders the tree
// and the editor from the same shared component (`template-files-editor.tsx`),
// so what an admin sees here is exactly what the catalog author saw and
// exactly what a new project will get.
//
// A template is the contents of the files a project will publish: the
// AGENTS.md body (`org_project_templates.agent_instructions`, migration 265)
// and one `.buildmill/*.md` per task kind (a `worker_instruction` section
// keyed by kind). Retired `guideline`/`prompt` section rows stay in the
// database as a rollback and are not offered.
//
// Every mutating control is disabled for a caller without manage_project;
// the database enforces the same gate via RLS on org_project_templates /
// org_project_template_sections, so a disabled button here is a courtesy,
// not the real boundary.

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronRight, Copy, Eye, EyeOff } from "lucide-react";

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
  DialogClose,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { MarkdownView } from "@/components/markdown-view";
import { TemplateStaticRow, TemplateThumb } from "@/components/template-card";
import {
  TemplateDetailsDialog,
  type CoverChange,
  type TemplateDetailsValues,
} from "@/components/template-details-dialog";
import {
  TEMPLATE_IMAGE_BUCKET,
  builtinCoverPath,
  isOwnOrgCover,
  orgCoverObject,
} from "@/lib/template-cover";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import {
  AGENTS_KEY,
  contentFor,
  filledFileCount,
  templateFileForKey,
  totalFileCount,
  type TemplateContents,
} from "@/lib/template-files";
import {
  TemplateFileEditor,
  TemplateFileTree,
} from "@/components/template-files-editor";
import { TemplateZipButtons } from "@/components/template-zip-buttons";
import type { ImportPlan } from "@/lib/template-zip";

type GlobalTemplate = {
  id: string;
  key: string;
  name: string;
  description: string;
  category: string;
  is_default: boolean;
  image_path: string | null;
  updated_at: string;
};

export type OrgTemplate = {
  id: string;
  template_key: string | null;
  name: string;
  description: string;
  // US-118.1/118.2: the face
  category: string;
  image_path: string | null;
  updated_at: string;
  is_default: boolean;
  is_available: boolean;
  archived_at: string | null;
  agent_instructions: string;
};

type Section = {
  section_type: string;
  section_key: string;
  content: string;
};

const ORG_TEMPLATE_COLUMNS =
  "id, template_key, name, description, category, image_path, updated_at, is_default, is_available, archived_at, agent_instructions";

export function ProjectTemplatesClient({
  orgId,
  canManage,
  globalTemplates,
  orgTemplates: initialOrgTemplates,
  fileCounts: initialFileCounts,
}: {
  orgId: string;
  canManage: boolean;
  globalTemplates: GlobalTemplate[];
  orgTemplates: OrgTemplate[];
  /** Filled per-task files per template (worker_instruction rows with
   * content); the document is added client-side from the row. */
  fileCounts: Record<string, number>;
}) {
  const supabase = createClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");
  const fileKey = searchParams.get("file");

  const [templates, setTemplates] = useState<OrgTemplate[]>(initialOrgTemplates);
  const [counts, setCounts] = useState<Record<string, number>>(initialFileCounts);
  const [sections, setSections] = useState<Section[] | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [catalogOpen, setCatalogOpen] = useState(false);
  // US-118.2: the details dialog (name, category, description, cover) replaced
  // the inline rename and the window.prompt behind "New custom template".
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const reloadTemplates = useCallback(async () => {
    const { data } = await supabase
      .from("org_project_templates")
      .select(ORG_TEMPLATE_COLUMNS)
      .eq("org_id", orgId)
      .order("sort_order", { ascending: true });
    const list = (data ?? []) as OrgTemplate[];
    setTemplates(list);
    const ids = list.map((t) => t.id);
    if (ids.length) {
      const { data: secs } = await supabase
        .from("org_project_template_sections")
        .select("org_template_id")
        .in("org_template_id", ids)
        .eq("section_type", "worker_instruction")
        .neq("content", "");
      const c: Record<string, number> = {};
      for (const s of secs ?? []) c[s.org_template_id] = (c[s.org_template_id] ?? 0) + 1;
      setCounts(c);
    } else {
      setCounts({});
    }
  }, [supabase, orgId]);

  const loadSections = useCallback(async () => {
    if (!selectedId) return;
    const { data } = await supabase
      .from("org_project_template_sections")
      .select("section_type, section_key, content")
      .eq("org_template_id", selectedId)
      .eq("section_type", "worker_instruction");
    setSections((data as Section[]) ?? []);
  }, [supabase, selectedId]);

  useEffect(() => {
    setSections(null);
    void loadSections();
  }, [loadSections]);

  useEffect(() => {
    if (!selectedId && templates.length > 0) {
      router.replace(`/settings/project-templates?id=${templates[0].id}`);
    }
  }, [selectedId, templates, router]);

  function select(templateId: string, file?: string) {
    const q = file ? `?id=${templateId}&file=${file}` : `?id=${templateId}`;
    router.push(`/settings/project-templates${q}`);
  }

  async function reload() {
    await Promise.all([reloadTemplates(), loadSections()]);
  }

  async function setDefault(t: OrgTemplate) {
    const { error } = await supabase
      .from("org_project_templates")
      .update({ is_default: true })
      .eq("id", t.id);
    if (error) return toastError("Could not set default", error.message);
    toastSuccess("Default set", `${t.name} is now what a new project in this org inherits.`);
    await reloadTemplates();
  }

  async function toggleAvailable(t: OrgTemplate) {
    const { error } = await supabase
      .from("org_project_templates")
      .update({ is_available: !t.is_available })
      .eq("id", t.id);
    if (error) return toastError("Could not update", error.message);
    await reloadTemplates();
  }

  async function toggleArchived(t: OrgTemplate) {
    const { error } = await supabase
      .from("org_project_templates")
      .update({ archived_at: t.archived_at ? null : new Date().toISOString() })
      .eq("id", t.id);
    if (error) return toastError("Could not update", error.message);
    toastSuccess(t.archived_at ? "Unarchived" : "Archived", t.name);
    await reloadTemplates();
  }

  async function duplicateTemplate(t: OrgTemplate) {
    const names = new Set(templates.map((x) => x.name));
    let name = `${t.name} (copy)`;
    let n = 2;
    while (names.has(name)) {
      name = `${t.name} (copy ${n})`;
      n += 1;
    }
    const { data: created, error } = await supabase
      .from("org_project_templates")
      .insert({
        org_id: orgId,
        template_key: null,
        name,
        description: t.description,
        category: t.category,
        // A built-in cover is a name and travels; an uploaded one is one
        // row's object — the duplicate starts with the generated cover.
        image_path: t.image_path?.startsWith("builtin/") ? t.image_path : null,
        agent_instructions: t.agent_instructions ?? "",
      })
      .select("id")
      .single();
    if (error || !created) return toastError("Could not duplicate", error?.message ?? "unknown error");

    // Only the files: retired guideline/prompt rows are rollback data on the
    // source, not content a new template should inherit.
    const { data: srcSections } = await supabase
      .from("org_project_template_sections")
      .select("section_type, section_key, title, content, sort_order")
      .eq("org_template_id", t.id)
      .eq("section_type", "worker_instruction");
    if (srcSections?.length) {
      const { error: copyError } = await supabase.from("org_project_template_sections").insert(
        srcSections.map((s) => ({ ...s, org_template_id: created.id, org_id: orgId })),
      );
      if (copyError) return toastError("Duplicated without its files", copyError.message);
    }
    toastSuccess("Duplicated", `${name} is ready to fine-tune.`);
    await reloadTemplates();
    select(created.id);
  }

  async function deleteTemplate(t: OrgTemplate) {
    const ok = await confirmDialog({
      title: `Delete ${t.name}?`,
      description: "This removes the template and every file in it from this org.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    if (isOwnOrgCover(t.image_path, orgId)) {
      await supabase.storage.from(TEMPLATE_IMAGE_BUCKET).remove([t.image_path!]).catch(() => null);
    }
    const { error } = await supabase.from("org_project_templates").delete().eq("id", t.id);
    if (error) return toastError("Could not delete", error.message);
    toastSuccess("Deleted", `${t.name} is gone.`);
    const remaining = templates.filter((x) => x.id !== t.id);
    await reloadTemplates();
    if (selectedId === t.id) {
      if (remaining.length > 0) select(remaining[0].id);
      else router.push("/settings/project-templates");
    }
  }

  /** US-118.2: resolve the cover intent for an org copy. Uploads go to the
   * org's own path under Storage RLS (`manage_project`); a copy that still
   * points at the catalog's object is never deleted from here — Remove on it
   * just means "no image". */
  async function resolveOrgCover(
    orgTemplateId: string,
    current: string | null,
    cover: CoverChange,
  ): Promise<{ image_path?: string | null }> {
    if (cover.kind === "keep") return {};
    const own = orgCoverObject(orgId, orgTemplateId);
    if (cover.kind === "upload") {
      const { error } = await supabase.storage
        .from(TEMPLATE_IMAGE_BUCKET)
        .upload(own, cover.file, { upsert: true, contentType: cover.file.type });
      if (error) throw new Error(`Upload failed: ${error.message}`);
      return { image_path: own };
    }
    if (isOwnOrgCover(current, orgId)) {
      await supabase.storage.from(TEMPLATE_IMAGE_BUCKET).remove([own]).catch(() => null);
    }
    if (cover.kind === "builtin") return { image_path: builtinCoverPath(cover.name) };
    return { image_path: null };
  }

  async function saveDetails(t: OrgTemplate, values: TemplateDetailsValues, cover: CoverChange) {
    const patch: Record<string, unknown> = {};
    if (values.name !== t.name) patch.name = values.name;
    if (values.category !== t.category) patch.category = values.category;
    if (values.description !== t.description) patch.description = values.description;
    Object.assign(patch, await resolveOrgCover(t.id, t.image_path, cover));
    if (Object.keys(patch).length > 0) {
      const { error } = await supabase.from("org_project_templates").update(patch).eq("id", t.id);
      if (error) throw new Error(error.message);
    }
    toastSuccess("Saved", `${values.name} updated.`);
    await reloadTemplates();
  }

  async function createFromDetails(values: TemplateDetailsValues, cover: CoverChange) {
    const { data: created, error } = await supabase
      .from("org_project_templates")
      .insert({
        org_id: orgId,
        template_key: null,
        name: values.name,
        category: values.category,
        description: values.description,
      })
      .select("id")
      .single();
    if (error || !created) throw new Error(error?.message ?? "unknown error");
    // A cover picked before Create is uploaded once the row exists — the
    // object path needs the id.
    const coverPatch = await resolveOrgCover(created.id, null, cover);
    if (coverPatch.image_path !== undefined) {
      const { error: patchError } = await supabase
        .from("org_project_templates")
        .update(coverPatch)
        .eq("id", created.id);
      if (patchError) throw new Error(patchError.message);
    }
    toastSuccess("Created", `${values.name} is ready to fill in.`);
    await reloadTemplates();
    select(created.id, AGENTS_KEY);
  }

  async function copyIn(t: GlobalTemplate) {
    const { error } = await supabase.rpc("copy_project_template_into_org", {
      p_template_id: t.id,
      p_org: orgId,
      p_name: t.name,
    });
    if (error) return toastError("Could not copy template", error.message);
    toastSuccess("Copied", `${t.name} is now in this org — fine-tune it on the left.`);
    setCatalogOpen(false);
    await reloadTemplates();
  }

  /** Save one file. The document goes to the template row; a per-task file
   * goes to its `worker_instruction` section — or is deleted when blanked,
   * because a stored empty string would win over the factory default at
   * project creation (`seed_worker_instructions` coalesces on it). */
  async function saveFile(t: OrgTemplate, key: string, text: string): Promise<boolean> {
    const file = templateFileForKey(key);
    let message: string | null = null;
    if (key === AGENTS_KEY) {
      const { error } = await supabase
        .from("org_project_templates")
        .update({ agent_instructions: text })
        .eq("id", t.id);
      message = error?.message ?? null;
    } else if (text.trim() === "") {
      const { error } = await supabase
        .from("org_project_template_sections")
        .delete()
        .eq("org_template_id", t.id)
        .eq("section_type", "worker_instruction")
        .eq("section_key", key);
      message = error?.message ?? null;
    } else {
      const { error } = await supabase.from("org_project_template_sections").upsert(
        {
          org_template_id: t.id,
          org_id: orgId,
          section_type: "worker_instruction",
          section_key: key,
          title: "",
          content: text,
          sort_order: 0,
        },
        { onConflict: "org_template_id,section_type,section_key" },
      );
      message = error?.message ?? null;
    }
    if (message) {
      toastError("Could not save", message);
      return false;
    }
    toastSuccess("Saved", `${file?.path ?? key} updated.`);
    await reload();
    return true;
  }

  /** US-114.1: apply a confirmed zip import — the same writes `saveFile`
   * makes, batched: the document to the row, changed sections in one upsert,
   * cleared sections in one delete. RLS (`manage_project`) is the boundary. */
  async function applyImport(t: OrgTemplate, plan: ImportPlan) {
    try {
      const doc = plan.overwrite.find((f) => f.key === AGENTS_KEY);
      const docCleared = plan.cleared.some((f) => f.key === AGENTS_KEY);
      if (doc || docCleared) {
        const { error } = await supabase
          .from("org_project_templates")
          .update({ agent_instructions: doc ? doc.text : "" })
          .eq("id", t.id);
        if (error) throw new Error(`AGENTS.md: ${error.message}`);
      }
      const upserts = plan.overwrite.filter((f) => f.key !== AGENTS_KEY);
      if (upserts.length) {
        const { error } = await supabase.from("org_project_template_sections").upsert(
          upserts.map((f) => ({
            org_template_id: t.id,
            org_id: orgId,
            section_type: "worker_instruction",
            section_key: f.key,
            title: "",
            content: f.text,
            sort_order: 0,
          })),
          { onConflict: "org_template_id,section_type,section_key" },
        );
        if (error) throw new Error(`${upserts.map((f) => f.path).join(", ")}: ${error.message}`);
      }
      const clears = plan.cleared.filter((f) => f.key !== AGENTS_KEY);
      if (clears.length) {
        const { error } = await supabase
          .from("org_project_template_sections")
          .delete()
          .eq("org_template_id", t.id)
          .eq("section_type", "worker_instruction")
          .in(
            "section_key",
            clears.map((f) => f.key),
          );
        if (error) throw new Error(`${clears.map((f) => f.path).join(", ")}: ${error.message}`);
      }
    } finally {
      await reload();
    }
  }

  const selected = templates.find((t) => t.id === selectedId) ?? null;
  const copiedKeys = new Set(templates.filter((t) => t.template_key).map((t) => t.template_key));
  const categoriesInUse = Array.from(
    new Set(
      [...templates.map((t) => t.category), ...globalTemplates.map((t) => t.category)].filter(Boolean),
    ),
  ).sort();
  const contents: TemplateContents | null =
    selected && sections
      ? {
          agentInstructions: selected.agent_instructions ?? "",
          instructions: Object.fromEntries(
            sections.map((s) => [s.section_key, s.content ?? ""]),
          ),
        }
      : null;
  const activeFile = templateFileForKey(fileKey);

  function fileCountFor(t: OrgTemplate): number {
    if (t.id === selectedId && contents) return filledFileCount(contents);
    return (counts[t.id] ?? 0) + ((t.agent_instructions ?? "").trim() ? 1 : 0);
  }

  return (
    <div className="flex h-[calc(100vh-8rem)] min-h-[600px] w-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Project templates
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            The files a new project in this org starts with: its Agent
            Instructions (<span className="font-mono">AGENTS.md</span>) and
            one per-task instruction file under{" "}
            <span className="font-mono">.buildmill/</span>. A new project
            silently inherits a copy of the default template below.
            {!canManage && (
              <span className="mt-1 block text-xs">
                You can read every template here; only Owner/Admin can copy,
                edit, or create one.
              </span>
            )}
          </p>
        </div>
        {canManage && (
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => setCatalogOpen(true)}>
              Copy from Catalog
            </Button>
            <Button size="sm" onClick={() => setCreateOpen(true)}>
              New custom template
            </Button>
          </div>
        )}
      </div>

      <div className="flex min-h-0 flex-1 gap-4">
        <aside className="w-80 shrink-0 overflow-y-auto rounded-md border">
          {templates.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">
              No templates yet — copy one from the superadmin catalog.
            </p>
          ) : (
            <ul className="divide-y">
              {templates.map((t) => {
                const expanded = t.id === selectedId && !collapsedIds.has(t.id);
                return (
                  <li key={t.id}>
                    <div
                      role="button"
                      tabIndex={0}
                      onClick={() => {
                        if (t.id === selectedId) {
                          setCollapsedIds((prev) => {
                            const next = new Set(prev);
                            if (next.has(t.id)) next.delete(t.id);
                            else next.add(t.id);
                            return next;
                          });
                        } else {
                          setCollapsedIds((prev) => {
                            if (!prev.has(t.id)) return prev;
                            const next = new Set(prev);
                            next.delete(t.id);
                            return next;
                          });
                          select(t.id);
                        }
                      }}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          e.currentTarget.click();
                        }
                      }}
                      className={cn(
                        "flex w-full cursor-pointer items-start gap-1.5 px-3 py-2 text-left text-sm hover:bg-muted/50",
                        expanded && !fileKey && "bg-muted",
                      )}
                    >
                      {expanded ? (
                        <ChevronDown className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                      ) : (
                        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
                      )}
                      <TemplateThumb template={t} className="mt-px" />
                      <span className="flex-1">
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="font-medium">{t.name}</span>
                          {t.is_default && <Badge className="text-[10px]">Default</Badge>}
                          {t.archived_at && (
                            <Badge variant="outline" className="text-[10px] text-muted-foreground">
                              Archived
                            </Badge>
                          )}
                          {!t.is_available && (
                            <Badge variant="outline" className="text-[10px] text-muted-foreground">
                              Hidden
                            </Badge>
                          )}
                        </span>
                        <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span className="font-mono">{t.template_key ?? "custom"}</span>
                          <span>
                            · {fileCountFor(t)} of {totalFileCount()} files
                          </span>
                        </span>
                      </span>
                      {canManage && (
                        <span className="flex shrink-0 items-center gap-0.5">
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title="Duplicate"
                            aria-label="Duplicate"
                            onClick={(e) => {
                              e.stopPropagation();
                              void duplicateTemplate(t);
                            }}
                          >
                            <Copy className="size-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            title={t.is_available ? "Hide from new-project picker" : "Make available again"}
                            aria-label={t.is_available ? "Hide" : "Unhide"}
                            onClick={(e) => {
                              e.stopPropagation();
                              void toggleAvailable(t);
                            }}
                          >
                            {t.is_available ? (
                              <Eye className="size-3.5" />
                            ) : (
                              <EyeOff className="size-3.5" />
                            )}
                          </Button>
                        </span>
                      )}
                    </div>

                    {expanded && contents && (
                      <TemplateFileTree
                        contents={contents}
                        activeKey={fileKey}
                        onSelect={(key) => select(t.id, key)}
                      />
                    )}
                    {expanded && !contents && (
                      <p className="pb-2 pl-8 text-xs text-muted-foreground">Loading files…</p>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </aside>

        <div className="min-w-0 flex-1 overflow-y-auto rounded-md border p-4">
          {!selected ? (
            <p className="text-sm text-muted-foreground">
              {templates.length === 0
                ? "Copy a template from the superadmin catalog to get started."
                : "Select a template on the left."}
            </p>
          ) : (
            <div className="flex h-full flex-col gap-4">
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <TemplateThumb template={selected} />
                    <h2 className="text-base font-semibold">{selected.name}</h2>
                    {selected.template_key && (
                      <Badge variant="outline" className="font-mono text-[11px]">
                        {selected.template_key}
                      </Badge>
                    )}
                    {canManage && (
                      <Button variant="ghost" size="sm" onClick={() => setDetailsOpen(true)}>
                        Edit details
                      </Button>
                    )}
                    {selected.is_default ? (
                      <Badge className="text-[11px]">Default</Badge>
                    ) : (
                      canManage && (
                        <Button variant="outline" size="sm" onClick={() => void setDefault(selected)}>
                          Make default
                        </Button>
                      )
                    )}
                  </div>
                  {canManage && (
                    <div className="flex gap-2">
                      <TemplateZipButtons
                        contents={contents}
                        name={selected.name}
                        onImport={(plan) => applyImport(selected, plan)}
                      />
                      <Button variant="outline" size="sm" onClick={() => void toggleArchived(selected)}>
                        {selected.archived_at ? "Unarchive" : "Archive"}
                      </Button>
                      {!selected.is_default && (
                        <Button variant="outline" size="sm" onClick={() => void deleteTemplate(selected)}>
                          Delete
                        </Button>
                      )}
                    </div>
                  )}
                </div>
                {selected.description.trim() && (
                  <MarkdownView className="mt-1 max-w-3xl text-muted-foreground">
                    {selected.description}
                  </MarkdownView>
                )}
              </div>

              {!contents ? (
                <p className="text-sm text-muted-foreground">Loading files…</p>
              ) : activeFile ? (
                <TemplateFileEditor
                  key={`${selected.id}:${activeFile.key}`}
                  file={activeFile}
                  value={contentFor(contents, activeFile.key)}
                  canManage={canManage}
                  orgId={orgId}
                  onSave={(text) => saveFile(selected, activeFile.key, text)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Select a file on the left to {canManage ? "edit" : "view"} it.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <Dialog open={catalogOpen} onOpenChange={setCatalogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Copy from Catalog</DialogTitle>
            <DialogDescription>
              Copies the template into this org as your own editable version —
              editing it never changes the superadmin&apos;s original.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            {globalTemplates.length === 0 && (
              <p className="text-sm text-muted-foreground">No templates published yet.</p>
            )}
            {globalTemplates.map((t) => (
              <TemplateStaticRow
                key={t.id}
                template={t}
                keyBadge={
                  <Badge variant="outline" className="font-mono text-[10.5px]">
                    {t.key}
                  </Badge>
                }
                meta={
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={copiedKeys.has(t.key)}
                    onClick={() => void copyIn(t)}
                  >
                    {copiedKeys.has(t.key) ? "Copied" : "Copy"}
                  </Button>
                }
              />
            ))}
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" size="sm" />}>Close</DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <TemplateDetailsDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        scope="org"
        mode="create"
        initial={{ name: "", key: "", category: "", description: "" }}
        categories={categoriesInUse}
        onSave={createFromDetails}
      />
      {selected && (
        <TemplateDetailsDialog
          open={detailsOpen}
          onOpenChange={setDetailsOpen}
          scope="org"
          mode="edit"
          initial={{
            name: selected.name,
            key: selected.template_key ?? "",
            category: selected.category,
            description: selected.description,
          }}
          imagePath={selected.image_path}
          updatedAt={selected.updated_at}
          isDefault={selected.is_default}
          categories={categoriesInUse}
          onSave={(values, cover) => saveDetails(selected, values, cover)}
        />
      )}
    </div>
  );
}
