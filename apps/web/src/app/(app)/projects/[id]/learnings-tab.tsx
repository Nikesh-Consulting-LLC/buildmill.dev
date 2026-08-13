"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import {
  Check,
  Eye,
  History,
  Loader2,
  Pencil,
  Sparkles,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { MarkdownEditor } from "@/components/markdown-editor";
import { MarkdownView } from "@/components/markdown-view";

export type ProjectLearningsRow = {
  content: string;
  last_updated_by: string;
  updated_at: string;
} | null;

function formatUpdatedAt(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export type LearningSubmission = {
  id: string;
  text: string;
  createdAt: string;
  status: string;
  worker: string;
};

/** us-5.31: one pending submission awaiting the manager's decision —
 * approve runs the curated merge, reject takes an optional note. */
function PendingSubmissionCard({
  submission,
  projectId,
}: {
  submission: LearningSubmission;
  projectId: string;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"approve" | "reject" | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function decide(decision: "approve" | "reject") {
    setError(null);
    setBusy(decision);
    try {
      await apiFetch(
        `/api/v1/llm/learnings/${projectId}/submissions/${submission.id}/decide`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision, note }),
        }
      );
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision failed.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <li className="grid gap-2 rounded-md border bg-muted/20 p-3">
      <p className="text-sm">{submission.text}</p>
      <p className="text-xs text-muted-foreground">
        {submission.worker} · {formatUpdatedAt(submission.createdAt)}
      </p>
      {rejecting && (
        <Input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="Why not? (optional)"
          className="h-8"
        />
      )}
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          disabled={busy !== null}
          onClick={() => decide("approve")}
        >
          {busy === "approve" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Check className="size-4" />
          )}
          Approve &amp; merge
        </Button>
        {rejecting ? (
          <Button
            size="sm"
            variant="destructive"
            disabled={busy !== null}
            onClick={() => decide("reject")}
          >
            {busy === "reject" ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <X className="size-4" />
            )}
            Confirm reject
          </Button>
        ) : (
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null}
            onClick={() => setRejecting(true)}
          >
            <X className="size-4" />
            Reject
          </Button>
        )}
      </div>
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </li>
  );
}

export function LearningsTab({
  orgId,
  projectId,
  learnings,
  submissions = [],
}: {
  orgId: string;
  projectId: string;
  learnings: ProjectLearningsRow;
  /** us-5.6: recent worker contributions (the pre-merge submissions). */
  submissions?: LearningSubmission[];
}) {
  const router = useRouter();
  const initialContent = learnings?.content ?? "";
  const [content, setContent] = useState(initialContent);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dirty = content !== initialContent;
  // us-5.31: the Learnings page is the gate — pending waits here,
  // deliberately not in Things to Do.
  const pending = submissions.filter((s) => s.status === "pending");
  const decided = submissions.filter((s) => s.status !== "pending");

  async function handleSave() {
    setError(null);
    setSaving(true);
    const supabase = createClient();
    const { error: dbError } = await supabase.from("project_learnings").upsert(
      { org_id: orgId, project_id: projectId, content, last_updated_by: "user" },
      { onConflict: "project_id" }
    );
    setSaving(false);
    if (dbError) {
      setError(dbError.message);
      return;
    }
    router.refresh();
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {learnings ? (
            <>
              Last updated {formatUpdatedAt(learnings.updated_at)}
              {" · "}
              <Badge variant="secondary" className="gap-1 font-normal">
                {learnings.last_updated_by === "llm" && (
                  <Sparkles className="size-3" />
                )}
                {learnings.last_updated_by === "llm"
                  ? "updated by the factory"
                  : "updated manually"}
              </Badge>
            </>
          ) : (
            "Not saved yet."
          )}
        </p>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="History"
            title="Who changed the learnings, and what they said before"
            render={
              <Link href={`/projects/${projectId}/audit?surface=learnings`} />
            }
          >
            <History className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={previewing ? "Edit" : "Preview"}
            onClick={() => setPreviewing((p) => !p)}
          >
            {previewing ? <Pencil className="size-4" /> : <Eye className="size-4" />}
          </Button>
        </div>
      </div>

      {previewing ? (
        <div className="rounded-md border bg-muted/30 p-3">
          {content.trim() ? (
            <MarkdownView>{content}</MarkdownView>
          ) : (
            <p className="text-sm text-muted-foreground">
              Nothing learned yet.
            </p>
          )}
        </div>
      ) : (
        <MarkdownEditor
          rows={16}
          value={content}
          onChange={setContent}
          orgId={orgId}
          placeholder="Freeform notes the factory accumulates as it works this project — edit directly, or let runs contribute automatically."
        />
      )}

      {error && <p className="text-sm font-medium text-destructive">{error}</p>}

      {dirty && !previewing && (
        <div className="flex justify-end">
          <Button size="sm" disabled={saving} onClick={handleSave}>
            {saving && <Loader2 className="size-4 animate-spin" />}
            Save
          </Button>
        </div>
      )}

      {pending.length > 0 && (
        <div className="grid gap-2 rounded-md border border-amber-500/40 p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Pending review ({pending.length})
          </h3>
          <p className="text-xs text-muted-foreground">
            Agent-submitted learnings waiting on your decision — nothing
            reaches the document (or future run contexts) until you approve.
          </p>
          <ul className="grid gap-2">
            {pending.map((s) => (
              <PendingSubmissionCard
                key={s.id}
                submission={s}
                projectId={projectId}
              />
            ))}
          </ul>
        </div>
      )}

      {decided.length > 0 && (
        <div className="grid gap-2 rounded-md border p-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Recent worker contributions
          </h3>
          <ul className="grid gap-1.5">
            {decided.map((s) => (
              <li key={s.id} className="text-sm">
                <span className="text-muted-foreground">
                  {s.worker} · {formatUpdatedAt(s.createdAt)}
                  {s.status === "rejected" && " · rejected"}:
                </span>{" "}
                {s.text}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            Raw submissions from workers — the document above is the curated
            merge of the approved ones.
          </p>
        </div>
      )}
    </div>
  );
}
