"use client";

// Phase 67 (us-67.2) → Phase 100 (us-100.4): the superadmin's project
// templates. A template is the contents of the files a new project will
// publish — the AGENTS.md body (Agent Instructions) and one `.buildmill/*.md`
// per task kind — and nothing else. Exactly one template is the Default
// every org/project falls back to.
//
// Layout: templates down the left, each expandable to its file tree; the
// right pane edits exactly one file at a time (the shared MarkdownEditor's
// Write/Preview tabs). The tree and the editor are the same components the
// org's copies use (`template-files-editor.tsx`), so a superadmin authoring
// here sees what a manager will get.
//
// Storage is unchanged from Phase 67: the document is
// `project_templates.agent_instructions` (migration 265) and each per-task
// file is a `worker_instruction` section keyed by kind. The `guideline` and
// `prompt` section rows that Phase 67 also stored are deliberately left in
// the database (migration 265 deletes nothing) but are no longer offered
// here: guideline sections became the document (us-100.1) and prompt
// sections are platform-global LLM prompts that no agent reads. Those still
// live at /admin/prompt-templates.

import { useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronRight, Copy, EyeOff, Eye } from "lucide-react";

import { apiCall } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { MarkdownView } from "@/components/markdown-view";
import { TemplateThumb } from "@/components/template-card";
import {
  TemplateDetailsDialog,
  type CoverChange,
  type TemplateDetailsValues,
} from "@/components/template-details-dialog";
import {
  TEMPLATE_IMAGE_BUCKET,
  builtinCoverPath,
  catalogCoverObject,
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

type Template = {
  id: string;
  key: string;
  name: string;
  description: string;
  category: string;
  is_default: boolean;
  is_disabled: boolean;
  version: number;
  agent_instructions: string;
  file_count: number;
  // US-118.1: the face
  image_path: string | null;
  updated_at: string;
};

type Section = {
  section_type: string;
  section_key: string;
  content: string;
};

export default function AdminProjectTemplatesPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");
  const fileKey = searchParams.get("file"); // "agents" | <run kind> | null

  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [sections, setSections] = useState<Section[] | null>(null);
  // The selected template's tree is expanded by default; toggled shut here
  // without losing the selection or the editor on the right.
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  // US-118.1: the details dialog (name, key, category, description, cover)
  // replaced the inline rename.
  const [detailsOpen, setDetailsOpen] = useState(false);

  const loadTemplates = useCallback(async () => {
    try {
      const res = await apiCall("/api/v1/admin/project-templates");
      setTemplates(res ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void loadTemplates();
  }, [loadTemplates]);

  const loadSections = useCallback(async () => {
    if (!selectedId) return;
    try {
      const res: Section[] = await apiCall(
        `/api/v1/admin/project-templates/${selectedId}/sections`,
      );
      setSections((res ?? []).filter((s) => s.section_type === "worker_instruction"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, [selectedId]);

  useEffect(() => {
    setSections(null);
    void loadSections();
  }, [loadSections]);

  // Default to the first template once the list loads, if none is selected.
  useEffect(() => {
    if (!selectedId && templates && templates.length > 0) {
      router.replace(`/admin/project-templates?id=${templates[0].id}`);
    }
  }, [selectedId, templates, router]);

  function select(templateId: string, file?: string) {
    const q = file ? `?id=${templateId}&file=${file}` : `?id=${templateId}`;
    router.push(`/admin/project-templates${q}`);
  }

  async function reload() {
    await Promise.all([loadTemplates(), loadSections()]);
  }

  async function setDefault(t: Template) {
    try {
      await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_default: true }),
      });
      toastSuccess("Default set", `${t.name} is now the default project template.`);
      await loadTemplates();
    } catch (e) {
      toastError("Could not set default", (e as Error).message);
    }
  }

  async function toggleDisabled(t: Template) {
    try {
      await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_disabled: !t.is_disabled }),
      });
      toastSuccess(
        t.is_disabled ? "Enabled" : "Disabled",
        t.is_disabled
          ? `${t.name} is visible to orgs again.`
          : `${t.name} is hidden from every org — orgs already using a copy keep it.`,
      );
      await loadTemplates();
    } catch (e) {
      toastError("Could not update", (e as Error).message);
    }
  }

  async function duplicateTemplate(t: Template) {
    try {
      const created = await apiCall(`/api/v1/admin/project-templates/${t.id}/duplicate`, {
        method: "POST",
      });
      toastSuccess("Duplicated", `${created.name} is ready to fill in.`);
      await loadTemplates();
      select(created.id);
    } catch (e) {
      toastError("Could not duplicate", (e as Error).message);
    }
  }

  async function deleteTemplate(t: Template) {
    const ok = await confirmDialog({
      title: `Delete ${t.name}?`,
      description:
        "This removes the template and every file in it. Orgs that already copied it keep their own copy.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
    try {
      await apiCall(`/api/v1/admin/project-templates/${t.id}`, { method: "DELETE" });
      toastSuccess("Deleted", `${t.name} is gone.`);
      const remaining = (templates ?? []).filter((x) => x.id !== t.id);
      await loadTemplates();
      if (selectedId === t.id) {
        if (remaining.length > 0) select(remaining[0].id);
        else router.push("/admin/project-templates");
      }
    } catch (e) {
      toastError("Could not delete", (e as Error).message);
    }
  }

  /** US-118.1: resolve the cover intent into an `image_path` for the row.
   * Uploads go browser → Storage under RLS (the admin is an authenticated
   * user; `is_platform_admin()` holds); the API only records the path. An
   * uploaded object that is being replaced by a built-in, or removed, is
   * deleted best-effort — never a hard failure, the row is what matters. */
  async function resolveCatalogCover(
    templateId: string,
    current: string | null,
    cover: CoverChange,
  ): Promise<{ image_path?: string | null }> {
    if (cover.kind === "keep") return {};
    const supabase = createClient();
    const objectPath = catalogCoverObject(templateId);
    const hadUpload = current === objectPath;
    if (cover.kind === "upload") {
      const { error } = await supabase.storage
        .from(TEMPLATE_IMAGE_BUCKET)
        .upload(objectPath, cover.file, { upsert: true, contentType: cover.file.type });
      if (error) throw new Error(`Upload failed: ${error.message}`);
      return { image_path: objectPath };
    }
    if (hadUpload) {
      await supabase.storage.from(TEMPLATE_IMAGE_BUCKET).remove([objectPath]).catch(() => null);
    }
    if (cover.kind === "builtin") return { image_path: builtinCoverPath(cover.name) };
    return { image_path: null };
  }

  async function saveDetails(t: Template, values: TemplateDetailsValues, cover: CoverChange) {
    const patch: Record<string, unknown> = {};
    if (values.name !== t.name) patch.name = values.name;
    if (values.key !== t.key) patch.key = values.key;
    if (values.category !== t.category) patch.category = values.category;
    if (values.description !== t.description) patch.description = values.description;
    Object.assign(patch, await resolveCatalogCover(t.id, t.image_path, cover));
    if (Object.keys(patch).length > 0) {
      await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
    }
    toastSuccess("Saved", `${values.name} updated.`);
    await loadTemplates();
  }

  async function createFromDetails(values: TemplateDetailsValues, cover: CoverChange) {
    const created: Template = await apiCall("/api/v1/admin/project-templates", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        key: values.key,
        name: values.name,
        category: values.category,
        description: values.description,
      }),
    });
    // A cover picked before Create is uploaded once the row exists, then
    // patched on — the object path needs the id.
    const coverPatch = await resolveCatalogCover(created.id, null, cover);
    if (coverPatch.image_path !== undefined) {
      await apiCall(`/api/v1/admin/project-templates/${created.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(coverPatch),
      });
    }
    toastSuccess("Created", `${values.name} is ready to fill in.`);
    await loadTemplates();
    select(created.id, AGENTS_KEY);
  }

  /** Save one file. The document goes to the template row; a per-task file
   * goes to its `worker_instruction` section — or is deleted when blanked,
   * because a stored empty string would win over the factory default at
   * project creation (`seed_worker_instructions` coalesces on it). */
  async function saveFile(t: Template, key: string, text: string): Promise<boolean> {
    const file = templateFileForKey(key);
    try {
      if (key === AGENTS_KEY) {
        await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_instructions: text }),
        });
      } else if (text.trim() === "") {
        await apiCall(
          `/api/v1/admin/project-templates/${t.id}/sections/worker_instruction/${key}`,
          { method: "DELETE" },
        );
      } else {
        await apiCall(
          `/api/v1/admin/project-templates/${t.id}/sections/worker_instruction/${key}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title: "", content: text, sort_order: 0 }),
          },
        );
      }
      toastSuccess("Saved", `${file?.path ?? key} updated.`);
      await reload();
      return true;
    } catch (e) {
      toastError("Could not save", (e as Error).message);
      return false;
    }
  }

  /** US-114.1: apply a confirmed zip import — the same writes `saveFile`
   * makes, one per changed file. A failure names the file it stopped on;
   * the template is reloaded either way so the tree shows what landed. */
  async function applyImport(t: Template, plan: ImportPlan) {
    try {
      for (const f of [...plan.overwrite, ...plan.cleared]) {
        const text = plan.cleared.includes(f) ? "" : f.text;
        try {
          if (f.key === AGENTS_KEY) {
            await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ agent_instructions: text }),
            });
          } else if (text === "") {
            await apiCall(
              `/api/v1/admin/project-templates/${t.id}/sections/worker_instruction/${f.key}`,
              { method: "DELETE" },
            );
          } else {
            await apiCall(
              `/api/v1/admin/project-templates/${t.id}/sections/worker_instruction/${f.key}`,
              {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ title: "", content: text, sort_order: 0 }),
              },
            );
          }
        } catch (e) {
          throw new Error(`${f.path}: ${(e as Error).message}`);
        }
      }
    } finally {
      await reload();
    }
  }

  const selected = templates?.find((t) => t.id === selectedId) ?? null;
  const categoriesInUse = Array.from(
    new Set((templates ?? []).map((t) => t.category).filter(Boolean)),
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

  return (
    <div className="flex h-[calc(100vh-8rem)] min-h-[600px] w-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Project templates
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            The files a new project starts with: its Agent Instructions
            (<span className="font-mono">AGENTS.md</span>) and one
            per-task instruction file under{" "}
            <span className="font-mono">.buildmill/</span>. A new project
            silently inherits a copy of the org&apos;s default template —
            editing here changes no existing project.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreateOpen(true)}>
          New template
        </Button>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex min-h-0 flex-1 gap-4">
        <aside className="w-80 shrink-0 overflow-y-auto rounded-md border">
          {templates === null ? (
            <p className="p-3 text-sm text-muted-foreground">Loading…</p>
          ) : templates.length === 0 ? (
            <p className="p-3 text-sm text-muted-foreground">No templates yet.</p>
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
                          {t.is_disabled && (
                            <Badge variant="outline" className="text-[10px] text-muted-foreground">
                              Disabled
                            </Badge>
                          )}
                        </span>
                        <span className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
                          <span className="font-mono">{t.key}</span>
                          <span>v{t.version}</span>
                          <span>
                            · {t.id === selectedId && contents
                              ? filledFileCount(contents)
                              : t.file_count}{" "}
                            of {totalFileCount()} files
                          </span>
                        </span>
                      </span>
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
                          disabled={t.is_default}
                          title={t.is_disabled ? "Enable — visible to orgs again" : "Disable — hide from every org"}
                          aria-label={t.is_disabled ? "Enable" : "Disable"}
                          onClick={(e) => {
                            e.stopPropagation();
                            void toggleDisabled(t);
                          }}
                        >
                          {t.is_disabled ? (
                            <EyeOff className="size-3.5" />
                          ) : (
                            <Eye className="size-3.5" />
                          )}
                        </Button>
                      </span>
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
              {templates === null ? "Loading…" : "Select a template on the left."}
            </p>
          ) : (
            <div className="flex h-full flex-col gap-4">
              <div>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <TemplateThumb template={selected} />
                    <h2 className="text-base font-semibold">{selected.name}</h2>
                    <Badge variant="outline" className="font-mono text-[11px]">{selected.key}</Badge>
                    <Button variant="ghost" size="sm" onClick={() => setDetailsOpen(true)}>
                      Edit details
                    </Button>
                    <Badge variant="secondary" className="text-[11px]">v{selected.version}</Badge>
                    {selected.is_default ? (
                      <Badge className="text-[11px]">Default</Badge>
                    ) : (
                      <Button variant="outline" size="sm" onClick={() => void setDefault(selected)}>
                        Make default
                      </Button>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <TemplateZipButtons
                      contents={contents}
                      name={selected.key}
                      onImport={(plan) => applyImport(selected, plan)}
                    />
                    {!selected.is_default && (
                      <Button variant="outline" size="sm" onClick={() => void deleteTemplate(selected)}>
                        Delete
                      </Button>
                    )}
                  </div>
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
                  canManage
                  onSave={(text) => saveFile(selected, activeFile.key, text)}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Select a file on the left to edit it.
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <TemplateDetailsDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        scope="catalog"
        mode="create"
        initial={{ name: "", key: "", category: "", description: "" }}
        categories={categoriesInUse}
        onSave={createFromDetails}
      />
      {selected && (
        <TemplateDetailsDialog
          open={detailsOpen}
          onOpenChange={setDetailsOpen}
          scope="catalog"
          mode="edit"
          initial={{
            name: selected.name,
            key: selected.key,
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
