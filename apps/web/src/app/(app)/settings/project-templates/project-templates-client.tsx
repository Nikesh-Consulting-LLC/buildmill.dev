"use client";

// Phase 67 (us-67.3): mirrors the superadmin's project-templates layout —
// a template list down the left, each expandable to its section tree, with
// a single full-height Write/Preview editor on the right for whichever
// section is selected. Every mutating control is disabled for a caller
// without manage_project; the database enforces the same gate via RLS on
// org_project_templates/org_project_template_sections, so a disabled button
// here is a courtesy, not the real boundary.

import { Fragment, useCallback, useEffect, useState, type ReactNode } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { ChevronDown, ChevronRight, Copy, Eye, EyeOff, Plus } from "lucide-react";

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
import { MarkdownEditor } from "@/components/markdown-editor";
import { cn } from "@/lib/utils";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { KIND_FILES } from "@/lib/instruction-files";

type GlobalTemplate = {
  id: string;
  key: string;
  name: string;
  description: string;
  category: string;
  is_default: boolean;
};

type OrgTemplate = {
  id: string;
  template_key: string | null;
  name: string;
  description: string;
  is_default: boolean;
  is_available: boolean;
  archived_at: string | null;
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
const PROMPT_KINDS = ["test_case_elaborate", "deploy_script_generate"];
const PROMPT_LABELS: Record<string, string> = {
  test_case_elaborate: "Test-case elaboration",
  deploy_script_generate: "Deploy-script generation",
};
const NEW_GUIDELINE = "new-guideline";

function sectionParam(type: SectionType, key: string) {
  return `${type}:${key}`;
}

export function ProjectTemplatesClient({
  orgId,
  canManage,
  globalTemplates,
  orgTemplates: initialOrgTemplates,
  sectionCounts,
}: {
  orgId: string;
  canManage: boolean;
  globalTemplates: GlobalTemplate[];
  orgTemplates: OrgTemplate[];
  sectionCounts: { org_template_id: string }[];
}) {
  const supabase = createClient();
  const router = useRouter();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("id");
  const sectionRef = searchParams.get("section");

  const [templates, setTemplates] = useState<OrgTemplate[]>(initialOrgTemplates);
  const [counts, setCounts] = useState<Record<string, number>>(() => {
    const c: Record<string, number> = {};
    for (const s of sectionCounts) c[s.org_template_id] = (c[s.org_template_id] ?? 0) + 1;
    return c;
  });
  const [sections, setSections] = useState<Section[] | null>(null);
  const [collapsedIds, setCollapsedIds] = useState<Set<string>>(new Set());
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [catalogOpen, setCatalogOpen] = useState(false);

  const reloadTemplates = useCallback(async () => {
    const { data } = await supabase
      .from("org_project_templates")
      .select("id, template_key, name, description, is_default, is_available, archived_at")
      .eq("org_id", orgId)
      .order("sort_order", { ascending: true });
    const list = data ?? [];
    setTemplates(list);
    const ids = list.map((t) => t.id);
    if (ids.length) {
      const { data: secs } = await supabase
        .from("org_project_template_sections")
        .select("org_template_id")
        .in("org_template_id", ids);
      const c: Record<string, number> = {};
      for (const s of secs ?? []) c[s.org_template_id] = (c[s.org_template_id] ?? 0) + 1;
      setCounts(c);
    }
  }, [supabase, orgId]);

  const loadSections = useCallback(async () => {
    if (!selectedId) return;
    const { data } = await supabase
      .from("org_project_template_sections")
      .select("section_type, section_key, title, content, sort_order")
      .eq("org_template_id", selectedId);
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

  function select(templateId: string, section?: string) {
    const q = section ? `?id=${templateId}&section=${section}` : `?id=${templateId}`;
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
      .insert({ org_id: orgId, template_key: null, name, description: t.description })
      .select("id")
      .single();
    if (error || !created) return toastError("Could not duplicate", error?.message ?? "unknown error");

    const { data: srcSections } = await supabase
      .from("org_project_template_sections")
      .select("section_type, section_key, title, content, sort_order")
      .eq("org_template_id", t.id);
    if (srcSections?.length) {
      await supabase.from("org_project_template_sections").insert(
        srcSections.map((s) => ({ ...s, org_template_id: created.id, org_id: orgId })),
      );
    }
    toastSuccess("Duplicated", `${name} is ready to fine-tune.`);
    await reloadTemplates();
    select(created.id);
  }

  async function deleteTemplate(t: OrgTemplate) {
    const ok = await confirmDialog({
      title: `Delete ${t.name}?`,
      description: "This removes the template and every section in it from this org.",
      confirmLabel: "Delete",
      destructive: true,
    });
    if (!ok) return;
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

  async function renameTemplate(t: OrgTemplate) {
    const name = renameValue.trim();
    if (!name || name === t.name) {
      setRenamingId(null);
      return;
    }
    const { error } = await supabase.from("org_project_templates").update({ name }).eq("id", t.id);
    if (error) return toastError("Could not rename", error.message);
    toastSuccess("Renamed", `Now called ${name}.`);
    setRenamingId(null);
    await reloadTemplates();
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

  async function createCustom() {
    const name = window.prompt("Name for the new template?");
    if (!name?.trim()) return;
    const { data: created, error } = await supabase
      .from("org_project_templates")
      .insert({ org_id: orgId, template_key: null, name: name.trim() })
      .select("id")
      .single();
    if (error || !created) return toastError("Could not create template", error?.message ?? "unknown error");
    toastSuccess("Created", `${name.trim()} is ready to fill in.`);
    await reloadTemplates();
    select(created.id);
  }

  const selected = templates.find((t) => t.id === selectedId) ?? null;
  const guidelineSections = (sections ?? []).filter((s) => s.section_type === "guideline");
  const copiedKeys = new Set(templates.filter((t) => t.template_key).map((t) => t.template_key));

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
            A named bundle of guideline sections, worker instructions and
            project-shaped prompts. A new project in this org silently
            inherits a copy of the default template below.
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
            <Button size="sm" onClick={() => void createCustom()}>
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
                          <span>· {counts[t.id] ?? 0} sections</span>
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
                          {canManage && (
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
                          )}
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
              {templates.length === 0
                ? "Copy a template from the superadmin catalog to get started."
                : "Select a template on the left."}
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
                        {selected.template_key && (
                          <Badge variant="outline" className="font-mono text-[11px]">
                            {selected.template_key}
                          </Badge>
                        )}
                        {canManage && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => {
                              setRenamingId(selected.id);
                              setRenameValue(selected.name);
                            }}
                          >
                            Rename
                          </Button>
                        )}
                      </>
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
                {selected.description && (
                  <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
                    {selected.description}
                  </p>
                )}
              </div>

              {sections === null ? (
                <p className="text-sm text-muted-foreground">Loading sections…</p>
              ) : sectionRef === NEW_GUIDELINE && canManage ? (
                <AddGuidelineSection
                  orgTemplateId={selected.id}
                  orgId={orgId}
                  onAdded={async (key) => {
                    await reload();
                    select(selected.id, sectionParam("guideline", key));
                  }}
                />
              ) : activeType && activeKey ? (
                <SectionEditor
                  key={sectionRef ?? ""}
                  orgTemplateId={selected.id}
                  orgId={orgId}
                  canManage={canManage}
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
                  Select a section on the left to view it.
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
              <div key={t.id} className="flex items-center justify-between gap-2 rounded-md border p-2">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">{t.name}</span>
                    <Badge variant="outline" className="font-mono text-[11px]">{t.key}</Badge>
                    {t.is_default && <Badge className="text-[11px]">Default</Badge>}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={copiedKeys.has(t.key)}
                  onClick={() => void copyIn(t)}
                >
                  {copiedKeys.has(t.key) ? "Copied" : "Copy"}
                </Button>
              </div>
            ))}
          </div>
          <DialogFooter>
            <DialogClose render={<Button variant="outline" size="sm" />}>Close</DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SectionGroup({ label, children }: { label: string; children: ReactNode }) {
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
  orgTemplateId,
  orgId,
  canManage,
  section,
  label,
  showTitle,
  onSaved,
}: {
  orgTemplateId: string;
  orgId: string;
  canManage: boolean;
  section: Section;
  label?: string;
  showTitle?: boolean;
  onSaved: () => Promise<void>;
}) {
  const supabase = createClient();
  const [title, setTitle] = useState(section.title);
  const [content, setContent] = useState(section.content);
  const [saving, setSaving] = useState(false);
  const dirty = title !== section.title || content !== section.content;

  async function save() {
    setSaving(true);
    const { error } = await supabase.from("org_project_template_sections").upsert(
      {
        org_template_id: orgTemplateId,
        org_id: orgId,
        section_type: section.section_type,
        section_key: section.section_key,
        title,
        content,
        sort_order: section.sort_order,
      },
      { onConflict: "org_template_id,section_type,section_key" },
    );
    setSaving(false);
    if (error) return toastError("Could not save", error.message);
    toastSuccess("Saved", `${label ?? title ?? section.section_key} updated.`);
    await onSaved();
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        {showTitle ? (
          <input
            value={title}
            disabled={!canManage}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Section title"
            className="rounded-md border bg-background px-2 py-1 text-sm font-medium disabled:opacity-60"
          />
        ) : (
          <span className="font-mono text-sm font-medium">{label}</span>
        )}
        {canManage && (
          <Button size="sm" disabled={!dirty || saving} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </Button>
        )}
      </div>
      <MarkdownEditor
        value={content}
        onChange={setContent}
        placeholder="(blank)"
        rows={20}
        defaultTab="preview"
        disabled={!canManage}
        className="min-h-0 flex-1 overflow-y-auto"
      />
    </div>
  );
}

function AddGuidelineSection({
  orgTemplateId,
  orgId,
  onAdded,
}: {
  orgTemplateId: string;
  orgId: string;
  onAdded: (key: string) => Promise<void>;
}) {
  const supabase = createClient();
  const [key, setKey] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  async function add() {
    setSaving(true);
    const { error } = await supabase.from("org_project_template_sections").upsert(
      {
        org_template_id: orgTemplateId,
        org_id: orgId,
        section_type: "guideline",
        section_key: key.trim(),
        title,
        content,
        sort_order: 0,
      },
      { onConflict: "org_template_id,section_type,section_key" },
    );
    setSaving(false);
    if (error) return toastError("Could not add section", error.message);
    await onAdded(key.trim());
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
