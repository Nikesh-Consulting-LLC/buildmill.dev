"use client";

// Phase 67 (us-67.2): the superadmin's project templates — a named bundle of
// guideline sections, per-run-kind worker instructions, and the two
// project-shaped thinking prompts (test-case elaboration, deploy-script
// generation) that a new project silently inherits a copy of (us-67.1).
// Exactly one template is the Default every org/project falls back to.
//
// Layout: templates down the left, each expandable to its section tree
// (Guideline sections / Worker instructions / Prompts); the right pane shows
// exactly one section's editor at a time — the shared MarkdownEditor's
// Write/Preview tabs — for whichever section is selected in the tree.

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronRight, Copy, EyeOff, Eye, Plus } from "lucide-react";

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
import { MarkdownEditor } from "@/components/markdown-editor";
import { cn } from "@/lib/utils";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { KIND_FILES } from "@/lib/instruction-files";

type Template = {
  id: string;
  key: string;
  name: string;
  description: string;
  category: string;
  is_default: boolean;
  is_disabled: boolean;
  version: number;
  section_count: number;
};

type SectionType = "guideline" | "worker_instruction" | "prompt";

type Section = {
  section_type: SectionType;
  section_key: string;
  title: string;
  content: string;
  sort_order: number;
};

// us-99.6: derived, not hand-listed. This constant omitted all five kinds
// Phase 96 added (chore, bug_rca, bug_fix, standalone_plan, standalone_code)
// in TWO verbatim copies, so a template could not carry per-type
// instructions even though every project's own editor exposes them — new
// projects silently fell through to the factory default for exactly the
// kinds that exist to give each type its own words.
const WORKER_INSTRUCTION_KINDS = Object.keys(KIND_FILES);
// us-100.4: retired from the EDITOR, not from the database. These are
// server-side LLM prompts (llm.LLM_FUNCTIONS) that no agent reads and that
// are platform-global — they do not belong in a project template. Existing
// rows are deliberately left in place (migration 265 deletes nothing), so
// this is reversible by reverting this commit rather than restoring a backup.
const PROMPT_KINDS: string[] = [];
const PROMPT_LABELS: Record<string, string> = {
  test_case_elaborate: "Test-case elaboration",
  deploy_script_generate: "Deploy-script generation",
};

const NEW_GUIDELINE = "new-guideline";

function sectionParam(type: SectionType, key: string) {
  return `${type}:${key}`;
}

export default function AdminProjectTemplatesPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");
  const sectionRef = searchParams.get("section"); // "type:key" | "new-guideline" | null

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
      const res = await apiCall(`/api/v1/admin/project-templates/${selectedId}/sections`);
      setSections(res ?? []);
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

  function select(templateId: string, section?: string) {
    const q = section ? `?id=${templateId}&section=${section}` : `?id=${templateId}`;
    router.push(`/admin/project-templates${q}`);
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

  async function reload() {
    await Promise.all([loadTemplates(), loadSections()]);
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
      description: "This removes the template and every section in it. Orgs that already copied it keep their own copy.",
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

  const selected = templates?.find((t) => t.id === selectedId) ?? null;
  const guidelineSections = (sections ?? []).filter((s) => s.section_type === "guideline");

  function sectionFor(type: SectionType, key: string): Section {
    return (
      sections?.find((s) => s.section_type === type && s.section_key === key) ?? {
        section_type: type,
        section_key: key,
        title: "",
        content: "",
        sort_order: 0,
      }
    );
  }

  const [activeType, activeKey] = sectionRef?.includes(":")
    ? sectionRef.split(":", 2)
    : [null, null];

  return (
    <div className="flex h-[calc(100vh-8rem)] min-h-[600px] w-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">
            Project templates
          </h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            A named bundle of guideline sections, worker instructions, and
            project-shaped prompts. A new project silently inherits a copy of
            the org&apos;s default template — editing here changes no
            existing project.
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
                        expanded && !sectionRef && "bg-muted",
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
                          <span>· {t.section_count} sections</span>
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

                    {expanded && (
                      <div className="pb-2 pl-8 pr-2">
                        <SectionGroup label="Guideline sections">
                          {guidelineSections.map((s) => (
                            <SectionRow
                              key={s.section_key}
                              label={s.title || s.section_key}
                              active={activeType === "guideline" && activeKey === s.section_key}
                              onClick={() => select(t.id, sectionParam("guideline", s.section_key))}
                            />
                          ))}
                          <button
                            type="button"
                            onClick={() => select(t.id, NEW_GUIDELINE)}
                            className={cn(
                              "flex w-full items-center gap-1 rounded px-2 py-1 text-left text-xs text-muted-foreground hover:bg-muted/50",
                              sectionRef === NEW_GUIDELINE && "bg-muted text-foreground",
                            )}
                          >
                            <Plus className="size-3" /> Add guideline section
                          </button>
                        </SectionGroup>

                        <SectionGroup label="Worker instructions">
                          {WORKER_INSTRUCTION_KINDS.map((kind) => (
                            <SectionRow
                              key={kind}
                              label={kind}
                              mono
                              active={activeType === "worker_instruction" && activeKey === kind}
                              onClick={() => select(t.id, sectionParam("worker_instruction", kind))}
                            />
                          ))}
                        </SectionGroup>

                        <SectionGroup label="Prompts">
                          {PROMPT_KINDS.map((kind) => (
                            <SectionRow
                              key={kind}
                              label={PROMPT_LABELS[kind] ?? kind}
                              active={activeType === "prompt" && activeKey === kind}
                              onClick={() => select(t.id, sectionParam("prompt", kind))}
                            />
                          ))}
                        </SectionGroup>
                      </div>
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
                  {!selected.is_default && (
                    <Button variant="outline" size="sm" onClick={() => void deleteTemplate(selected)}>
                      Delete
                    </Button>
                  )}
                </div>
                {selected.description && (
                  <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                    {selected.description}
                  </p>
                )}
              </div>

              {sections === null ? (
                <p className="text-sm text-muted-foreground">Loading sections…</p>
              ) : sectionRef === NEW_GUIDELINE ? (
                <AddGuidelineSection
                  templateId={selected.id}
                  onAdded={async (key) => {
                    await reload();
                    select(selected.id, sectionParam("guideline", key));
                  }}
                />
              ) : activeType && activeKey ? (
                <SectionEditor
                  key={sectionRef ?? ""}
                  templateId={selected.id}
                  section={
                    activeType === "guideline"
                      ? guidelineSections.find((s) => s.section_key === activeKey) ??
                        sectionFor("guideline", activeKey)
                      : sectionFor(activeType as SectionType, activeKey)
                  }
                  showTitle={activeType === "guideline"}
                  label={
                    activeType === "prompt"
                      ? PROMPT_LABELS[activeKey] ?? activeKey
                      : activeKey
                  }
                  onSaved={reload}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Select a section on the left to edit it.
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
              Starts with no sections — add guideline, worker-instruction and
              prompt content from the editor.
            </DialogDescription>
          </DialogHeader>
          <CreateForm
            onCreated={async (id) => {
              setCreateOpen(false);
              await loadTemplates();
              select(id);
            }}
            onCancel={() => setCreateOpen(false)}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SectionGroup({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <div className="mt-2 first:mt-1">
      <p className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <div className="flex flex-col">{children}</div>
    </div>
  );
}

function SectionRow({
  label,
  active,
  mono,
  onClick,
}: {
  label: string;
  active: boolean;
  mono?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "truncate rounded px-2 py-1 text-left text-xs hover:bg-muted/50",
        mono && "font-mono",
        active && "bg-primary/10 font-medium text-primary",
      )}
    >
      {label}
    </button>
  );
}

function SectionEditor({
  templateId,
  section,
  label,
  showTitle,
  onSaved,
}: {
  templateId: string;
  section: Section;
  label?: string;
  showTitle?: boolean;
  onSaved: () => Promise<void>;
}) {
  const [title, setTitle] = useState(section.title);
  const [content, setContent] = useState(section.content);
  const [saving, setSaving] = useState(false);
  const dirty = title !== section.title || content !== section.content;

  async function save() {
    setSaving(true);
    try {
      await apiCall(
        `/api/v1/admin/project-templates/${templateId}/sections/${section.section_type}/${section.section_key}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, content, sort_order: section.sort_order }),
        },
      );
      toastSuccess("Saved", `${label ?? title ?? section.section_key} updated.`);
      await onSaved();
    } catch (e) {
      toastError("Could not save", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        {showTitle ? (
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Section title"
            className="rounded-md border bg-background px-2 py-1 text-sm font-medium"
          />
        ) : (
          <span className="font-mono text-sm font-medium">{label}</span>
        )}
        <Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>
      <MarkdownEditor
        value={content}
        onChange={setContent}
        placeholder="(blank)"
        rows={20}
        defaultTab="preview"
        className="min-h-0 flex-1 overflow-y-auto"
      />
    </div>
  );
}

function AddGuidelineSection({
  templateId,
  onAdded,
}: {
  templateId: string;
  onAdded: (key: string) => Promise<void>;
}) {
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  async function add() {
    setSaving(true);
    try {
      await apiCall(
        `/api/v1/admin/project-templates/${templateId}/sections/guideline/${key.trim()}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title, content, sort_order: 0 }),
        },
      );
      await onAdded(key.trim());
    } catch (e) {
      toastError("Could not add section", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="grid gap-2 md:grid-cols-2">
        <input
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="section key, e.g. tech-stack"
          className="rounded-md border bg-background px-2 py-1 font-mono text-sm"
        />
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Title"
          className="rounded-md border bg-background px-2 py-1 text-sm"
        />
      </div>
      <MarkdownEditor
        value={content}
        onChange={setContent}
        placeholder="Content"
        rows={18}
        className="min-h-0 flex-1 overflow-y-auto"
      />
      <div className="flex justify-end">
        <Button size="sm" disabled={!key.trim() || saving} onClick={() => void add()}>
          {saving ? "Adding…" : "Add section"}
        </Button>
      </div>
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
