"use client";

import { useId, useState } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { cn } from "@/lib/utils";
import { MarkdownView } from "@/components/markdown-view";

/** US-14.1: agent-authored prose, rendered the way the agent wrote it.
 *
 * Workers write markdown — emphasis, backticked identifiers, numbered
 * lists — deliberately, so a manager can skim to the decision. The
 * comments thread already renders it; Things to Do and the review surface
 * were dropping the same text into a plain <p>, so it arrived as literal
 * asterisks and backticks on exactly the screens where decisions are made.
 *
 * This is not a second renderer: it composes MarkdownView, and adds the
 * one thing a summary card needs that a full page does not — a height
 * clamp, so one agent's essay cannot push the rest of the hub off screen.
 *
 * `clamp` is the collapsed max height in px. Omit it (or pass 0) on a
 * surface that should always show everything, like a review gate. */
export function AgentText({
  children,
  clamp,
  className,
}: {
  children: string | null | undefined;
  clamp?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const text = children?.trim();
  if (!text) return null;

  // Cheap, deterministic guess at whether clamping will actually bite.
  // Getting this wrong only costs a redundant toggle, never hidden text:
  // the clamp itself is what hides, and the toggle always reveals.
  const mightOverflow = !!clamp && (text.length > 240 || text.includes("\n"));

  return (
    <div className={cn("min-w-0", className)}>
      <div
        id={bodyId}
        className={cn(!open && clamp ? "overflow-hidden" : undefined)}
        style={!open && clamp ? { maxHeight: clamp } : undefined}
      >
        <MarkdownView className="[&_p:first-child]:mt-0 [&_p:last-child]:mb-0">
          {text}
        </MarkdownView>
      </div>
      {mightOverflow && (
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={bodyId}
          className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
        >
          {open ? (
            <>
              <ChevronUp className="size-3" /> Show less
            </>
          ) : (
            <>
              <ChevronDown className="size-3" /> Show more
            </>
          )}
        </button>
      )}
    </div>
  );
}
