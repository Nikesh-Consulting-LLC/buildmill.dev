"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, X } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { AgentText } from "@/components/agent-text";
import { DiffView } from "@/components/diff-view";
import { toastError, toastSuccess } from "@/components/ui/toast";
import { confirmDialog } from "@/components/ui/confirm-dialog";
import { diffStats, unifiedDiff } from "@/lib/line-diff";
import { metaForKind } from "@/lib/instruction-kinds";
import { AGENTS_KEY } from "@/lib/template-files";

export type ProposedFile = {
  id: string;
  /** `agents` for the document, otherwise a run kind. */
  key: string;
  /** Repo-relative path — AGENTS.md or .buildmill/<File>.md. */
  path: string;
  severity: string;
  rationale: string;
  proposedText: string;
  currentText: string;
  /** A pre-Phase-100 section proposal, shown but not editable. */
  isLegacy: boolean;
  status: string;
  decisionNote: string;
};

const SEVERITY_CLASSES: Record<string, string> = {
  severe:
    "border-red-200 bg-red-100 text-red-700 dark:border-red-900 dark:bg-red-950 dark:text-red-300",
  major:
    "border-amber-200 bg-amber-100 text-amber-700 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300",
  minor:
    "border-blue-200 bg-blue-100 text-blue-700 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-300",
  trivial: "",
};

function titleFor(file: ProposedFile): string {
  if (file.key === AGENTS_KEY) return "Agent Instructions";
  return metaForKind(file.key).title;
}

/** US-43.3 → us-100.5: the whole pass, one decision, a diff per file.
 *
 * A refresh is one agent's coherent read of what is wrong with this
 * project's instructions. It is accepted or rejected WHOLE (AC1b) — splitting
 * it into per-file votes would let a manager take a change that only makes
 * sense alongside another. So there are two buttons, and each decides every
 * file. A 90%-right file need not be rejected with the rest: the proposed
 * text is editable before applying, and the edit is what gets written.
 *
 * Accepting writes the factory's text — `projects.agent_instructions` and
 * `worker_instructions` — through `decide_guidelines_refresh`, one
 * transaction, and leaves the project UNPUBLISHED (us-99.4): the manager
 * publishes when ready; nothing reaches the repository from here. */
export function RefreshReview({
  refreshId,
  projectId,
  status,
  summary,
  scope,
  focus,
  workerName,
  files,
}: {
  refreshId: string;
  projectId: string;
  status: string;
  summary: string;
  scope: string;
  focus: string;
  workerName: string;
  files: ProposedFile[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<"accept" | "reject" | null>(null);
  const pending = files.filter((f) => f.status === "pending");
  const isOpen = status === "pending" && pending.length > 0;
  // Edited proposed text per file — what gets written if the pass is taken.
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [editing, setEditing] = useState<Record<string, boolean>>({});

  async function decide(accept: boolean) {
    if (accept) {
      const ok = await confirmDialog({
        title: `Apply ${pending.length} file${pending.length === 1 ? "" : "s"}?`,
        description:
          "This replaces the project's Agent Instructions and per-task instructions with the proposed text. Nothing is committed to the repository until you publish.",
        confirmLabel: "Apply all",
      });
      if (!ok) return;
    }
    setBusy(accept ? "accept" : "reject");
    const supabase = createClient();
    try {
      if (accept) {
        for (const file of pending) {
          const edited = edits[file.id];
          if (edited !== undefined && edited !== file.proposedText) {
            const { error: editError } = await supabase
              .from("guideline_recommendations")
              .update({ proposed_text: edited })
              .eq("id", file.id);
            if (editError) throw new Error(editError.message);
          }
        }
      }
      const { data, error } = await supabase.rpc("decide_guidelines_refresh", {
        p_refresh: refreshId,
        p_accept: accept,
        p_note: "",
      });
      if (error) throw new Error(error.message);
      const result = data as unknown as { applied: number; rejected: number };
      toastSuccess(
        accept
          ? `Instructions updated — ${result?.applied ?? pending.length} file${
              (result?.applied ?? pending.length) === 1 ? "" : "s"
            } applied. Publish when ready.`
          : "Pass rejected — nothing changed"
      );
      router.refresh();
    } catch (e) {
      toastError(e instanceof Error ? e.message : "Could not decide the pass");
      router.refresh();
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Instructions refresh from {workerName}
          </CardTitle>
          <CardDescription>
            {summary || "No summary was given."}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-muted-foreground">
            {status === "decided" ? (
              <>This pass is closed.</>
            ) : (
              <>
                <span className="font-medium text-foreground">
                  {pending.length}
                </span>{" "}
                file{pending.length === 1 ? "" : "s"} proposed — accepted or
                rejected as one pass
              </>
            )}
            <span className="ml-2 text-xs">
              (scope:{" "}
              {scope === "document" || scope === "existing"
                ? "Agent Instructions only"
                : "Agent Instructions and per-task files"}
              {focus ? ` · focus: ${focus}` : ""})
            </span>
          </div>
          {isOpen ? (
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => void decide(false)}
                disabled={busy !== null}
              >
                {busy === "reject" ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <X className="size-4" />
                )}
                Reject pass
              </Button>
              <Button
                size="sm"
                onClick={() => void decide(true)}
                disabled={busy !== null}
              >
                {busy === "accept" ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Check className="size-4" />
                )}
                Apply all
              </Button>
            </div>
          ) : status === "decided" && files.some((f) => f.status === "accepted") ? (
            <Button
              size="sm"
              variant="outline"
              render={<Link href={`/projects/${projectId}?tab=guidelines`} />}
            >
              Review and publish
            </Button>
          ) : null}
        </CardContent>
      </Card>

      {files.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            The agent read the repository and proposed nothing. That is an
            answer — the instructions it found were already right, or there
            was not enough in the repo to ground a change.
          </CardContent>
        </Card>
      ) : null}

      {files.map((file) => {
        const isPending = file.status === "pending";
        const value = edits[file.id] ?? file.proposedText;
        const stats = diffStats(file.currentText, value);
        const diff = unifiedDiff(file.path, file.currentText, value);
        return (
          <Card key={file.id} className={isPending ? "" : "opacity-70"}>
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle className="text-base">{titleFor(file)}</CardTitle>
                <span className="font-mono text-xs text-muted-foreground">
                  {file.path}
                </span>
                <Badge
                  variant="outline"
                  className={SEVERITY_CLASSES[file.severity] ?? ""}
                >
                  {file.severity}
                </Badge>
                <span className="text-xs tabular-nums text-muted-foreground">
                  <span className="text-emerald-700 dark:text-emerald-300">
                    +{stats.added}
                  </span>{" "}
                  <span className="text-red-700 dark:text-red-300">
                    −{stats.removed}
                  </span>
                </span>
                {!isPending ? (
                  <Badge variant="outline">
                    {file.status === "accepted" ? "applied" : "rejected"}
                  </Badge>
                ) : null}
                {file.isLegacy ? (
                  <Badge variant="outline">legacy section</Badge>
                ) : null}
                {isPending && !file.isLegacy ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-auto"
                    onClick={() =>
                      setEditing((prev) => ({ ...prev, [file.id]: !prev[file.id] }))
                    }
                  >
                    {editing[file.id] ? "Show diff" : "Edit proposed text"}
                  </Button>
                ) : null}
              </div>
              <AgentText clamp={240} className="text-sm text-muted-foreground">
                {file.rationale}
              </AgentText>
            </CardHeader>
            <CardContent>
              {isPending && editing[file.id] ? (
                <Textarea
                  className="min-h-64 font-mono text-xs"
                  value={value}
                  onChange={(e) =>
                    setEdits((prev) => ({ ...prev, [file.id]: e.target.value }))
                  }
                />
              ) : diff ? (
                <DiffView diff={diff} />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Identical to the current file.
                </p>
              )}
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
