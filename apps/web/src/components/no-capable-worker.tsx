import { UserX } from "lucide-react";
import { cn } from "@/lib/utils";
import { capabilityGapText, type CapabilityGap } from "@/lib/capability-gap";

export { capabilityGapText, type CapabilityGap };

/**
 * us-107.2: one visual for "nothing in the pool can take this run".
 *
 * The condition already had a name and a computed reason — `eligibilityByIssue`
 * in the Workbench's `loadFactoryHealth` has worked out *why* nothing can claim
 * a run since US-35.5 — and then rendered it **nowhere**. So the manager saw a
 * run that never moved and had to go and reason about pool membership, project
 * access and per-kind toggles by hand.
 *
 * This is the shared treatment for every kind of agent run — `plan`, `code`,
 * `guidelines`, a release prep — so that "no capable worker" looks the same
 * wherever it appears and is learned once.
 *
 * **`UserX`, not `Hourglass` or `AlertTriangle`.** Those two are already taken
 * and mean different things: `Hourglass` is *held by a rule* (us-74.5 — the
 * factory has decided to wait, and will proceed on its own), `AlertTriangle` is
 * a generic warning. This is neither: the work is legal and ready, there is
 * simply nobody to do it. It never resolves on its own, and it is fixed by
 * changing the pool — which is why the badge links there.
 */

/** The icon itself, exported so a surface too small for the badge — a table
 *  cell, a dense row — can still use the same glyph rather than inventing one. */
export const NoCapableWorkerIcon = UserX;

export function NoCapableWorker({
  gap,
  kind,
  className,
  compact = false,
}: {
  gap: CapabilityGap;
  /** The run kind, so `kind-disabled` can name the checkbox to tick. */
  kind?: string;
  className?: string;
  /** Icon + short label only — for rows and cells. */
  compact?: boolean;
}) {
  const detail = capabilityGapText(gap, kind);
  return (
    <span
      title={detail}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium",
        "border-amber-300 bg-amber-50 text-amber-900",
        "dark:border-amber-900 dark:bg-amber-950 dark:text-amber-200",
        className,
      )}
    >
      <NoCapableWorkerIcon className="size-3.5 shrink-0" />
      {compact ? "No capable worker" : detail}
    </span>
  );
}
