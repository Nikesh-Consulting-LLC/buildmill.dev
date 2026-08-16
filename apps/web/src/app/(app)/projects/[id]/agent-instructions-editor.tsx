"use client";

import { useState } from "react";
import { Loader2, Save, AlertCircle } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarkdownEditor } from "@/components/markdown-editor";

/** US-100.1 AC3 / US-100.3 AC1: the project's Agent Instructions — one
 * document, not twenty-two section cards.
 *
 * This also closes a hazard migration 263 opened. That migration repointed
 * `assemble_project_guidelines` at `projects.agent_instructions`, so the old
 * section editor kept saving to `project_guidelines` — a table nothing reads
 * any more. An edit that appears to save and changes nothing an agent sees is
 * worse than an editor that refuses, so the section editor is replaced here
 * rather than left standing until the rest of us-100.3 lands.
 *
 * Writes go straight to Supabase under RLS ("build less API"); the content is
 * what publishes as AGENTS.md's body (us-100.2).
 */
export function AgentInstructionsEditor({
  projectId,
  initial,
  canEdit,
  onSaved,
}: {
  projectId: string;
  initial: string;
  canEdit: boolean;
  /** Called after a successful save, so the publish badge can re-check. */
  onSaved?: () => void;
}) {
  const [value, setValue] = useState(initial);
  const [saved, setSaved] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dirty = value !== saved;

  async function save() {
    setBusy(true);
    setError(null);
    const supabase = createClient();
    const { error: e } = await supabase
      .from("projects")
      .update({ agent_instructions: value })
      .eq("id", projectId);
    setBusy(false);
    if (e) {
      setError(e.message);
      return;
    }
    setSaved(value);
    onSaved?.();
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div className="flex flex-col gap-1">
            <CardTitle className="text-base">Agent Instructions</CardTitle>
            <p className="text-sm text-muted-foreground">
              How work is done in this project, in one document. Every agent
              reads it, and it becomes the body of <code>AGENTS.md</code> in
              the repository the next time you publish.
            </p>
          </div>
          {canEdit ? (
            <Button size="sm" disabled={!dirty || busy} onClick={save}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Save className="size-4" />
              )}
              {dirty ? "Save" : "Saved"}
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error ? (
          <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </div>
        ) : null}
        <MarkdownEditor
          value={value}
          onChange={setValue}
          disabled={!canEdit || busy}
          rows={24}
        />
        {dirty ? (
          <p className="text-xs text-muted-foreground">
            Unsaved changes. Saving stores them; publishing is a separate step.
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
