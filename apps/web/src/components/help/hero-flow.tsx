"use client";

import { cn } from "@/lib/utils";
import { HERO_STEPS, type HeroStep } from "./help-content";

/** US-2.30: the whole journey in one glance. Person steps are amber
 * (waiting on you), agent steps blue (factory working), shipped steps
 * emerald — the same reading as the stage tracker. Connector dashes drift
 * forward via CSS only; `prefers-reduced-motion` stills them. */

const TONE_STYLES: Record<HeroStep["tone"], string> = {
  person:
    "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200",
  agent:
    "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-700 dark:bg-blue-950 dark:text-blue-200",
  shipped:
    "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-200",
};

function Connector() {
  return (
    <svg
      width="20"
      height="10"
      viewBox="0 0 20 10"
      aria-hidden
      className="shrink-0 text-muted-foreground/60"
    >
      <line
        x1="1"
        y1="5"
        x2="19"
        y2="5"
        strokeWidth="2"
        strokeDasharray="4 4"
        className="help-dash stroke-current"
      />
    </svg>
  );
}

export function HeroFlow() {
  return (
    <div className="overflow-x-auto">
      <div className="flex w-full min-w-fit items-center gap-1.5 py-1 sm:gap-2">
        {HERO_STEPS.map((step, i) => (
          <span key={step.label} className="contents">
            {i > 0 && <Connector />}
            <span
              className={cn(
                "inline-flex flex-1 items-center justify-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1.5 text-xs font-medium sm:text-sm",
                TONE_STYLES[step.tone]
              )}
            >
              <step.icon className="size-3.5 shrink-0" />
              {step.label}
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
