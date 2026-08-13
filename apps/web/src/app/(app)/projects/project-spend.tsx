/**
 * US-37.4: what a project has spent, at the top right of its card.
 *
 * Settings → Spend answers "where did the money go" after the fact. This
 * answers "is this project about to stop", which is the question a manager has
 * while looking at the list — and since us-37.2 an exhausted budget really does
 * stop new work, so it decides whether anything will start.
 *
 * The dollar figure shows with or without a budget: attribution does not depend
 * on a budget existing. The percentage needs one, because a percentage of
 * nothing is not a number.
 */

import { budgetState, budgetLabel, money, percent } from "@/lib/budget";
import { cn } from "@/lib/utils";

export function ProjectSpend({
  project,
  spend,
}: {
  project: { budget_enabled: boolean | null; budget_usd: number | null };
  spend: { spent: number; unmeasured: number } | undefined;
}) {
  const state = budgetState(project, spend);
  const label = budgetLabel(state);

  return (
    <div className="flex shrink-0 flex-col items-end gap-1 pl-2 text-right">
      <span
        className="font-mono text-sm tabular-nums"
        title={
          state.unmeasured > 0
            ? `${state.unmeasured} call(s) on a model with no rate are not included`
            : undefined
        }
      >
        {money(state.spent)}
        {state.unmeasured > 0 && (
          <span className="ml-1 text-amber-600 dark:text-amber-400">*</span>
        )}
      </span>

      {state.budget != null && (
        <>
          {/* Colour never carries the state on its own — the words say it too,
              so the card reads the same for anyone who cannot tell the tones
              apart. */}
          <span
            className={cn(
              "text-[11px] leading-none",
              state.exhausted
                ? "font-medium text-destructive"
                : state.near
                  ? "font-medium text-amber-700 dark:text-amber-400"
                  : "text-muted-foreground"
            )}
          >
            {percent(state.fraction ?? 0)} of {money(state.budget)}
          </span>
          <span
            className="h-1.5 w-20 overflow-hidden rounded-full bg-muted"
            role="img"
            aria-label={`${label}: ${percent(state.fraction ?? 0)} of ${money(
              state.budget
            )} used`}
          >
            <span
              className={cn(
                "block h-full rounded-full",
                state.exhausted
                  ? "bg-destructive"
                  : state.near
                    ? "bg-amber-500"
                    : "bg-primary"
              )}
              style={{ width: `${Math.min(100, (state.fraction ?? 0) * 100)}%` }}
            />
          </span>
        </>
      )}
    </div>
  );
}
