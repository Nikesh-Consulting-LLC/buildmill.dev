"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Check, FileText, Loader2, Pencil, Sparkles } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import {
  EMPTY_PRD_SECTIONS,
  PRD_SECTIONS,
  parsePrdSections,
  serializePrdSections,
  type PrdSections,
} from "@/lib/prd";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";
import { DocumentsPanel } from "@/components/documents-panel";
import type { DocumentRow } from "@/lib/documents";
import { useActivitySession } from "@/lib/use-activity-session";

export type PrdArtifact = {
  id: string;
  content: string;
  version: number;
  status: string;
  /** US-49.2: the item's instruction set as it stood when this version was
   * handed back. Null on manager-authored versions and on everything written
   * before migration 190 — the value was never recorded and cannot be. */
  instruction_set?: string | null;
};

/** US-49.2: the brief a PRD version was written from, kept with the version.
 *
 * The item's own instruction set is living — us-49.1 makes editing it easy, at
 * dispatch and mid-run — so reading it later answers what the NEXT agent will
 * read, never what this document was written from. When the two have parted
 * company the panel says so: without that line a reader has no way to know it
 * is looking at something different from the item's current brief.
 *
 * Renders nothing at all when the version carries no snapshot. An empty box
 * captioned "instructions" reads as "the agent was given none", which is
 * false — it was simply never recorded. */
function InstructionSnapshot({
  artifact,
  current,
}: {
  artifact: PrdArtifact;
  current: string | null;
}) {
  const snapshot = artifact.instruction_set;
  if (!snapshot || !snapshot.trim()) return null;
  const changed = (current ?? "").trim() !== snapshot.trim();
  return (
    <details className="rounded-md border border-dashed p-3">
      <summary className="cursor-pointer select-none text-sm text-muted-foreground">
        Instructions this PRD was written from · v{artifact.version}
      </summary>
      {changed && (
        <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-400">
          The item&apos;s instructions have changed since this was written.
        </p>
      )}
      <MarkdownView className="mt-3">{snapshot}</MarkdownView>
    </details>
  );
}

/** us-2.28: breakdown standing-instruction modes offered at approval. */
export const BREAKDOWN_MODE_ITEMS = [
  { value: "automatic", label: "Automatic — let the agent judge" },
  { value: "single", label: "Single story — one story covers the whole PRD" },
  { value: "multiple", label: "Multiple stories — a detailed split" },
];

/** PRD lifecycle for a feature issue (us-2.3): Draft → per-section edit.
 * `artifacts` is every PRD artifact for the issue, newest first.
 * `documents` are the PRD-linked design documents — uploaded by hand.
 * us-2.22's generator, which used to produce them from the description, has
 * been dead since us-3.21 and was removed in us-48.6; a story's screens are
 * drawn by the `wireframe` run kind (Phase 48) instead.
 *
 * us-12.2: the approve/send-back decision lives on `/review/[issueId]`
 * with the plan and code gates. The document stays here as context for the
 * whole feature, and stays editable; only the gate moved. */
export function PrdPanel({
  issueId,
  orgId,
  projectId,
  status,
  artifacts,
  documents,
  actorNames,
  hasActivePrdRun,
  currentInstructionSet,
}: {
  issueId: string;
  orgId: string;
  projectId: string;
  status: string;
  artifacts: PrdArtifact[];
  documents: DocumentRow[];
  actorNames?: Record<string, string>;
  hasActivePrdRun: boolean;
  /** US-49.2: what the item says NOW, to tell the reader when a version's
   * snapshot has been overtaken. */
  currentInstructionSet?: string | null;
}) {
  const router = useRouter();
  // us-12.2: approve/send-back moved to the review surface, so this panel
  // only drafts and edits.
  const [busy, setBusy] = useState<"draft" | "save" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);
  const [sections, setSections] = useState<PrdSections>(EMPTY_PRD_SECTIONS);
  // US-62.6: how long this edit session was actually active, pause-aware.
  useActivitySession(editing, "artifact-edit", issueId);

  const draft = artifacts.find((a) => a.status === "draft");
  const approved = artifacts.find((a) => a.status === "approved");
  const canDraft =
    !draft &&
    !hasActivePrdRun &&
    (status === "draft" || status === "prd-review" || status === "ready");

  // US-3.21: PRD drafting is now dispatched into the worker pool — poll via
  // Realtime for the run completing rather than awaiting it synchronously.
  useEffect(() => {
    if (!hasActivePrdRun) return;
    const supabase = createClient();
    let channel: ReturnType<typeof supabase.channel> | null = null;
    let cancelled = false;

    async function subscribe() {
      const {
        data: { session },
      } = await supabase.auth.getSession();
      if (!session || cancelled) return;
      await supabase.realtime.setAuth(session.access_token);
      channel = supabase
        .channel(`prd-panel-${issueId}`, { config: { private: false } })
        .on(
          "postgres_changes",
          {
            event: "*",
            schema: "public",
            table: "runs",
            filter: `issue_id=eq.${issueId}`,
          },
          () => router.refreshSilently()
        )
        .subscribe();
    }

    subscribe();
    return () => {
      cancelled = true;
      if (channel) supabase.removeChannel(channel);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [issueId, hasActivePrdRun]);

  async function draftPrd() {
    setError(null);
    setBusy("draft");
    try {
      await apiFetch(`/api/v1/issues/${issueId}/prd/draft`, { method: "POST" });
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function startEditing() {
    setSections(parsePrdSections(draft?.content ?? ""));
    setEditing(true);
  }

  async function saveEdit() {
    if (!draft) return;
    setError(null);
    setBusy("save");
    try {
      await apiFetch(`/api/v1/artifacts/${draft.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: serializePrdSections(sections) }),
      });
      setEditing(false);
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }


  return (
    <Card id="prd">
      <CardHeader className="flex flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="flex items-center gap-2 text-base">
            <FileText className="size-4 text-muted-foreground" />
            PRD
          </CardTitle>
          <CardDescription>
            The product requirements this feature ships against.
          </CardDescription>
        </div>
        {canDraft && (
          <Button onClick={draftPrd} disabled={busy !== null}>
            {busy === "draft" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Sparkles className="size-4" />
            )}
            Draft PRD
          </Button>
        )}
        {hasActivePrdRun && !draft && (
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Loader2 className="size-3.5 animate-spin" />
            Drafting — waiting for a worker to pick this up…
          </span>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {/* US-2.16: anchor targets so audit/decisions links land on the
            exact PRD artifact (#artifact-<id>). */}
        {artifacts.map((a) => (
          <span key={a.id} id={`artifact-${a.id}`} className="scroll-mt-20" />
        ))}
        {!draft && !approved && (
          <p className="text-sm text-muted-foreground">
            No PRD yet. Draft one to define the problem, goals, and
            acceptance criteria before breaking this into stories.
          </p>
        )}

        {draft && (
          <div className="grid gap-3 rounded-md border p-3">
            <div className="flex items-center justify-between gap-2">
              <Badge variant="secondary">Draft · v{draft.version}</Badge>
              {status === "prd-review" && !editing && (
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" onClick={startEditing}>
                    <Pencil className="size-3.5" />
                    Edit
                  </Button>
                  {/* us-12.2: the PRD gate moved to the shared review
                      surface, alongside plan and code review. The
                      document stays readable (and editable) here; the
                      decision happens in one place for all three gates. */}
                  <Button size="sm" render={<Link href={`/review/${issueId}`} />}>
                    <Check className="size-3.5" />
                    Review PRD
                  </Button>
                </div>
              )}
            </div>
            {editing ? (
              <div className="grid gap-4">
                {PRD_SECTIONS.map(({ key, heading }) => (
                  <div key={key} className="grid gap-2">
                    <Label htmlFor={`prd-${key}`}>{heading}</Label>
                    <MarkdownEditor
                      id={`prd-${key}`}
                      rows={key === "acceptance_criteria" ? 5 : 4}
                      orgId={orgId}
                      value={sections[key]}
                      onChange={(next) =>
                        setSections((prev) => ({ ...prev, [key]: next }))
                      }
                    />
                  </div>
                ))}
                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(false)}
                  >
                    Cancel
                  </Button>
                  <Button size="sm" onClick={saveEdit} disabled={busy === "save"}>
                    {busy === "save" && (
                      <Loader2 className="size-3.5 animate-spin" />
                    )}
                    Save
                  </Button>
                </div>
              </div>
            ) : (
              <MarkdownView>{draft.content}</MarkdownView>
            )}
          </div>
        )}

        {approved && (
          <details className="rounded-md border p-3" open={!draft}>
            <summary className="cursor-pointer select-none text-sm font-medium">
              Approved PRD · v{approved.version}
            </summary>
            <MarkdownView className="mt-3">{approved.content}</MarkdownView>
          </details>
        )}

        {/* US-49.2: adjacent to the document, because it is what the document
            was written to. The draft's own brief sits with the draft when it
            has one — each version carries its own. */}
        {draft && (
          <InstructionSnapshot
            artifact={draft}
            current={currentInstructionSet ?? null}
          />
        )}
        {approved && (
          <InstructionSnapshot
            artifact={approved}
            current={currentInstructionSet ?? null}
          />
        )}

        {(draft || approved || documents.length > 0) && (
          <div className="rounded-md border p-3">
            <DocumentsPanel
              orgId={orgId}
              projectId={projectId}
              target={{ attachedTo: "prd", issueId }}
              initialDocs={documents}
              actorNames={actorNames}
              preview
              variant="plain"
              title="Design documents"
              emptyTitle="No design documents"
              emptyDescription="Upload the design documents this feature ships against. To see a story's screens, draw it on the story itself."
            />
          </div>
        )}

        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
