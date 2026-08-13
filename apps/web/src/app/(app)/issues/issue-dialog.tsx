"use client";

import { useRef, useState } from "react";
import { toastSuccess } from "@/components/ui/toast";
import { useRouter } from "@/lib/router-with-progress";
import { Loader2, Pencil, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useRememberedToggle } from "@/lib/use-remembered-toggle";
import { createClient } from "@/lib/supabase/client";
import { apiFetch } from "@/lib/api";
import {
  MAX_DOCUMENT_BYTES,
  formatBytes,
  uploadDocument,
} from "@/lib/documents";
import { Button } from "@/components/ui/button";
import { TYPE_ICONS } from "@/components/type-badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { MarkdownEditor } from "@/components/markdown-editor";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ISSUE_TYPES,
  TYPE_DESCRIPTIONS,
  TYPE_LABELS,
  composeBugBody,
  parseBugBody,
  requiresAcceptanceCriteria,
  supportsAcceptanceCriteria,
  type IssueType,
} from "@/lib/issue-body";
import {
  defaultEpicId as pickDefaultEpicId,
  epicPickerOptions,
} from "@/lib/epic-picker";
import { epicLabel } from "@/lib/work-items";

export type IssueFormData = {
  id: string;
  title: string;
  body: string | null;
  acceptance_criteria: string[];
  type: IssueType;
  status: string;
  epic_id: string | null;
};

/** US-14.4: `active` decides the default for a new item. Epics are the
 * numbering root (us-7.10), so an item created outside the active epic
 * gets the wrong readable id and counts against the wrong close-gate —
 * a silent failure from a field the manager never touched. */
export type EpicOption = {
  id: string;
  title: string;
  active?: boolean;
  /** US-20.3: `completed` epics are filtered out of the picker. */
  status?: string;
  /** US-71.1: shown in the option label and sorts the list newest-first. */
  number?: number | null;
};

/** US-20.3: sentinel value for the "create one" entry in the epic select. */
const NEW_EPIC = "__new_epic__";
export type ProjectOption = { id: string; name: string; org_id: string };

export function IssueDialog({
  orgId: orgIdProp,
  projectId: projectIdProp,
  issue,
  epics: epicsProp = [],
  parent,
  trigger,
  projects,
  epicsByProject,
}: {
  orgId: string;
  projectId: string;
  issue?: IssueFormData;
  /** Project epics for the (optional) epic selector. */
  epics?: EpicOption[];
  /** Preset when adding a story to a feature — locks type to "story",
   * inherits the parent's epic, and sets parent_id on create. */
  parent?: { id: string; epicId: string | null };
  /** Custom trigger element (e.g. an "Add story" button) — defaults to the
   * standard New issue / Edit button. */
  trigger?: React.ReactElement;
  /** US-8.2: cross-project create — when present (and not editing) the dialog
   * shows a Project picker; org id and epic options follow the choice. */
  projects?: ProjectOption[];
  epicsByProject?: Record<string, EpicOption[]>;
}) {
  const router = useRouter();
  const isEdit = !!issue;
  const [open, setOpen] = useState(false);
  // US-25.6: set by "Add another" just before the form submits, so one save
  // path serves both buttons — a second handler would drift from this one.
  const addAnotherRef = useRef(false);
  const titleRef = useRef<HTMLInputElement>(null);

  // US-8.2: picker mode only when a project list is supplied for a new item.
  const pickerMode = !isEdit && !!projects?.length;
  const [pickedProjectId, setPickedProjectId] = useState(projectIdProp);
  const projectId = pickerMode ? pickedProjectId : projectIdProp;
  const orgId = pickerMode
    ? projects!.find((p) => p.id === projectId)?.org_id ?? orgIdProp
    : orgIdProp;
  // US-20.3: epics created inline, kept with the project they were made in
  // so picker mode cannot offer one project's new epic under another.
  const [createdEpics, setCreatedEpics] = useState<
    (EpicOption & { projectId: string })[]
  >([]);
  const baseEpics = pickerMode
    ? epicsByProject?.[projectId] ?? []
    : epicsProp;
  const epics: EpicOption[] = [
    ...baseEpics,
    ...createdEpics
      .filter((e) => e.projectId === projectId)
      .map(({ projectId: _p, ...rest }) => rest),
  ];

  const [type, setType] = useState<IssueType>(
    issue?.type ?? (parent ? "story" : "feature")
  );
  const [title, setTitle] = useState(issue?.title ?? "");
  const [description, setDescription] = useState(
    issue && issue.type !== "bug" ? issue.body ?? "" : ""
  );
  const initialBug = parseBugBody(issue?.type === "bug" ? issue.body : null);
  const [repro, setRepro] = useState(initialBug.repro);
  const [expected, setExpected] = useState(initialBug.expected);
  const [criteria, setCriteria] = useState<string[]>(
    issue?.acceptance_criteria?.length ? issue.acceptance_criteria : [""]
  );
  // US-14.4: an existing item keeps its epic and a story under a parent
  // inherits the parent's; everything else is a *new* item, which belongs
  // in the epic the project is currently working — the active epic, or
  // (US-71.1, now that closing can leave none active) the newest open one.
  // "none" only when the project genuinely has no open epic.
  const projectDefaultEpicId = pickDefaultEpicId(epics);
  const [epicId, setEpicId] = useState<string>(
    issue?.epic_id ?? parent?.epicId ?? projectDefaultEpicId ?? "none"
  );

  // US-20.3: inline epic creation. A nested dialog would take focus and
  // EpicDialog's router.refresh() would discard this half-filled form.
  const [creatingEpic, setCreatingEpic] = useState(false);
  const [newEpicTitle, setNewEpicTitle] = useState("");
  const [epicBusy, setEpicBusy] = useState(false);
  const [epicError, setEpicError] = useState<string | null>(null);

  // US-14.4: in picker mode the epic list belongs to the chosen project,
  // so switching project must re-default — otherwise a dialog left open
  // across a switch files the item into another project's epic. Adjusted
  // during render (the repo forbids setState in an effect) by tracking
  // the project the current selection was made for.
  const [epicForProject, setEpicForProject] = useState(projectId);
  if (pickerMode && epicForProject !== projectId) {
    setEpicForProject(projectId);
    setEpicId(projectDefaultEpicId ?? "none");
    // US-20.3: a half-typed epic belongs to the project it was started in.
    if (creatingEpic) {
      setCreatingEpic(false);
      setNewEpicTitle("");
      setEpicError(null);
    }
  }
  // us-2.29: optional attachments, uploaded via the us-2.21 client path
  // after the work item is created. uploadFailures ≠ null means the item
  // exists but some files didn't land — the dialog stays open to say so.
  const [files, setFiles] = useState<File[]>([]);
  const [uploadFailures, setUploadFailures] = useState<string[] | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingChildEpicUpdate, setPendingChildEpicUpdate] = useState<{
    values: Record<string, unknown>;
    issueId: string;
    nextEpic: string | null;
    priorEpic: string | null;
    childCount: number;
  } | null>(null);

  /** US-14.10: a create dialog reopens on its defaults. The component stays
   * mounted between opens, so useState initialisers do not re-run and the
   * dialog came back holding whatever the last visit left behind — which
   * quietly made "Type defaults to Feature" true only the first time. Reset
   * during render (the repo forbids setState in an effect); editing is
   * exempt, since there the state IS the item. */
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open && !isEdit) {
      setType(parent ? "story" : "feature");
      setTitle("");
      setDescription("");
      setRepro("");
      setExpected("");
      setCriteria([""]);
      setFiles([]);
      setError(null);
      setCreatingEpic(false);
      setNewEpicTitle("");
      setEpicError(null);
      // US-71.1: re-default the epic too. The last visit's selection can
      // point at an epic closed since (US-71.1 makes that routine), and a
      // value absent from the options renders as its raw id.
      setEpicId(parent?.epicId ?? projectDefaultEpicId ?? "none");
    }
  }

  // us-2.2: type is only changeable while the issue is still a draft, and
  // never for a preset story (its type is fixed by the breakdown/add-story flow).
  const typeLocked = !!parent || (isEdit && issue!.status !== "draft");

  /** US-14.5: Basic asks for what the chosen type actually needs; Advanced
   * is every control there is. Remembered per user, so whichever way you
   * work is the way the dialog opens next time.
   *
   * Editing is always Advanced when the item carries anything Basic cannot
   * show — the same component edits existing items, and opening a filled-in
   * item in a two-field form reads as data loss. */
  const editHasAdvancedContent =
    isEdit &&
    !!(
      issue!.body?.trim() ||
      issue!.acceptance_criteria?.some((c) => c.trim())
    );
  const [rememberedAdvanced, setRememberedAdvanced] = useRememberedToggle(
    "sf-new-work-item-advanced",
    false
  );
  const advanced = editHasAdvancedContent || rememberedAdvanced;

  /** A feature's acceptance criteria are the PRD run's job (us-14.5): the
   * gate writes, reviews and approves them, so the form never asks. A
   * story's are mandatory (`requiresAcceptanceCriteria`) because it goes
   * straight to a plan run with no stage in between — so Basic reveals
   * that one field inline rather than failing validation on submit. */
  const criteriaOwnedByPrd = type === "feature";
  const showAcceptanceCriteria =
    supportsAcceptanceCriteria(type) &&
    !criteriaOwnedByPrd &&
    (advanced || requiresAcceptanceCriteria(type) || isEdit);
  const parentEpic = epics.find((e) => e.id === (parent?.epicId ?? ""));

  /** US-20.3: only open epics are offerable — with one exception. An item
   * already filed under a completed epic keeps it in the list, because
   * dropping it would make the select fall back to another value and
   * silently move the item on save, from a field nobody touched.
   * US-71.1: sorted newest-first and labeled "Epic N · Title". */
  const epicOptions = epicPickerOptions(epics, issue?.epic_id);

  const epicLabelFor = (e: EpicOption) => {
    const label = epicLabel(e.number, e.title);
    return e.status === "completed" ? `${label} (closed)` : label;
  };

  async function createEpicInline() {
    const title = newEpicTitle.trim();
    if (!title) {
      setEpicError("Give the epic a title.");
      return;
    }
    setEpicBusy(true);
    setEpicError(null);
    const supabase = createClient();
    const { data, error: dbError } = await supabase
      .from("epics")
      .insert({
        org_id: orgId,
        project_id: projectId,
        title,
        status: "open",
      })
      .select("id, title, status, number")
      .single();
    setEpicBusy(false);
    if (dbError || !data) {
      setEpicError(dbError?.message ?? "Could not create the epic.");
      return;
    }
    setCreatedEpics((prev) => [
      ...prev,
      {
        id: data.id,
        title: data.title,
        status: data.status,
        number: data.number,
        projectId,
      },
    ]);
    setEpicId(data.id);
    setCreatingEpic(false);
    setNewEpicTitle("");
  }

  function setCriterion(i: number, value: string) {
    setCriteria((prev) => prev.map((c, idx) => (idx === i ? value : c)));
  }

  function removeCriterion(i: number) {
    setCriteria((prev) =>
      prev.length > 1 ? prev.filter((_, idx) => idx !== i) : prev
    );
  }

  function addFiles(list: FileList | null) {
    if (!list?.length) return;
    const incoming = Array.from(list);
    const oversized = incoming.filter((f) => f.size > MAX_DOCUMENT_BYTES);
    if (oversized.length) {
      setError(
        `Too large (limit 25 MB per file): ${oversized
          .map((f) => f.name)
          .join(", ")}`
      );
    } else {
      setError(null);
    }
    setFiles((prev) => [
      ...prev,
      ...incoming.filter((f) => f.size <= MAX_DOCUMENT_BYTES),
    ]);
  }

  function removeFile(i: number) {
    setFiles((prev) => prev.filter((_, idx) => idx !== i));
  }

  async function writeEpicEvents(
    supabase: ReturnType<typeof createClient>,
    issueId: string,
    priorEpic: string | null,
    nextEpic: string | null
  ) {
    if (priorEpic === nextEpic) return;
    if (priorEpic && !nextEpic) {
      await supabase.from("issue_events").insert({
        org_id: orgId,
        issue_id: issueId,
        type: "epic-removed",
        payload: { epic_id: priorEpic },
      });
    } else if (nextEpic) {
      await supabase.from("issue_events").insert({
        org_id: orgId,
        issue_id: issueId,
        type: "epic-assigned",
        payload: { epic_id: nextEpic, from: priorEpic },
      });
    }
  }

  /** Returns the saved issue's id, or null when the write failed. */
  async function persistIssue(
    values: Record<string, unknown>,
    opts?: { updateChildrenEpic?: boolean; nextEpic?: string | null; priorEpic?: string | null }
  ): Promise<string | null> {
    const supabase = createClient();
    let issueId = issue?.id;
    const priorType = issue?.type;
    const priorEpic = issue?.epic_id ?? null;
    const nextEpic =
      opts?.nextEpic !== undefined
        ? opts.nextEpic
        : parent
          ? parent.epicId
          : ((values.epic_id as string | null | undefined) ?? null);

    if (isEdit) {
      const { error: dbError } = await supabase
        .from("issues")
        .update(values)
        .eq("id", issue!.id);
      if (dbError) {
        setError(dbError.message);
        return null;
      }
    } else {
      const { data, error: dbError } = await supabase
        .from("issues")
        .insert({
          ...values,
          org_id: orgId,
          project_id: projectId,
          ...(parent
            ? { parent_id: parent.id, epic_id: parent.epicId }
            : {}),
        })
        .select("id")
        .single();
      if (dbError) {
        setError(dbError.message);
        return null;
      }
      issueId = data.id;
      // US-7.1: score complexity in the background for a dispatchable item —
      // fire-and-forget so creation never waits on it.
      if (["story", "bug", "chore"].includes(type)) {
        apiFetch(`/api/v1/issues/${issueId}/complexity-score`, {
          method: "POST",
        }).catch(() => {});
      }
    }

    await supabase.from("issue_events").insert({
      org_id: orgId,
      issue_id: issueId!,
      type: isEdit ? "updated" : "created",
      payload: { title: values.title },
    });

    if (isEdit && priorType && priorType !== type) {
      await supabase.from("issue_events").insert({
        org_id: orgId,
        issue_id: issueId!,
        type: "type-changed",
        payload: { from: priorType, to: type },
      });
    }

    await writeEpicEvents(supabase, issueId!, priorEpic, nextEpic ?? null);

    if (opts?.updateChildrenEpic && isEdit && issue?.type === "feature") {
      const { data: children } = await supabase
        .from("issues")
        .select("id")
        .eq("parent_id", issue.id)
        .is("abandoned_at", null);
      for (const child of children ?? []) {
        await supabase
          .from("issues")
          .update({ epic_id: nextEpic })
          .eq("id", child.id);
        await writeEpicEvents(
          supabase,
          child.id,
          opts.priorEpic ?? priorEpic,
          nextEpic ?? null
        );
      }
    }

    return issueId!;
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (uploadFailures) return; // item already created; only Close remains
    setError(null);

    if (!title.trim()) {
      setError("Title is required.");
      return;
    }

    let bodyValue: string | null;
    if (type === "bug") {
      if (!repro.trim() || !expected.trim()) {
        setError("Repro and Expected are both required for a bug.");
        return;
      }
      bodyValue = composeBugBody(repro, expected);
    } else {
      bodyValue = description.trim() || null;
    }

    let cleanCriteria: string[] = [];
    if (showAcceptanceCriteria) {
      cleanCriteria = criteria.map((c) => c.trim()).filter(Boolean);
      if (requiresAcceptanceCriteria(type) && !cleanCriteria.length) {
        setError("At least one acceptance criterion is required.");
        return;
      }
    }

    const values: Record<string, unknown> = {
      title: title.trim(),
      type,
      body: bodyValue,
      acceptance_criteria: cleanCriteria,
    };
    if (!parent) {
      values.epic_id = epicId === "none" ? null : epicId;
    }

    const nextEpic = parent
      ? parent.epicId
      : epicId === "none"
        ? null
        : epicId;
    const priorEpic = issue?.epic_id ?? null;
    const epicChanged = isEdit && !parent && priorEpic !== nextEpic;

    setSaving(true);
    const supabase = createClient();
    try {
      if (
        epicChanged &&
        isEdit &&
        issue?.type === "feature"
      ) {
        const { count } = await supabase
          .from("issues")
          .select("id", { count: "exact", head: true })
          .eq("parent_id", issue.id)
          .is("abandoned_at", null);
        if ((count ?? 0) > 0) {
          setPendingChildEpicUpdate({
            values,
            issueId: issue.id,
            nextEpic,
            priorEpic,
            childCount: count ?? 0,
          });
          return;
        }
      }

      const savedId = await persistIssue(values, { nextEpic, priorEpic });
      if (!savedId) return;

      // us-2.29: the work item exists from here on — a failed upload never
      // loses it. Failed files are named inline and retried from the
      // detail page's Documents panel.
      if (!isEdit && files.length) {
        const failed: string[] = [];
        for (const file of files) {
          try {
            await uploadDocument(
              orgId,
              projectId,
              { attachedTo: "work-item", issueId: savedId },
              file
            );
          } catch {
            failed.push(file.name);
          }
        }
        if (failed.length) {
          setFiles((prev) => prev.filter((f) => failed.includes(f.name)));
          setUploadFailures(failed);
          router.refresh();
          return;
        }
      }

      // US-25.6: "Add another" keeps the dialog up and clears it for the next
      // item. The confirmation matters more here than on a normal create: the
      // dialog does not close, so without it the click looks like nothing
      // happened.
      const keepOpen = addAnotherRef.current && !isEdit;
      addAnotherRef.current = false;
      if (!keepOpen) setOpen(false);
      if (!isEdit) {
        setTitle("");
        setDescription("");
        setRepro("");
        setExpected("");
        setCriteria([""]);
        setFiles([]);
        if (keepOpen) {
          toastSuccess("Work item created", title);
          titleRef.current?.focus();
        }
      }
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  function closeAfterPartialFailure() {
    setUploadFailures(null);
    setFiles([]);
    setTitle("");
    setDescription("");
    setRepro("");
    setExpected("");
    setCriteria([""]);
    setError(null);
    setOpen(false);
    router.refresh();
  }

  async function finishWithChildren(updateChildren: boolean) {
    if (!pendingChildEpicUpdate) return;
    setSaving(true);
    setError(null);
    try {
      const ok = await persistIssue(pendingChildEpicUpdate.values, {
        updateChildrenEpic: updateChildren,
        nextEpic: pendingChildEpicUpdate.nextEpic,
        priorEpic: pendingChildEpicUpdate.priorEpic,
      });
      if (!ok) return;
      setPendingChildEpicUpdate(null);
      setOpen(false);
      router.refresh();
    } finally {
      setSaving(false);
    }
  }

  const defaultTrigger = isEdit ? (
    <Button variant="outline" size="sm" />
  ) : (
    <Button variant="create" />
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && uploadFailures) {
          closeAfterPartialFailure();
          return;
        }
        setOpen(next);
      }}
    >
      <DialogTrigger render={trigger ?? defaultTrigger}>
        {!trigger &&
          (isEdit ? (
            <>
              <Pencil className="size-4" />
              Edit
            </>
          ) : (
            <>
              <Plus className="size-4" />
              {parent ? "Add story" : "New work item"}
            </>
          ))}
      </DialogTrigger>
      {/* US-14.10: Advanced stacked six full-width fields in a 512px column,
          which overflowed 85vh and pushed the primary action off-screen behind
          a scrollbar. Widening it and pairing the short fields below fits the
          same content without scrolling; Basic stays deliberately narrow. */}
      <DialogContent
        className={cn(
          "flex max-h-[85vh] flex-col overflow-hidden",
          advanced ? "gap-3 sm:max-w-3xl" : "gap-4 sm:max-w-lg"
        )}
      >
        <DialogHeader>
          <DialogTitle>
            {isEdit ? "Edit work item" : parent ? "Add story" : "New work item"}
          </DialogTitle>
          {/* US-14.10: the type row below carries this now — each button's
              tooltip says what the factory does with that kind — so the
              header does not repeat it. DialogDescription still renders for
              a11y, visually hidden, because the dialog is aria-describedby
              it and an empty description reads worse than none. */}
          <DialogDescription className="sr-only">
            {TYPE_DESCRIPTIONS[type]}
          </DialogDescription>
        </DialogHeader>
        {/* US-14.10: only the fields scroll. The footer used to sit inside
            the scroll area, where its -mx-4 bleed made the content 32px wider
            than the container — that summoned a horizontal scrollbar, whose
            own height then pushed the content into a vertical one. Two bars
            from one stray margin. */}
        <form
          onSubmit={handleSave}
          className="flex min-h-0 flex-1 flex-col gap-4 overflow-hidden"
        >
          <div
            className={cn(
              "grid min-h-0 flex-1 overflow-y-auto overflow-x-hidden pr-1 [&>*]:min-w-0",
              // US-14.10: Advanced carries twice the fields, so it sits a
              // little tighter. Story is the tallest case (it adds
              // acceptance criteria) and was the one still overflowing.
              advanced ? "gap-3" : "gap-4"
            )}
          >
          <div className="grid gap-2">
            {/* US-14.10: no visible "Type" label — four labelled, iconed
                buttons are not ambiguous, and the row reads as the choice it
                is. The group keeps its accessible name. */}
            {typeLocked && <Label htmlFor="issue-type">Type</Label>}
            {/* US-14.5: a radio group in Basic. Type is the one decision
                worth making deliberately — it selects the pipeline the item
                travels — and a dropdown hides three of the four choices
                behind a click. Advanced keeps the compact select. */}
            {!typeLocked ? (
              <div
                role="radiogroup"
                aria-label="Type"
                id="issue-type"
                className="flex flex-wrap gap-2"
              >
                {ISSUE_TYPES.map((t) => {
                  // US-14.10: the same icon the badges use, so a type looks
                  // like itself on every screen.
                  const Icon = TYPE_ICONS[t];
                  return (
                    <button
                      key={t}
                      type="button"
                      role="radio"
                      aria-checked={type === t}
                      onClick={() => setType(t)}
                      title={TYPE_DESCRIPTIONS[t]}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors",
                        type === t
                          ? "border-primary bg-primary text-primary-foreground"
                          : "hover:bg-muted"
                      )}
                    >
                      {Icon && <Icon className="size-3.5" />}
                      {TYPE_LABELS[t]}
                    </button>
                  );
                })}
              </div>
            ) : (
            <Select
              items={ISSUE_TYPES.map((t) => ({ value: t, label: TYPE_LABELS[t] }))}
              value={type}
              onValueChange={(v) => {
                if (typeof v === "string") setType(v as IssueType);
              }}
              disabled={typeLocked}
            >
              <SelectTrigger id="issue-type" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ISSUE_TYPES.map((t) => (
                  <SelectItem key={t} value={t}>
                    {TYPE_LABELS[t]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            )}
            {isEdit && issue!.status !== "draft" && !parent && (
              <p className="text-xs text-muted-foreground">
                Type can only be changed while the issue is a draft.
              </p>
            )}
          </div>

          {/* US-14.5: a bug's repro and expected are required to save, so
              Basic shows them — the mode hides what is optional, never what
              the chosen type actually needs. The free description is the
              part Basic omits for the other types. */}
          <div className="grid gap-2">
            <Label htmlFor="issue-title">Title</Label>
            <Input
              ref={titleRef}
              id="issue-title"
              placeholder="Add CSV export to the report page"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>

          {pickerMode && projects && (
            <div className="grid gap-2">
              <Label htmlFor="issue-project">Project</Label>
              <Select
                items={projects.map((p) => ({ value: p.id, label: p.name }))}
                value={projectId}
                onValueChange={(v) => {
                  if (typeof v === "string") {
                    setPickedProjectId(v);
                    setEpicId("none");
                  }
                }}
              >
                <SelectTrigger id="issue-project" className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}

          {type === "bug" ? (
            <>
              <div className="grid gap-2">
                <Label htmlFor="issue-repro">Repro</Label>
                <MarkdownEditor
                  id="issue-repro"
                  orgId={orgId}
                  rows={4}
                  placeholder={"1. Go to…\n2. Click…\n3. Observe…"}
                  value={repro}
                  onChange={setRepro}
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="issue-expected">Expected</Label>
                <MarkdownEditor
                  id="issue-expected"
                  orgId={orgId}
                  rows={3}
                  placeholder="What should have happened instead."
                  value={expected}
                  onChange={setExpected}
                />
              </div>
            </>
          ) : advanced || isEdit ? (
            <div className="grid gap-2">
              <Label htmlFor="issue-body">
                {type === "feature"
                  ? "Description"
                  : type === "chore"
                    ? "Description"
                    : "Story"}
              </Label>
              <MarkdownEditor
                id="issue-body"
                orgId={orgId}
                rows={type === "chore" ? 3 : advanced ? 4 : 5}
                placeholder={
                  type === "feature"
                    ? "The raw idea — Draft PRD turns this into a full spec."
                    : type === "chore"
                      ? "A short description of the housekeeping work."
                      : "As a user, I can… so that…\n\nMarkdown supported."
                }
                value={description}
                onChange={setDescription}
              />
            </div>
          ) : null}

          {showAcceptanceCriteria && (
            <div className="grid gap-2">
              <Label>
                Acceptance criteria
                {!requiresAcceptanceCriteria(type) && (
                  <span className="ml-1 font-normal text-muted-foreground">
                    (optional)
                  </span>
                )}
              </Label>
              {criteria.map((c, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    placeholder={`Criterion ${i + 1}`}
                    value={c}
                    onChange={(e) => setCriterion(i, e.target.value)}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Remove criterion"
                    onClick={() => removeCriterion(i)}
                    disabled={criteria.length === 1}
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-fit"
                onClick={() => setCriteria((prev) => [...prev, ""])}
              >
                <Plus className="size-4" />
                Add criterion
              </Button>
            </div>
          )}

          {/* US-20.3: Epic is not Advanced — it is the numbering root and
              the close-gate, so it should not take a click to find.
              US-71.2: target date left the UI entirely. */}
          <div className="grid gap-4">
          {parent ? (
            <p className="text-xs text-muted-foreground">
              Parented to the current feature
              {parentEpic ? ` · inherits epic "${parentEpic.title}"` : ""}.
            </p>
          ) : (
            <div className="grid gap-2">
              <Label htmlFor="issue-epic">Epic</Label>
              {creatingEpic ? (
                <>
                  <div className="flex items-center gap-2">
                    <Input
                      id="issue-epic"
                      autoFocus
                      placeholder="Overhaul the billing system"
                      value={newEpicTitle}
                      onChange={(e) => setNewEpicTitle(e.target.value)}
                      onKeyDown={(e) => {
                        // The dialog's own form must not submit from here.
                        if (e.key === "Enter") {
                          e.preventDefault();
                          createEpicInline();
                        }
                      }}
                    />
                    <Button
                      type="button"
                      size="sm"
                      disabled={epicBusy}
                      onClick={createEpicInline}
                    >
                      {epicBusy && <Loader2 className="size-4 animate-spin" />}
                      Create
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      disabled={epicBusy}
                      onClick={() => {
                        setCreatingEpic(false);
                        setNewEpicTitle("");
                        setEpicError(null);
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                  {epicError && (
                    <p className="text-xs font-medium text-destructive">
                      {epicError}
                    </p>
                  )}
                </>
              ) : (
                <Select
                  items={[
                    { value: "none", label: "No epic" },
                    ...epicOptions.map((ep) => ({
                      value: ep.id,
                      label: epicLabelFor(ep),
                    })),
                    { value: NEW_EPIC, label: "＋ New epic…" },
                  ]}
                  value={epicId}
                  onValueChange={(v) => {
                    if (typeof v !== "string") return;
                    if (v === NEW_EPIC) {
                      setEpicError(null);
                      setCreatingEpic(true);
                      return;
                    }
                    setEpicId(v);
                  }}
                >
                  <SelectTrigger id="issue-epic" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="none">No epic</SelectItem>
                    {epicOptions.map((ep) => (
                      <SelectItem key={ep.id} value={ep.id}>
                        {epicLabelFor(ep)}
                      </SelectItem>
                    ))}
                    <SelectItem value={NEW_EPIC}>＋ New epic…</SelectItem>
                  </SelectContent>
                </Select>
              )}
            </div>
          )}
          </div>

          {advanced && !isEdit && (
            <div className="grid gap-2">
              <Label>
                Attachments
                <span className="ml-1 font-normal text-muted-foreground">
                  (optional)
                </span>
              </Label>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  addFiles(e.target.files);
                  e.target.value = "";
                }}
              />
              <button
                type="button"
                className="rounded-md border border-dashed px-3 py-3 text-center text-sm text-muted-foreground transition-colors hover:bg-accent"
                onClick={() => fileInputRef.current?.click()}
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  addFiles(e.dataTransfer.files);
                }}
              >
                Drag &amp; drop files here, or click to browse.
                <span className="mt-1 block text-xs">
                  Up to 25 MB per file, any type.
                </span>
              </button>
              {files.length > 0 && (
                <ul className="grid gap-1">
                  {files.map((f, i) => (
                    <li
                      key={`${f.name}-${i}`}
                      className="flex items-center justify-between gap-2 rounded-md border px-2 py-1 text-sm"
                    >
                      <span className="truncate">{f.name}</span>
                      <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                        {formatBytes(f.size)}
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={`Remove ${f.name}`}
                          onClick={() => removeFile(i)}
                          disabled={saving || !!uploadFailures}
                        >
                          <X className="size-4" />
                        </Button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {uploadFailures ? (
            <p className="text-sm font-medium text-destructive">
              The work item was created, but{" "}
              {uploadFailures.length === 1
                ? "this attachment"
                : "these attachments"}{" "}
              failed to upload: {uploadFailures.join(", ")}. Retry from the
              work item&apos;s Documents panel.
            </p>
          ) : (
            error && (
              <p className="text-sm font-medium text-destructive">{error}</p>
            )
          )}
          </div>

          <DialogFooter className="sm:justify-between">
            {/* US-14.5: the mode switch sits opposite the primary action, so
                "Create work item" never moves between modes. Hidden while
                editing an item whose content forces Advanced — offering a
                switch that cannot take effect is worse than no switch. */}
            {!uploadFailures && !editHasAdvancedContent ? (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-pressed={advanced}
                onClick={() => setRememberedAdvanced(!advanced)}
                className="text-muted-foreground"
              >
                {advanced ? "Basic" : "More options"}
              </Button>
            ) : (
              <span />
            )}
            {uploadFailures ? (
              <Button type="button" onClick={closeAfterPartialFailure}>
                Close
              </Button>
            ) : (
              <div className="flex items-center gap-2">
                {/* US-25.6: same submit, same validation — it only differs in
                    not closing afterwards, so capturing several items in a row
                    costs one click each instead of reopening the dialog. */}
                {!isEdit && (
                  <Button
                    type="submit"
                    variant="outline"
                    disabled={saving}
                    onClick={() => {
                      addAnotherRef.current = true;
                    }}
                  >
                    Add another
                  </Button>
                )}
                <Button
                  type="submit"
                  disabled={saving}
                  onClick={() => {
                    addAnotherRef.current = false;
                  }}
                >
                  {saving && <Loader2 className="size-4 animate-spin" />}
                  {isEdit ? "Save changes" : "Create work item"}
                </Button>
              </div>
            )}
          </DialogFooter>
        </form>
      </DialogContent>

      <Dialog
        open={!!pendingChildEpicUpdate}
        onOpenChange={(open) => {
          if (!open) setPendingChildEpicUpdate(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Update child stories&apos; epic?</DialogTitle>
            <DialogDescription>
              This feature has {pendingChildEpicUpdate?.childCount ?? 0} child{" "}
              {(pendingChildEpicUpdate?.childCount ?? 0) === 1
                ? "story"
                : "stories"}
              . Apply the same epic change to them?
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={saving}
              onClick={() => finishWithChildren(false)}
            >
              Feature only
            </Button>
            <Button disabled={saving} onClick={() => finishWithChildren(true)}>
              {saving && <Loader2 className="size-4 animate-spin" />}
              Update children too
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}
