import type { IssueStatus } from "@/components/status-badge";

export type EpicStatus = "open" | "completed";

export type EpicIssueType = "feature" | "bug" | "chore" | "story";

export type EpicRow = {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  status: EpicStatus;
  created_at: string;
  updated_at: string;
};

export type EpicMemberIssue = {
  id: string;
  title: string;
  type: EpicIssueType;
  status: IssueStatus;
  parent_id: string | null;
  abandoned_at: string | null;
};

export type EpicRollup = {
  /** Non-abandoned members only. */
  total: number;
  done: number;
  abandoned: number;
  byType: Record<EpicIssueType, number>;
  percent: number;
  readyToComplete: boolean;
};

export type EpicFeatureGroup = {
  feature: EpicMemberIssue;
  children: EpicMemberIssue[];
};

export type EpicGrouping = {
  features: EpicFeatureGroup[];
  others: EpicMemberIssue[];
};

/** An issue counts as done at `merged` or `done`. Abandoned issues are
 * excluded from the rollup entirely, so they neither block nor satisfy the
 * "ready to complete" nudge. */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set(["merged", "done"]);

export function isIssueTerminal(issue: Pick<EpicMemberIssue, "status">): boolean {
  return TERMINAL_STATUSES.has(issue.status);
}

const EMPTY_TYPE_COUNTS = (): Record<EpicIssueType, number> => ({
  feature: 0,
  bug: 0,
  chore: 0,
  story: 0,
});

export function rollupEpicIssues(issues: EpicMemberIssue[]): EpicRollup {
  const counted = issues.filter((i) => !i.abandoned_at);
  const byType = EMPTY_TYPE_COUNTS();
  let done = 0;
  for (const issue of counted) {
    byType[issue.type] += 1;
    if (isIssueTerminal(issue)) done += 1;
  }
  const total = counted.length;
  return {
    total,
    done,
    abandoned: issues.length - counted.length,
    byType,
    percent: total === 0 ? 0 : Math.round((done / total) * 100),
    // An empty epic is never "ready to complete" — there is nothing to finish.
    readyToComplete: total > 0 && done === total,
  };
}

export function formatEpicProgress(rollup: EpicRollup): string {
  if (rollup.total === 0) return "No work items assigned";
  return `${rollup.done} of ${rollup.total} ${
    rollup.total === 1 ? "work item" : "work items"
  } done`;
}

const TYPE_LABELS: Record<EpicIssueType, [string, string]> = {
  feature: ["feature", "features"],
  bug: ["bug", "bugs"],
  chore: ["chore", "chores"],
  story: ["story", "stories"],
};

const TYPE_ORDER: EpicIssueType[] = ["feature", "story", "bug", "chore"];

export function formatEpicTypeCounts(rollup: EpicRollup): string {
  const parts = TYPE_ORDER.filter((t) => rollup.byType[t] > 0).map((t) => {
    const n = rollup.byType[t];
    return `${n} ${TYPE_LABELS[t][n === 1 ? 0 : 1]}`;
  });
  return parts.join(" · ");
}

/** Features carry their child stories nested underneath, then bugs and
 * chores. A story whose parent feature is not in this epic has no nest to
 * sit in, so it falls through to `others` rather than disappearing. */
export function groupEpicIssues(issues: EpicMemberIssue[]): EpicGrouping {
  const features = issues
    .filter((i) => i.type === "feature")
    .map((feature) => ({
      feature,
      children: issues.filter(
        (i) => i.type === "story" && i.parent_id === feature.id
      ),
    }));

  const nestedIds = new Set(
    features.flatMap((g) => g.children.map((c) => c.id))
  );

  const others = issues.filter(
    (i) => i.type !== "feature" && !nestedIds.has(i.id)
  );

  const otherOrder: EpicIssueType[] = ["bug", "chore", "story"];
  others.sort((a, b) => otherOrder.indexOf(a.type) - otherOrder.indexOf(b.type));

  return { features, others };
}
