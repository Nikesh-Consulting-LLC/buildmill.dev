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
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [renameKeyValue, setRenameKeyValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

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

  async function renameTemplate(t: Template) {
    const name = renameValue.trim();
    const key = renameKeyValue.trim();
    if (!name || !key || (name === t.name && key === t.key)) {
      setRenamingId(null);
      return;
    }
    try {
      await apiCall(`/api/v1/admin/project-templates/${t.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, key }),
      });
      toastSuccess("Renamed", `Now called ${name} (${key}).`);
      setRenamingId(null);
      await loadTemplates();
    } catch (e) {
      toastError("Could not rename", (e as Error).message);
    }
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
                    {renamingId === selected.id ? (
                      <>
                        <input
                          autoFocus
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void renameTemplate(selected);
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          className="rounded-md border bg-background px-2 py-1 text-base font-semibold"
                        />
                        <input
                          value={renameKeyValue}
                          onChange={(e) => setRenameKeyValue(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") void renameTemplate(selected);
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          placeholder="key"
                          className="w-32 rounded-md border bg-background px-2 py-1 font-mono text-xs"
                        />
                        <Button size="sm" onClick={() => void renameTemplate(selected)}>
                          Save
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => setRenamingId(null)}>
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <>
                        <h2 className="text-base font-semibold">{selected.name}</h2>
                        <Badge variant="outline" className="font-mono text-[11px]">{selected.key}</Badge>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => {
                            setRenamingId(selected.id);
                            setRenameValue(selected.name);
                            setRenameKeyValue(selected.key);
                          }}
                        >
                          Rename
                        </Button>
                      </>
                    )}
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
                {selected.description && (
                  <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                    {selected.description}
                  </p>
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

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>New project template</DialogTitle>
            <DialogDescription>
              Starts with every file blank — write the Agent Instructions and
              any per-task instructions from the editor.
            </DialogDescription>
          </DialogHeader>
          <CreateForm
            onCreated={async (id) => {
              setCreateOpen(false);
              await loadTemplates();
              select(id, AGENTS_KEY);
            }}
            onCancel={() => setCreateOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CreateForm({
  onCreated,
  onCancel,
}: {
  onCreated: (id: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [key, setKey] = useState("");
  const [name, setName] = useState("");
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);

  async function create() {
    setSaving(true);
    try {
      const res = await apiCall("/api/v1/admin/project-templates", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          key: key.trim(),
          name: name.trim(),
          category: category.trim(),
          description,
        }),
      });
      toastSuccess("Created", `${name} is ready to fill in.`);
      await onCreated(res.id);
    } catch (e) {
      toastError("Could not create", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-4 pt-2 text-sm">
      <label className="grid gap-1">
        <span className="text-muted-foreground">Key</span>
        <input
          value={key}
          maxLength={64}
          autoComplete="off"
          placeholder="web-app"
          onChange={(e) => setKey(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Name</span>
        <input
          value={name}
          maxLength={200}
          autoComplete="off"
          placeholder="Web app"
          onChange={(e) => setName(e.target.value)}
          className="rounded-md border bg-background px-2 py-1"
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Category</span>
        <input
          value={category}
          maxLength={100}
          autoComplete="off"
          placeholder="Web app"
          onChange={(e) => setCategory(e.target.value)}
          className="rounded-md border bg-background px-2 py-1"
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Description</span>
        <textarea
          rows={2}
          maxLength={2000}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-xs"
        />
      </label>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" size="sm" onClick={onCancel} />}>
          Cancel
        </DialogClose>
        <Button
          size="sm"
          disabled={saving || !key.trim() || !name.trim()}
          onClick={() => void create()}
        >
          {saving ? "Creating…" : "Create"}
        </Button>
      </DialogFooter>
    </div>
  );
}
