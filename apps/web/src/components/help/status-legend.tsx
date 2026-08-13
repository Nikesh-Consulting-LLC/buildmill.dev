"use client";

import { cn } from "@/lib/utils";
import type { StageState } from "@/lib/stage-tracker";
import { STATE_STYLES, STATE_TITLES, StateIcon } from "@/components/stage-state";
import { StatusBadge } from "@/components/status-badge";
import { ALL_STATUSES } from "./help-content";
import type { HelpText } from "./use-help-text";

/** US-2.30: every badge and stage color, decoded in one line each. The
 * swatches are the real components/styles, so this legend cannot drift
 * from what the app shows. */

const STAGE_STATES: StageState[] = [
  "complete",
  "in-progress",
  "waiting",
  "failed",
  "not-started",
  "not-tracked",
];

export function StatusLegend({ text }: { text: HelpText }) {
  return (
    <div className="flex flex-col gap-6">
      <div>
        <h3 className="mb-3 text-sm font-semibold">Work item & run statuses</h3>
        <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
          {ALL_STATUSES.map((status) => (
            <div key={status} className="flex items-baseline gap-3">
              <span className="w-28 shrink-0">
                <StatusBadge status={status} />
              </span>
              <span className="text-sm text-muted-foreground">
                {text(`help/status/${status}`)}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="mb-3 text-sm font-semibold">Stage states</h3>
        <div className="grid gap-x-8 gap-y-2 sm:grid-cols-2">
          {STAGE_STATES.map((state) => (
            <div key={state} className="flex items-baseline gap-3">
              <span
                title={STATE_TITLES[state]}
                className={cn(
                  "inline-flex w-28 shrink-0 items-center justify-center gap-1.5 rounded-full px-2 py-1 text-xs font-medium",
                  STATE_STYLES[state]
                )}
              >
                <StateIcon state={state} />
                <span className="capitalize">{state.replace("-", " ")}</span>
              </span>
              <span className="text-sm text-muted-foreground">
                {text(`help/stage-state/${state}`)}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
