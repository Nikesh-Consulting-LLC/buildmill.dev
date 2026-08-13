"use client";

// US-32.5: the platform's preset templates.
//
// A template cannot name a model: `llm_providers` is org-scoped with a curated
// model list per org, and since us-27.8 the model is what resolves a call's
// provider — so `claude-sonnet-5` may name nothing in the next org. A template
// therefore carries everything else, plus advice about what model suits it, and
// each org holds real rows seeded from it with concrete models of its own.
//
// Editing here changes no org's presets. Each org is offered the change as an
// explicit re-seed on its own Run presets page, stating what would change.
//
// US-56.2: one compact row per template, the form behind an Edit dialog —
// us-56.1's shape, minus the Overview tab, because templates carry no run
// outcomes (runs bind to org preset versions) and an empty tab would be
// furniture.

import { useCallback, useEffect, useState } from "react";

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
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { toastError, toastSuccess } from "@/components/ui/toast";

type Template = {
  key: string;
  name: string;
  description: string;
  model_hint: string;
  settings: Record<string, unknown>;
  version: number;
};

// US-32.10: mirrors the API's `EFFORTS`. A template is authored for every org
// at once, so there is no machine declaration to read here either.
const EFFORTS = ["", "low", "medium", "high", "xhigh", "max"];
// US-47.1: no permission mode. Only `bypassPermissions` produces a headless run
// that can call an MCP tool, and the runner sets it itself.

export default function AdminPresetTemplatesPage() {
  const [templates, setTemplates] = useState<Template[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await apiCall("/api/v1/admin/preset-templates");
      setTemplates(res?.templates ?? []);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="flex w-full flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Preset templates
        </h1>
        <p className="max-w-3xl text-sm text-muted-foreground">
          What a new org is seeded with. A template holds effort, permission
          mode, ceilings and standing instructions — but never a model id, since
          a model that exists in one org may name nothing in another and the
          model is what decides which provider answers. Editing a template
          changes no org&apos;s presets: each org is offered the update on its own
          Run presets page, with the effect stated first.
        </p>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}
      {templates === null ? (
        <p className="text-sm text-muted-foreground">Loading templates…</p>
      ) : (
        <div className="overflow-x-auto rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Template</TableHead>
                <TableHead>Effort</TableHead>
                <TableHead className="text-right">Turns</TableHead>
                <TableHead>Instructions</TableHead>
                <TableHead>Model advice</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {templates.map((t) => (
                <TemplateRow key={t.key} template={t} onSaved={load} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}

/** US-56.2: one compact row — the form only exists once Edit is clicked. */
function TemplateRow({
  template,
  onSaved,
}: {
  template: Template;
  onSaved: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const s = template.settings ?? {};
  const instructions = String(s.standing_instructions ?? "");

  return (
    <TableRow>
      <TableCell>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{template.name}</span>
          <Badge variant="outline" className="font-mono text-[11px]">
            {template.key}
          </Badge>
          <Badge variant="secondary" className="text-[11px]">
            v{template.version}
          </Badge>
        </div>
        {template.description && (
          <p className="mt-0.5 max-w-md truncate text-xs text-muted-foreground">
            {template.description}
          </p>
        )}
      </TableCell>
      <TableCell className="text-xs">
        {String(s.effort ?? "") || (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="text-right font-mono text-xs">
        {s.max_turns == null ? (
          <span className="font-sans text-muted-foreground">—</span>
        ) : (
          String(s.max_turns)
        )}
      </TableCell>
      <TableCell className="text-xs">
        {instructions ? (
          `${instructions.length} chars`
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell>
        <p className="max-w-xs truncate text-xs text-muted-foreground">
          {template.model_hint || "—"}
        </p>
      </TableCell>
      <TableCell className="text-right">
        <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
          Edit
        </Button>
        <Dialog open={open} onOpenChange={setOpen}>
          <DialogContent className="max-h-[85vh] gap-0 overflow-y-auto sm:max-w-xl">
            <DialogHeader>
              <DialogTitle className="flex flex-wrap items-center gap-2">
                {template.name}
                <Badge variant="outline" className="font-mono text-[11px]">
                  {template.key}
                </Badge>
                <Badge variant="secondary" className="text-[11px]">
                  v{template.version}
                </Badge>
              </DialogTitle>
              <DialogDescription>
                Editing changes no org&apos;s presets — each org is offered the
                update on its own Run presets page.
              </DialogDescription>
            </DialogHeader>
            {/* Keyed by version so a concurrent save never shows stale values
                on the next open. */}
            {open && (
              <TemplateForm
                key={`${template.key}-v${template.version}`}
                template={template}
                onSaved={async () => {
                  await onSaved();
                  setOpen(false);
                }}
                onCancel={() => setOpen(false)}
              />
            )}
          </DialogContent>
        </Dialog>
      </TableCell>
    </TableRow>
  );
}

/** The five-field form — unchanged semantics, same PATCH, now mounted only
 * inside the edit dialog (US-56.2). */
function TemplateForm({
  template,
  onSaved,
  onCancel,
}: {
  template: Template;
  onSaved: () => Promise<void>;
  onCancel: () => void;
}) {
  const s = template.settings ?? {};
  const [description, setDescription] = useState(template.description);
  const [hint, setHint] = useState(template.model_hint);
  const [effort, setEffort] = useState(String(s.effort ?? ""));
  const [maxTurns, setMaxTurns] = useState(
    s.max_turns == null ? "" : String(s.max_turns),
  );
  const [instructions, setInstructions] = useState(
    String(s.standing_instructions ?? ""),
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    setSaving(true);
    try {
      const res = await apiCall(`/api/v1/admin/preset-templates/${template.key}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description,
          model_hint: hint,
          settings: {
            effort: effort || null,
            max_turns: maxTurns ? Number(maxTurns) : null,
            standing_instructions: instructions || null,
          },
        }),
      });
      toastSuccess(
        "Saved",
        `${template.name} is now template version ${res?.version ?? template.version}. No org has changed — each will be offered the update.`,
      );
      await onSaved();
    } catch (e) {
      toastError("Could not save", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="grid gap-4 pt-4 text-sm">
      <label className="grid gap-1">
        <span className="text-muted-foreground">What it is for</span>
        <textarea
          rows={2}
          value={description}
          maxLength={400}
          onChange={(e) => setDescription(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-xs"
        />
      </label>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Model advice</span>
        <input
          value={hint}
          maxLength={400}
          autoComplete="off"
          onChange={(e) => setHint(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 text-xs"
        />
        <span className="text-xs text-muted-foreground">
          Shown beside the model picker in every org. Advice, not a value.
        </span>
      </label>
      <div className="grid gap-4 md:grid-cols-2">
        <label className="grid gap-1">
          <span className="text-muted-foreground">Reasoning effort</span>
          <select
            value={effort}
            onChange={(e) => setEffort(e.target.value)}
            className="rounded-md border bg-background px-2 py-1"
          >
            {EFFORTS.map((e) => (
              <option key={e || "unset"} value={e}>
                {e || "Inherit"}
              </option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="text-muted-foreground">Turn ceiling</span>
          <input
            type="number"
            min={1}
            max={500}
            value={maxTurns}
            onChange={(e) => setMaxTurns(e.target.value)}
            placeholder="none"
            className="rounded-md border bg-background px-2 py-1"
          />
        </label>
      </div>
      <label className="grid gap-1">
        <span className="text-muted-foreground">Standing instructions</span>
        <textarea
          rows={3}
          maxLength={4000}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          className="rounded-md border bg-background px-2 py-1 font-mono text-xs"
        />
        <span className="text-xs text-muted-foreground">
          Appended to the system prompt, never replacing it — the CLI keeps its
          own tool guidance and safety instructions. Up to 4000 characters;{" "}
          {instructions.length}{" "}
          used.
        </span>
      </label>
      <DialogFooter>
        <Button variant="outline" size="sm" disabled={saving} onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save template"}
        </Button>
      </DialogFooter>
    </div>
  );
}
