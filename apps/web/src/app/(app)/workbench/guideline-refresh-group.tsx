import Link from "next/link";
import { AlertTriangle, FileStack, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { AgentText } from "@/components/agent-text";
import { cn } from "@/lib/utils";
import { CancelStalledRefresh } from "./cancel-stalled-refresh";
import { refreshLabel, refreshState } from "./refresh-state";

export type GuidelineRefresh = {
  id: string;
  project: string;
  projectId: string;
  worker: string;
  summary: string;
  pendingSections: number;
  totalSections: number;
  /** The agent has handed back and there is something to decide. A refresh is
   *  `pending` from the moment it is DISPATCHED, so this is the only honest
   *  test for "waiting on you". */
  ready: boolean;
  age: string;
  /** us-107.2: the run behind it, so a stalled pass can be cancelled from the
   *  card rather than from the database. */
  runId: string | null;
  /** us-107.2: a worker actually holds it (`claimed_at` is set) — not merely
   *  that it was dispatched. This is the distinction the card was missing. */
  claimed: boolean;
  hoursWaiting: number;
};

/** US-43.3: one card per open guidelines refresh — the whole pass, not one
 * card per section it proposed.
 *
 * A refresh proposes fifteen to twenty sections at once. Through us-5.32's
 * per-recommendation cards that is fifteen to twenty entries interleaved with
 * the ad-hoc ones and sorted by a severity the agent declared about its own
 * work, with no way to read the document it actually wrote. The decision here
 * is "do these guidelines describe my project", and that is not a decision
 * you can make one card at a time — so this card carries only the summary and
 * a way in. The deciding happens on the review page.
 *
 * No decide controls here on purpose: accepting a section means reading it
 * against what is stored, and a dashboard card cannot show that. */
export function GuidelineRefreshGroup({ items }: { items: GuidelineRefresh[] }) {
  if (!items.length) return null;

  return (
    <div className="grid gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        Instructions refresh ({items.filter((i) => i.ready).length || items.length})
      </h3>
      {items.map((item) => {
        const state = refreshState(item);
        const stalled = state === "stalled";
        return (
        <div
          key={item.id}
          className={cn(
            "grid gap-2 rounded-lg border p-3",
            // us-107.2: a stalled pass has to look different from a working
            // one. Six days of "an agent is reading" went unnoticed precisely
            // because it looked exactly like six seconds of it.
            stalled &&
              "border-amber-300 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/50",
          )}
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              {item.ready ? (
                <FileStack className="size-4 shrink-0 text-muted-foreground" />
              ) : stalled ? (
                <AlertTriangle className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
              ) : (
                <Loader2 className="size-4 shrink-0 animate-spin text-muted-foreground" />
              )}
              <p className="truncate text-sm font-medium">
                {item.ready ? (
                  <>
                    {item.pendingSections} file
                    {item.pendingSections === 1 ? "" : "s"} proposed — one
                    decision
                    {item.totalSections > item.pendingSections
                      ? ` · ${
                          item.totalSections - item.pendingSections
                        } already decided`
                      : ""}
                  </>
                ) : (
                  refreshLabel(state)
                )}
              </p>
            </div>
            <p className="shrink-0 text-xs text-muted-foreground">
              {[item.project, item.worker || null, item.age]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          {item.summary ? (
            <AgentText clamp={200} className="text-muted-foreground">
              {item.summary}
            </AgentText>
          ) : null}
          {/* Nothing to review until the agent has handed back. A live button
              over "0 sections" invited a click that could only disappoint. */}
          {item.ready ? (
            <div className="flex justify-end">
              <Button
                size="sm"
                render={
                  <Link
                    href={`/projects/${item.projectId}/guidelines/refresh/${item.id}`}
                  />
                }
              >
                Review the pass
              </Button>
            </div>
          ) : stalled ? (
            // The way out. `requeue_expired_claims` cannot reap this — a lease
            // can only expire if it was ever taken — so the manager is the
            // only thing that can end it, and it has to be reachable here.
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs text-amber-800 dark:text-amber-200">
                Dispatched {item.age} and still unclaimed. Nothing reaps an
                unclaimed run, so it will wait indefinitely — check a capable
                worker is online, or cancel it.
              </p>
              {item.runId && <CancelStalledRefresh runId={item.runId} />}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              Nothing to do yet — this card becomes a review when the pass
              lands. You can leave the page.
            </p>
          )}
        </div>
        );
      })}
    </div>
  );
}
