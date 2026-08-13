"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2 } from "lucide-react";
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
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { AgentText } from "@/components/agent-text";
import { toastError, toastSuccess } from "@/components/ui/toast";

export type ProposedSection = {
  id: string;
  sectionKey: string;
  title: string;
  severity: string;
  rationale: string;
  proposedText: string;
  isNew: boolean;
  currentText: string;
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

/** US-43.3, simplified by US-43.6: the whole pass, one decision.
 *
 * The first live use produced the complaint this surface was built to prevent
 * — too many steps. Per-section Accept and Skip meant up to twenty separate
 * decisions, each its own write. Now every section carries a checkbox, ticked
 * by default, and ONE button applies the lot.
 *
 * Unticking is not the same as doing nothing: a section left unticked is
 * recorded as rejected, so the refresh settles and the card clears. A pass you
 * looked at and half-took is a finished pass.
 *
 * The write still goes through `decide_guideline_recommendation` — the same
 * RPC us-5.32's ad-hoc cards use — so there is exactly one write path into
 * project_guidelines, and content_audit still attributes the change to the
 * manager rather than the agent that drafted it. */
export function RefreshReview({
  status,
  summary,
  scope,
  focus,
  workerName,
  sections,
}: {
  status: string;
  summary: string;
  scope: string;
  focus: string;
  workerName: string;
  sections: ProposedSection[];
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const pending = sections.filter((s) => s.status === "pending");
  const decided = sections.length - pending.length;

  // Ticked by default: the agent read the repository and the manager asked for
  // the pass. Unticking is the exception, so it is what costs a click.
  const [taking, setTaking] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(pending.map((s) => [s.id, true]))
  );
  // Edited text per section — a 90%-right section should not have to be
  // dropped and retyped on the Guidelines tab.
  const [edits, setEdits] = useState<Record<string, string>>({});

  const takingCount = pending.filter((s) => taking[s.id]).length;

  async function apply() {
    setBusy(true);
    const supabase = createClient();
    let applied = 0;
    try {
      for (const section of pending) {
        const accept = !!taking[section.id];
        const edited = edits[section.id];
        if (accept && edited !== undefined && edited !== section.proposedText) {
          const { error: editError } = await supabase
            .from("guideline_recommendations")
            .update({ proposed_text: edited })
            .eq("id", section.id);
          if (editError) throw new Error(editError.message);
        }
        const { error } = await supabase.rpc(
          "decide_guideline_recommendation",
          { p_recommendation: section.id, p_accept: accept, p_note: "" }
        );
        if (error) throw new Error(error.message);
        if (accept) applied += 1;
      }
      toastSuccess(
        applied
          ? `Guidelines updated — ${applied} section${
              applied === 1 ? "" : "s"
            } applied`
          : "Nothing applied — the pass is closed"
      );
      router.refresh();
    } catch (e) {
      // Deliberately not a transaction: whatever landed stays landed. A
      // failure part-way leaves a shorter review, not a rollback of sections
      // the manager already approved.
      toastError(e instanceof Error ? e.message : "Stopped part-way");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Guidelines refresh from {workerName}
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
                  {takingCount}
                </span>{" "}
                of {pending.length} section{pending.length === 1 ? "" : "s"}{" "}
                selected
                {decided > 0 ? ` · ${decided} already decided` : ""}
              </>
            )}
            <span className="ml-2 text-xs">
              (scope:{" "}
              {scope === "existing"
                ? "existing sections only"
                : "everything the repo supports"}
              {focus ? ` · focus: ${focus}` : ""})
            </span>
          </div>
          {pending.length > 0 ? (
            <Button size="sm" onClick={apply} disabled={busy}>
              {busy ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              Update guidelines
            </Button>
          ) : null}
        </CardContent>
      </Card>

      {sections.length === 0 ? (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            The agent read the repository and proposed nothing. That is an
            answer — the guidelines it found were already right, or there was
            not enough in the repo to ground a change.
          </CardContent>
        </Card>
      ) : null}

      {sections.map((section) => {
        const isPending = section.status === "pending";
        const value = edits[section.id] ?? section.proposedText;
        const ticked = !!taking[section.id];
        return (
          <Card
            key={section.id}
            className={isPending && ticked ? "" : "opacity-60"}
          >
            <CardHeader>
              <div className="flex flex-wrap items-center gap-2">
                {isPending ? (
                  <Checkbox
                    checked={ticked}
                    onCheckedChange={(v) =>
                      setTaking((prev) => ({
                        ...prev,
                        [section.id]: v === true,
                      }))
                    }
                    aria-label={`Apply ${section.title}`}
                  />
                ) : null}
                <CardTitle className="text-base">
                  {section.isNew ? `New: ${section.title}` : section.title}
                </CardTitle>
                <Badge
                  variant="outline"
                  className={SEVERITY_CLASSES[section.severity] ?? ""}
                >
                  {section.severity}
                </Badge>
                {!isPending ? (
                  <Badge variant="outline">
                    {section.status === "accepted" ? "applied" : "skipped"}
                  </Badge>
                ) : null}
              </div>
              <AgentText clamp={240} className="text-sm text-muted-foreground">
                {section.rationale}
              </AgentText>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-2">
              <div className="min-w-0">
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  {section.isNew ? "Not in your guidelines yet" : "Stored now"}
                </p>
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                  {section.isNew
                    ? "This section would be added."
                    : section.currentText || "—"}
                </pre>
              </div>
              <div className="min-w-0">
                <p className="mb-1 text-xs font-medium text-muted-foreground">
                  Proposed{" "}
                  {isPending ? (
                    <span className="font-normal">— edit before applying</span>
                  ) : null}
                </p>
                {isPending ? (
                  <Textarea
                    className="max-h-72 min-h-40 font-mono text-xs"
                    value={value}
                    onChange={(e) =>
                      setEdits((prev) => ({
                        ...prev,
                        [section.id]: e.target.value,
                      }))
                    }
                  />
                ) : (
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                    {section.proposedText}
                  </pre>
                )}
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
