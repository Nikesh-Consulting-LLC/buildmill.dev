import { Bot, Check, Ellipsis, Pause, User, X } from "lucide-react";
import type { StageActor, StageState } from "@/lib/stage-tracker";

/** US-2.23 state colors, shared with the /help legend and walkthrough
 * (US-2.30) so the product and its documentation cannot drift apart. The
 * three base states are unmistakable — solid green (complete), solid blue
 * (factory working), inert gray (not yet started) — with amber (waiting on
 * you) and red (failed) overlays on the current stage. Every state pairs
 * its color with an icon; color never carries the meaning alone. */
export const STATE_STYLES: Record<StageState, string> = {
  complete: "bg-emerald-600 text-white dark:bg-emerald-700",
  "in-progress": "bg-blue-600 text-white dark:bg-blue-600",
  waiting: "bg-amber-400 text-amber-950 dark:bg-amber-500",
  failed: "bg-red-600 text-white dark:bg-red-700",
  "not-started": "bg-muted text-muted-foreground",
  "not-tracked":
    "border border-dashed bg-transparent text-muted-foreground/70",
};

export const STATE_TITLES: Record<StageState, string> = {
  complete: "Complete",
  "in-progress": "In progress — the factory is working",
  waiting: "Waiting on you",
  failed: "Failed",
  "not-started": "Not yet started",
  "not-tracked": "Not tracked — no deployment classified for this environment",
};

export function StateIcon({ state }: { state: StageState }) {
  if (state === "complete") return <Check className="size-3.5 shrink-0" />;
  if (state === "in-progress")
    return <Ellipsis className="size-3.5 shrink-0 animate-pulse" />;
  if (state === "waiting") return <Pause className="size-3.5 shrink-0" />;
  if (state === "failed") return <X className="size-3.5 shrink-0" />;
  return null;
}

export function ActorIcon({ actor }: { actor: StageActor }) {
  const Icon = actor === "agent" ? Bot : User;
  return (
    <Icon
      className="size-3 shrink-0 opacity-70"
      aria-label={
        actor === "agent" ? "Agent works this stage" : "Person works this stage"
      }
    />
  );
}
