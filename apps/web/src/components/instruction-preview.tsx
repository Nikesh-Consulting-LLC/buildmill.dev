"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader2, Pencil, Eye } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";
import { RUN_KIND_RUN_PHRASES } from "@/lib/run-kinds";

/** US-49.1: what the agent will read, before it reads it.
 *
 * `issues.instruction_set` is seeded once, inside the dispatch RPC, and read
 * live by workers from then on — so the text steering every future run on an
 * item is decided at a moment nobody sees. This is that moment, made visible
 * and editable.
 *
 * The text comes from `preview_issue_instructions`, which calls the same
 * `build_issue_instructions` the seeder calls and resolves the run kind with
 * the same `dispatch_kind_for` the dispatcher uses. Nothing here re-derives
 * either: a preview headed "plan run" over code-run text would be worse than
 * no preview at all.
 *
 * Saving an edit needs no new plumbing. The seeder skips an item that already
 * carries instructions, so writing the field here means the dispatch a second
 * later leaves it exactly as the manager left it. */
export type InstructionPreviewState = {
  kind: string;
  seeded: boolean;
  instruction_set: string | null;
};

/** What the confirm button does, named as an act. Interpolating the raw kind
 * produced "Start the prd". */
export const CONFIRM_LABELS: Record<string, string> = {
  plan: "Plan it",
  code: "Build it",
  prd: "Draft the PRD",
  breakdown: "Break it down",
  elaborate: "Elaborate it",
  wireframe: "Draw it",
  merge: "Merge them",
};

/** us-98.1: this module used to export its own `RUN_KIND_LABELS` holding
 * "Plan run" while `lib/run-kinds` exported one holding "Plan" — two
 * different contents behind one name, which is how a caller ends up
 * importing the wrong vocabulary from the wrong place. The phrases now live
 * beside the labels in `lib/run-kinds`, named for what they are, and
 * nothing outside this file ever imported the shadowed export. */

export function InstructionPreview({
  issueId,
  orgId,
  kind,
  onResolved,
}: {
  issueId: string;
  orgId: string;
  /** null asks the database which phase a dispatch would run. */
  kind: string | null;
  onResolved?: (state: InstructionPreviewState) => void;
}) {
  const [state, setState] = useState<InstructionPreviewState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    const supabase = createClient();
    const { data, error: rpcError } = await supabase.rpc(
      "preview_issue_instructions",
      { p_issue: issueId, p_kind: kind ?? undefined }
    );
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    const next = data as unknown as InstructionPreviewState;
    setState(next);
    setDraft(next.instruction_set ?? "");
    onResolved?.(next);
    // onResolved is a render-time closure in every caller; depending on it
    // would reload the preview on each parent render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, kind]);

  useEffect(() => {
    void load();
  }, [load]);

  async function save() {
    setSaving(true);
    setError(null);
    const supabase = createClient();
    const { error: dbError } = await supabase
      .from("issues")
      .update({ instruction_set: draft })
      .eq("id", issueId);
    if (dbError) {
      setSaving(false);
      setError(dbError.message);
      return;
    }
    // Audited like every other edit of this field (us-5.11), so the change is
    // visible on the item's history and to the next MCP read.
    await supabase.from("issue_events").insert({
      org_id: orgId,
      issue_id: issueId,
      type: "instructions-updated",
      payload: { length: draft.length, at: "dispatch" },
    });
    setSaving(false);
    setEditing(false);
    setSaved(true);
    // Now that the item carries a set, the dispatch will use it verbatim.
    setState((s) => (s ? { ...s, seeded: false, instruction_set: draft } : s));
  }

  if (error) {
    return (
      <p className="text-sm font-medium text-destructive">
        Couldn&apos;t read the instructions: {error}
      </p>
    );
  }

  if (!state) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" />
        Reading the instructions…
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {saved
            ? "Saved — the run will read exactly this."
            : state.seeded
              ? "Not saved yet: this is what the factory will write to the item when the run is queued."
              : "This is the item's instruction set. The run reads it as it stands."}
        </p>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={editing ? "Preview" : "Edit"}
          onClick={() => setEditing((e) => !e)}
        >
          {editing ? <Eye className="size-4" /> : <Pencil className="size-4" />}
        </Button>
      </div>

      {editing ? (
        <MarkdownEditor
          rows={20}
          value={draft}
          onChange={setDraft}
          orgId={orgId}
          placeholder="What the agent on this item is expected to do."
        />
      ) : (
        <div className="max-h-[55vh] min-h-40 overflow-y-auto rounded-md border bg-muted/30 p-3">
          {draft.trim() ? (
            <MarkdownView>{draft}</MarkdownView>
          ) : (
            <p className="text-sm text-muted-foreground">
              No instructions — the agent gets the run context alone.
            </p>
          )}
        </div>
      )}

      {editing && draft !== (state.instruction_set ?? "") && (
        <div className="flex justify-end">
          <Button size="sm" disabled={saving} onClick={save}>
            {saving && <Loader2 className="size-3.5 animate-spin" />}
            Save instructions
          </Button>
        </div>
      )}
    </div>
  );
}

/** The confirmation every deliberate dispatch goes through. It costs a click,
 * which is the same trade us-27.11 made for the batch and for the same
 * reason: the wrong run costs a run, a review and a repair. */
export function DispatchPreviewDialog({
  issueId,
  orgId,
  kind,
  open,
  onOpenChange,
  onConfirm,
  busy,
  title,
  summary,
  showPreview = true,
  wireframe,
  confirmLabel,
  body,
}: {
  issueId: string;
  orgId: string;
  kind: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
  busy?: boolean;
  /** US-49.7: a batch names what it covers instead of the bare run kind. */
  title?: string;
  /** A line above the preview — "39 stories, each with its own set". */
  summary?: React.ReactNode;
  /** False when there is no single instruction set to show (a plan batch is
   * one run per story, and fifteen scrolling documents is not a
   * confirmation). */
  showPreview?: boolean;
  /** US-49.7: what to show instead of the per-item preview — a batch shows
   * one common instruction the manager edits once for all of them. */
  body?: React.ReactNode;
  /** US-49.7: offered on a plan dispatch only. Ticking it draws INSTEAD of
   * planning — Phase 48 settled that a plan run uses a wireframe and never
   * creates one, and a plan queued beside a drawing would race it. */
  wireframe?: {
    checked: boolean;
    onChange: (next: boolean) => void;
    note?: string;
  };
  confirmLabel?: string;
}) {
  const [resolved, setResolved] = useState<string | null>(null);

  // A closed dialog forgets what it resolved, so re-opening it after a status
  // change never shows the previous run's phase.
  useEffect(() => {
    if (!open) setResolved(null);
  }, [open]);

  const effectiveKind = resolved ?? kind;
  const drawing = !!wireframe?.checked;
  const label =
    title ??
    (effectiveKind
      ? (RUN_KIND_RUN_PHRASES[
            effectiveKind as keyof typeof RUN_KIND_RUN_PHRASES
          ] ?? `${effectiveKind} run`)
      : "Dispatch");

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* DialogContent's base carries `sm:max-w-sm`, which beats an unprefixed
          max-w at every width above 640px — the override has to be prefixed
          too or the dialog stays 24rem wide. This is a document to read
          before spending a run on it, so it gets the room. */}
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader>
          <DialogTitle>{drawing ? "Draw the screens" : label}</DialogTitle>
          <DialogDescription>
            {drawing
              ? "This dispatch will draw the screens instead of planning. Plan afterwards — those plans then read the drawings."
              : "The instructions this run will read. Edit them here and the agent gets exactly what you leave."}
          </DialogDescription>
        </DialogHeader>

        {summary && (
          <p className="text-sm text-muted-foreground">{summary}</p>
        )}

        {open && showPreview && !drawing && (
          <InstructionPreview
            issueId={issueId}
            orgId={orgId}
            kind={kind}
            onResolved={(s) => setResolved(s.kind)}
          />
        )}

        {open && !showPreview && !drawing && body}

        {/* US-49.7: Phase 48 kept drawing an explicit act so that pressing
            Plan is never silently two runs and two bills. A ticked box is not
            silent — but it cannot mean "do both", because a plan run queued
            beside a wireframe run would plan against a screen that does not
            exist yet. */}
        {wireframe && (effectiveKind === "plan" || drawing) && (
          <label className="flex cursor-pointer items-start gap-2 rounded-md border bg-muted/30 px-3 py-2">
            <Checkbox
              checked={wireframe.checked}
              onCheckedChange={(v) => wireframe.onChange(Boolean(v))}
              aria-label="Draw the screens instead of planning"
              className="mt-0.5"
            />
            <span className="text-sm">
              <span className="font-medium">
                Draw the screens instead of planning
              </span>
              <span className="block text-muted-foreground">
                {drawing
                  ? (wireframe.note ??
                    "Each screen is its own metered run. Plan once the drawings land.")
                  : "Off: this dispatch plans. A plan uses a wireframe when one already exists and never creates one."}
              </span>
            </span>
          </label>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Not now
          </Button>
          <Button disabled={busy} onClick={onConfirm}>
            {busy && <Loader2 className="size-3.5 animate-spin" />}
            {/* Naming the act, not the kind — "Start the prd" was what
                interpolating a raw run kind produced. */}
            {drawing
              ? "Draw them"
              : (confirmLabel ??
                CONFIRM_LABELS[effectiveKind ?? ""] ??
                "Start the run")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
