// US-8.1: the projects the hub selects across, and the epics used for
// grouping (Outline/Table) and the create pickers (US-8.2).
export type HubProject = { id: string; name: string; org_id: string };
export type HubEpic = {
  id: string;
  project_id: string;
  number: number;
  title: string;
  active: boolean;
  /** US-71.1: `open` | `completed` — closed epics collapse in the Outline
   * and leave the create pickers. */
  status: string;
};

export type ViewIssue = {
  id: string;
  title: string;
  status: string;
  type: string;
  updated_at: string;
  github_issue_number: number | null;
  github_issue_url: string | null;
  /** US-8.1: the owning project — the hub spans several at once. */
  project_id: string;
  /** US-8.3: a story's parent feature, for outline/table nesting. */
  parent_id: string | null;
  epic_id: string | null;
  /** Denormalized from join; may be absent on Realtime payloads. */
  epic_title: string | null;
  /** US-7.10: the epic number + item/sub sequence behind the display id. */
  epic_number: number | null;
  item_no: number | null;
  sub_no: number | null;
  /** US-7.1: advisory complexity estimate (null = not scored). */
  complexity: string | null;
  body?: string | null;
  acceptance_criteria?: unknown;
};

// US-8.3: Outline replaces List and becomes the default lens.
// US-71.2: Timeline retires with target dates (it was a calendar of them).
export type IssuesLayout = "outline" | "board" | "table";

export const ISSUES_LAYOUT_KEY = "sf-issues-view-mode";

export const ISSUES_LAYOUTS: { id: IssuesLayout; label: string }[] = [
  { id: "outline", label: "Outline" },
  { id: "board", label: "Board" },
  { id: "table", label: "Table" },
];

export function parseIssuesLayout(raw: string | null): IssuesLayout {
  if (raw === "outline" || raw === "table" || raw === "board") {
    return raw;
  }
  // Retired lenses (List, Timeline) fold into their replacement, Outline.
  return "outline";
}

// The 13 issue statuses in pipeline order — the Status filter list and the
// Table's status grouping/sort share this ordering.
export const ISSUE_STATUS_ORDER = [
  "draft",
  "prd-review",
  "ready",
  "planning",
  "plan-review",
  "planned",
  "queued",
  "running",
  "needs-fixes",
  "in-review",
  "merged",
  "failed",
  "done",
] as const;

// US-8.4: the Board lens collapses the 13 statuses into 5 phases. `failed`
// lives in Done but is flagged as a failure, not a silent member.
export type IssuePhase = "Define" | "Plan" | "Build" | "Review" | "Done";

export const ISSUE_PHASES: { id: IssuePhase; statuses: string[] }[] = [
  { id: "Define", statuses: ["draft", "prd-review", "ready"] },
  { id: "Plan", statuses: ["planning", "plan-review", "planned"] },
  { id: "Build", statuses: ["queued", "running", "needs-fixes"] },
  { id: "Review", statuses: ["in-review", "merged"] },
  { id: "Done", statuses: ["done", "failed"] },
];

const STATUS_TO_PHASE: Record<string, IssuePhase> = (() => {
  const map: Record<string, IssuePhase> = {};
  for (const phase of ISSUE_PHASES) {
    for (const s of phase.statuses) map[s] = phase.id;
  }
  return map;
})();

export function phaseForStatus(status: string): IssuePhase {
  return STATUS_TO_PHASE[status] ?? "Define";
}

export function formatIssueWhen(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** US-87.3: the list select carries no prose.
 *
 * `body` and `acceptance_criteria` were here so the CLIENT-side realtime
 * filter could re-run a search the server had already applied — and no list
 * view has ever rendered either one. Measured on prod (2026-08-12): 902 bytes
 * of body per item on average, 12 kB at the worst, which at 5,000 work items
 * is ~4.5 MB of markdown serialized into the RSC payload on every hub load.
 *
 * Dropping them costs the client filter nothing: a Realtime `postgres_changes`
 * payload carries the WHOLE row regardless of this select, and
 * `issueMatchesQuery` now matches on `search_text` — the generated column
 * (migration 036) that the server's own `applyIssueSearch` searches — so the
 * two sides run the same predicate rather than two approximations of it. */
export const VIEW_ISSUE_SELECT =
  "id, title, status, type, updated_at, github_issue_number, github_issue_url, project_id, parent_id, epic_id, item_no, sub_no, complexity, epics(title, number)";

/** US-87.3: one page of work items. The hub used to fetch every item in the
 * workspace with no limit; this bounds it, and `Load more` walks forward. */
export const HUB_PAGE_SIZE = 200;

export function mapIssueRow(row: Record<string, unknown>): ViewIssue {
  const epics = row.epics as
    | { title?: string; number?: number }
    | { title?: string; number?: number }[]
    | null;
  let epicTitle: string | null = null;
  let epicNumber: number | null = null;
  if (Array.isArray(epics)) {
    epicTitle = epics[0]?.title ?? null;
    epicNumber = epics[0]?.number ?? null;
  } else if (epics && typeof epics === "object") {
    epicTitle = epics.title ?? null;
    epicNumber = epics.number ?? null;
  }

  return {
    id: row.id as string,
    title: row.title as string,
    status: row.status as string,
    type: row.type as string,
    updated_at: row.updated_at as string,
    github_issue_number: (row.github_issue_number as number | null) ?? null,
    github_issue_url: (row.github_issue_url as string | null) ?? null,
    project_id: row.project_id as string,
    parent_id: (row.parent_id as string | null) ?? null,
    epic_id: (row.epic_id as string | null) ?? null,
    epic_title: epicTitle,
    epic_number: epicNumber,
    item_no: (row.item_no as number | null) ?? null,
    sub_no: (row.sub_no as number | null) ?? null,
    complexity: (row.complexity as string | null) ?? null,
    body: (row.body as string | null) ?? null,
    acceptance_criteria: row.acceptance_criteria,
  };
}
