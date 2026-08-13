"use client";

import { useEffect, useState } from "react";
import { FileQuestion, Loader2 } from "lucide-react";
import { createClient } from "@/lib/supabase/client";
import { MarkdownView } from "@/components/markdown-view";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

/** US-25.1: a near-full-screen reader for one artifact, for quick looks from
 * somewhere else.
 *
 * It exists for "I am triaging a list and want to read this without losing my
 * place". It is NOT how an artifact renders on the page that owns it — the work
 * item's own tabs, the review surface and release notes all keep rendering
 * inline, because that is the page you went to in order to read them. The test
 * for a future caller: did the manager come here to read this? If yes, render
 * it. If they came to do something else and want a glance, open the reader.
 *
 * Surface-agnostic on purpose: it takes a work item and an artifact kind, so
 * adding a caller is an import and a trigger. Today only Waiting on you's peek
 * calls it — the Factory queue and the Work items hub have no peek to hang it
 * off yet.
 *
 * No approve, no edit. Approving is the review surface's decision, and a gate
 * action inside a quick-look overlay invites approving something skimmed. */

export type ArtifactKind = "plan" | "test_plan" | "prd";

const KIND_LABEL: Record<ArtifactKind, string> = {
  plan: "Plan",
  test_plan: "Test plan",
  prd: "PRD",
};

const KIND_EMPTY: Record<ArtifactKind, string> = {
  plan: "No plan has been written for this work item yet.",
  test_plan: "No test plan has been written for this work item yet.",
  prd: "No PRD has been drafted for this work item yet.",
};

async function fetchArtifact(
  issueId: string,
  kind: ArtifactKind
): Promise<string | null> {
  const { data } = await createClient()
    .from("artifacts")
    .select("content, version")
    .eq("issue_id", issueId)
    .eq("kind", kind)
    .order("version", { ascending: false })
    .limit(1);
  const content = (data ?? [])[0]?.content;
  return typeof content === "string" && content.trim() ? content : null;
}

export function ArtifactReader({
  issueId,
  kind,
  workItem,
  triggerClassName,
  children,
}: {
  issueId: string;
  kind: ArtifactKind;
  /** Named in the overlay header, so the reader always says what it is showing
   * and which item it belongs to. */
  workItem: { title: string; displayId?: string | null };
  triggerClassName?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [content, setContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // Keyed on `open` rather than fired from the trigger's click: opening and
  // loading cannot then come apart, which is exactly how the TLDR popup
  // (us-25.3) ended up opening onto an empty body.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    setError(false);
    fetchArtifact(issueId, kind)
      .then((c) => {
        if (!cancelled) setContent(c);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, issueId, kind]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        className={cn(
          "cursor-pointer text-left hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:rounded-sm",
          triggerClassName
        )}
        title={`Read the ${KIND_LABEL[kind].toLowerCase()} full-screen`}
      >
        {children}
      </DialogTrigger>

      {/* Near-full-screen: the point of the story is that a thousand-word plan
          is unreadable in a row-height box. p-0/gap-0 so the body owns its own
          scroll region rather than the popup growing past the viewport. */}
      <DialogContent className="flex h-[90vh] w-[min(72rem,calc(100vw-2rem))] max-w-none flex-col gap-0 p-0 sm:max-w-none">
        <DialogHeader className="shrink-0 gap-1 border-b p-4 pr-12">
          <DialogTitle>{KIND_LABEL[kind]}</DialogTitle>
          <DialogDescription className="truncate">
            {workItem.displayId ? `${workItem.displayId} · ` : ""}
            {workItem.title}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {loading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading{" "}
              {KIND_LABEL[kind].toLowerCase()}…
            </p>
          ) : error ? (
            <p className="text-sm text-destructive">
              Couldn&apos;t load the {KIND_LABEL[kind].toLowerCase()} — open the
              work item instead.
            </p>
          ) : content ? (
            <MarkdownView>{content}</MarkdownView>
          ) : (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <FileQuestion className="size-4" />
              {KIND_EMPTY[kind]}
            </p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
