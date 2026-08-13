"use client";

import { useState } from "react";
import { useRouter } from "@/lib/router-with-progress";
import { Check, Loader2, X } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AgentText } from "@/components/agent-text";

export type GuidelineRecommendation = {
  id: string;
  project: string;
  projectId: string;
  worker: string;
  severity: string;
  sectionTitle: string;
  newSection: boolean;
  currentText: string;
  proposedText: string;
  rationale: string;
  age: string;
};

const SEVERITY_ORDER: Record<string, number> = {
  severe: 0,
  major: 1,
  minor: 2,
  trivial: 3,
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

const PREVIEW_CHARS = 700;

function preview(text: string) {
  if (text.length <= PREVIEW_CHARS) return text;
  return `${text.slice(0, PREVIEW_CHARS)}…`;
}

/** us-5.32: pending agent recommendations against the guidelines, one
 * card each, sorted severe → trivial. Accept applies the proposed text
 * (creating the section when new) via the decide RPC; reject takes an
 * optional note. Severity is the agent's claim — the manager is the
 * gate. */
export function GuidelineRecommendationsGroup({
  items,
}: {
  items: GuidelineRecommendation[];
}) {
  const router = useRouter();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (!items.length) return null;

  const sorted = [...items].sort(
    (a, b) =>
      (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );

  async function decide(item: GuidelineRecommendation, accept: boolean) {
    setError(null);
    setBusyId(item.id);
    const supabase = createClient();
    const { error: rpcError } = await supabase.rpc(
      "decide_guideline_recommendation",
      {
        p_recommendation: item.id,
        p_accept: accept,
        p_note: accept ? "" : note,
      }
    );
    setBusyId(null);
    if (rpcError) {
      setError(rpcError.message);
      return;
    }
    setRejectingId(null);
    setNote("");
    router.refresh();
  }

  return (
    <div className="grid gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Guideline recommendations ({items.length})
      </h3>
      {sorted.map((item) => (
        <div key={item.id} className="grid gap-2 rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <Badge
                variant="outline"
                className={SEVERITY_CLASSES[item.severity] ?? ""}
              >
                {item.severity}
              </Badge>
              <p className="truncate text-sm font-medium">
                {item.newSection
                  ? `New section: ${item.sectionTitle}`
                  : item.sectionTitle}
              </p>
            </div>
            <p className="shrink-0 text-xs text-muted-foreground">
              {item.project} · {item.worker} · {item.age} ago
            </p>
          </div>
          {/* US-14.1: the agent's argument for the change, as written.
              The proposed/current blocks below stay literal on purpose —
              they are the guideline text itself, shown for comparison. */}
          <AgentText clamp={200} className="text-muted-foreground">
            {item.rationale}
          </AgentText>
          <div className="grid gap-2 md:grid-cols-2">
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                {item.newSection ? "Before (no section)" : "Before"}
              </p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                {item.currentText ? preview(item.currentText) : "—"}
              </pre>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                After
              </p>
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-muted/50 p-2 text-xs">
                {preview(item.proposedText)}
              </pre>
            </div>
          </div>
          {rejectingId === item.id && (
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
              disabled={busyId !== null}
              onClick={() => decide(item, true)}
            >
              {busyId === item.id ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              Accept &amp; apply
            </Button>
            {rejectingId === item.id ? (
              <Button
                size="sm"
                variant="destructive"
                disabled={busyId !== null}
                onClick={() => decide(item, false)}
              >
                <X className="size-4" />
                Confirm reject
              </Button>
            ) : (
              <Button
                size="sm"
                variant="outline"
                disabled={busyId !== null}
                onClick={() => {
                  setRejectingId(item.id);
                  setNote("");
                }}
              >
                <X className="size-4" />
                Reject
              </Button>
            )}
          </div>
        </div>
      ))}
      {error && <p className="text-sm font-medium text-destructive">{error}</p>}
    </div>
  );
}
