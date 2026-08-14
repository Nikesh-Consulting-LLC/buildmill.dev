"use client";

// US-19.1 → US-91.19: this was four tabs; three of them restated pages that
// do the same job better — `/factory-queue` (the queue in worker-pull order),
// `/activity` (every finished run) and `/releases` (every build cut). What is
// left is what only this page does: what an agent is holding right now, what
// merged work is waiting on a release, and what needs a decision.
//
// One page, no facets. The tab bar cost a control, a decision, and 555px of
// bar on a phone (us-92.1).

import Link from "next/link";

import { InProgressSection } from "./in-progress-section";
import { ReleaseSuggestions } from "./release-suggestions";
import { WaitingList } from "./waiting-list";
import type { GuidelineRecommendation } from "./guideline-recommendations-group";
import type { GuidelineRefresh } from "./guideline-refresh-group";
import type {
  AgentItem,
  FeatureRunInfo,
  ReleaseSuggestion,
  TodoGroup,
} from "./data";

export function DashboardTabs({
  groups,
  recommendations,
  refreshes,
  agentItems,
  featureRuns,
  interactiveByPrincipal,
  releaseSuggestions,
  orgId,
}: {
  groups: TodoGroup[];
  recommendations: GuidelineRecommendation[];
  refreshes: GuidelineRefresh[];
  agentItems: AgentItem[];
  /** US-86.2: live feature-owned runs, keyed by feature issue id. */
  featureRuns: Record<string, FeatureRunInfo>;
  /** US-91.3: which claiming agents run the `interactive` module. */
  interactiveByPrincipal: Record<string, boolean>;
  /** US-91.18: projects holding merged work no release has shipped. */
  releaseSuggestions: ReleaseSuggestion[];
  /** US-84.1: passed through to WaitingList for the feature-header batch. */
  orgId: string;
}) {
  // US-91.19 AC2: "In the factory" was not purely duplicative — it showed
  // QUEUED and HELD work, which In Progress deliberately omits (us-91.2 AC2).
  // Deleting the tab would have made unclaimable work invisible on the page a
  // manager opens to find out why nothing is moving. It is a condition, not a
  // list, so it gets one line.
  const claimed = new Set(
    agentItems.filter((i) => i.runId && i.workerName).map((i) => i.id)
  );
  const queued = agentItems.filter(
    (i) => !claimed.has(i.id) && !(i.parent && featureRuns[i.parent.id])
  );
  const held = queued.filter((i) => !!i.holdReason).length;

  return (
    <div className="grid min-w-0 gap-5">
      <InProgressSection
        items={agentItems}
        featureRuns={featureRuns}
        interactiveByPrincipal={interactiveByPrincipal}
      />

      {queued.length > 0 && (
        <p className="text-xs text-muted-foreground">
          <Link
            href="/factory-queue"
            className="underline-offset-4 hover:text-foreground hover:underline"
          >
            {queued.length} queued
            {held > 0 && ` · ${held} waiting on something`}
          </Link>{" "}
          in the factory, unclaimed.
        </p>
      )}

      {/* US-91.18: work that is built and waiting on a cut. */}
      <ReleaseSuggestions suggestions={releaseSuggestions} />

      <WaitingList
        groups={groups}
        recommendations={recommendations}
        refreshes={refreshes}
        orgId={orgId}
      />

      {/* AC3: nothing is orphaned — the two pages that used to be tabs. */}
      <p className="text-xs text-muted-foreground">
        Finished runs are on{" "}
        <Link
          href="/activity"
          className="underline-offset-4 hover:text-foreground hover:underline"
        >
          Activity
        </Link>
        ; builds and their state are on{" "}
        <Link
          href="/releases"
          className="underline-offset-4 hover:text-foreground hover:underline"
        >
          Release
        </Link>
        .
      </p>
    </div>
  );
}
