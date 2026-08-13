"use client";

import { useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Bot,
  ChevronLeft,
  ChevronRight,
  User,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { StageState } from "@/lib/stage-tracker";
import { ActorIcon, STATE_STYLES, StateIcon } from "@/components/stage-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { MarkdownView } from "@/components/markdown-view";
import { PIPELINE_STAGES } from "./help-content";
import type { HelpText } from "./use-help-text";

/** US-2.30: the us-2.23 rail as a step-through. Selecting a stage animates
 * the pills to that moment in a work item's life — everything before it
 * complete, the stage itself in-progress (agent) or waiting (person),
 * everything after not started. */

function stateFor(index: number, selected: number): StageState {
  if (index < selected) return "complete";
  if (index > selected) return "not-started";
  return PIPELINE_STAGES[index].actor === "person" ? "waiting" : "in-progress";
}

export function PipelineWalkthrough({ text }: { text: HelpText }) {
  const [selected, setSelected] = useState(0);
  const stage = PIPELINE_STAGES[selected];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-1">
        {PIPELINE_STAGES.map((s, i) => (
          <span key={s.key} className="contents">
            {i > 0 && (
              <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
            )}
            <button
              type="button"
              onClick={() => setSelected(i)}
              aria-pressed={i === selected}
              className={cn(
                "flex min-w-0 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-full px-2 py-1.5 text-xs font-medium transition-colors duration-300",
                STATE_STYLES[stateFor(i, selected)],
                i === selected && "ring-2 ring-ring ring-offset-1 ring-offset-background"
              )}
            >
              <StateIcon state={stateFor(i, selected)} />
              <span className="truncate">{s.label}</span>
              <ActorIcon actor={s.actor} />
            </button>
          </span>
        ))}
      </div>

      <Card>
        <CardContent className="flex flex-col gap-3 pt-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="flex items-center gap-2">
              <span className="text-base font-semibold">{stage.label}</span>
              <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                {stage.actor === "agent" ? (
                  <Bot className="size-3" />
                ) : (
                  <User className="size-3" />
                )}
                {stage.actorLabel ??
                  (stage.actor === "agent" ? "The factory acts" : "You act")}
              </span>
            </span>
            <span className="flex items-center gap-1.5">
              <Button
                variant="outline"
                size="sm"
                disabled={selected === 0}
                onClick={() => setSelected((i) => Math.max(0, i - 1))}
              >
                <ChevronLeft className="size-3.5" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={selected === PIPELINE_STAGES.length - 1}
                onClick={() =>
                  setSelected((i) =>
                    Math.min(PIPELINE_STAGES.length - 1, i + 1)
                  )
                }
              >
                Next
                <ChevronRight className="size-3.5" />
              </Button>
            </span>
          </div>
          <MarkdownView>{text(`help/pipeline/${stage.key}`)}</MarkdownView>
          <Link
            href={stage.href}
            className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
          >
            {stage.linkLabel}
            <ArrowRight className="size-3.5" />
          </Link>
        </CardContent>
      </Card>
    </div>
  );
}
