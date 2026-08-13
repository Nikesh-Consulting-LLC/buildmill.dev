"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import Link from "next/link";
import { Loader2, MessageCircleQuestion, Send } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { AgentText } from "@/components/agent-text";

export type OpenClarification = {
  id: string;
  issueId: string;
  orgId: string;
  issueTitle: string;
  project: string;
  projectId: string;
  worker: string;
  question: string;
  age: string;
  /** US-14.9: the concrete resolutions the agent already worked out.
   * Absent → a free-text question, exactly as before. */
  options?: { label: string; description?: string }[] | null;
  multiSelect?: boolean;
};

/** US-5.4: open worker questions on Things to Do. Answering updates the
 * clarification row, appends the Q&A pair to the work item's instruction
 * set (us-5.11) so it survives re-dispatch, and writes the audit event —
 * all plain CRUD under RLS. */
export function ClarificationsCard({
  items,
}: {
  items: OpenClarification[];
}) {
  const router = useRouter();
  const [answers, setAnswers] = useState<Record<string, string>>({});
  // US-14.9: labels picked, per question. Free text lives in `answers` and
  // is always available alongside — a manager must never be trapped into a
  // choice the agent happened to think of.
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!items.length) return null;

  function togglePick(item: OpenClarification, label: string) {
    setPicked((prev) => {
      const current = prev[item.id] ?? [];
      if (item.multiSelect) {
        return {
          ...prev,
          [item.id]: current.includes(label)
            ? current.filter((l) => l !== label)
            : [...current, label],
        };
      }
      return { ...prev, [item.id]: current.includes(label) ? [] : [label] };
    });
  }

  async function answer(item: OpenClarification) {
    const text = (answers[item.id] ?? "").trim();
    const chosen = picked[item.id] ?? [];
    if (!text && !chosen.length) {
      setError(
        item.options?.length
          ? "Pick an option, or write an answer."
          : "An answer is required."
      );
      return;
    }
    setError(null);
    setBusyId(item.id);
    const supabase = createClient();
    try {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      const { error: updateError } = await supabase
        .from("clarifications")
        .update({
          answer: text || null,
          selected_options: chosen.length ? chosen : null,
          answered_at: new Date().toISOString(),
          answered_by: user?.id ?? null,
        })
        .eq("id", item.id);
      if (updateError) throw new Error(updateError.message);

      // Append the pair to the instruction set so the answer reaches any
      // future claimer of a retry instead of being re-asked.
      const { data: issueRow, error: readError } = await supabase
        .from("issues")
        .select("instruction_set")
        .eq("id", item.issueId)
        .single();
      if (readError) throw new Error(readError.message);
      // US-14.9: the instruction set must stand alone — a future claimer
      // reads this, not the buttons. Labels resolve back to their
      // descriptions so a bare word can never be the whole answer.
      const chosenLines = chosen.map((label) => {
        const opt = item.options?.find((o) => o.label === label);
        return opt?.description ? `${label} — ${opt.description}` : label;
      });
      const answerText = [
        chosenLines.length ? `Chose: ${chosenLines.join("; ")}` : null,
        text || null,
      ]
        .filter(Boolean)
        .join("\n");
      const block =
        `**Clarification (${new Date().toLocaleDateString()}):**\n` +
        `**Q:** ${item.question}\n**A:** ${answerText}`;
      const current = (issueRow?.instruction_set ?? "").trimEnd();
      const { error: issueError } = await supabase
        .from("issues")
        .update({
          instruction_set: current ? `${current}\n\n${block}` : block,
        })
        .eq("id", item.issueId);
      if (issueError) throw new Error(issueError.message);

      await supabase.from("issue_events").insert({
        org_id: item.orgId,
        issue_id: item.issueId,
        type: "clarification-answered",
        payload: {
          clarification_id: item.id,
          question: item.question,
          answer: answerText,
          selected_options: chosen.length ? chosen : null,
        },
      });

      setAnswers((prev) => ({ ...prev, [item.id]: "" }));
      setPicked((prev) => ({ ...prev, [item.id]: [] }));
      router.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Card className="min-w-0">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <MessageCircleQuestion className="size-4 text-muted-foreground" />
          Questions from workers ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-3">
        {items.map((item) => (
          <div key={item.id} className="grid gap-2 rounded-lg border p-3">
            <div className="min-w-0">
              <Link
                href={`/issues/${item.issueId}?from=dashboard`}
                className="block truncate text-sm font-medium hover:underline"
              >
                {item.issueTitle}
              </Link>
              <p className="truncate text-xs text-muted-foreground">
                {item.project} · asked by {item.worker} · {item.age} ago
              </p>
            </div>
            {/* US-14.1: the question is the thing being answered — a
                1,184-character numbered list arrived here as a wall of
                literal asterisks. Clamped, because the hub is a summary. */}
            <AgentText clamp={220}>{item.question}</AgentText>
            {/* US-14.9: the agent usually knows the two or three answers
                worth considering. Radio semantics for one, checkbox for
                many — and the free-text box below never goes away, so
                "none of these" is always sayable. */}
            {!!item.options?.length && (
              <div
                role={item.multiSelect ? "group" : "radiogroup"}
                aria-label="Answer options"
                className="grid gap-1.5"
              >
                {item.options.map((opt) => {
                  const on = (picked[item.id] ?? []).includes(opt.label);
                  return (
                    <button
                      key={opt.label}
                      type="button"
                      role={item.multiSelect ? "checkbox" : "radio"}
                      aria-checked={on}
                      onClick={() => togglePick(item, opt.label)}
                      className={`rounded-md border px-3 py-2 text-left text-sm transition-colors ${
                        on
                          ? "border-primary bg-primary/10"
                          : "hover:bg-muted/60"
                      }`}
                    >
                      <span className="font-medium">{opt.label}</span>
                      {opt.description && (
                        <span className="block text-xs text-muted-foreground">
                          {opt.description}
                        </span>
                      )}
                    </button>
                  );
                })}
                <p className="text-xs text-muted-foreground">
                  {item.multiSelect
                    ? "Pick any that apply"
                    : "Pick one"}{" "}
                  — or ignore these and answer in your own words below.
                </p>
              </div>
            )}
            <div className="flex items-start gap-2">
              <Textarea
                rows={2}
                placeholder={
                  item.options?.length
                    ? "Add anything else — or answer here instead."
                    : "Your answer — it lands on the work item's instruction set."
                }
                value={answers[item.id] ?? ""}
                onChange={(e) =>
                  setAnswers((prev) => ({
                    ...prev,
                    [item.id]: e.target.value,
                  }))
                }
              />
              <Button
                size="sm"
                onClick={() => answer(item)}
                disabled={busyId !== null}
              >
                {busyId === item.id ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Send className="size-4" />
                )}
                Answer
              </Button>
            </div>
          </div>
        ))}
        {error && (
          <p className="text-sm font-medium text-destructive">{error}</p>
        )}
      </CardContent>
    </Card>
  );
}
