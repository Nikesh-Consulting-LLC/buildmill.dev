/** US-14.8: turning the factory's own record of a run into something a
 * manager can read.
 *
 * The agent does not have to cooperate for any of this. Every one of these
 * is a call the API served on the worker's behalf; migration 115 keeps the
 * tool name that migration 109's lease intercept was already throwing
 * away. That matters because narration cannot be relied on: told to narrate
 * at boundaries, a well-behaved agent produced exactly one note in a
 * twelve-minute run.
 *
 * Phrases are in the manager's language, not the tool's. "read_repo_file"
 * is an implementation detail; "reading the code" is what happened. */

export const ACTIVITY_PHRASES: Record<string, string> = {
  claim_work: "picked up the work",
  get_work_context: "reading the brief",
  get_context_detail: "going back to the requirements",
  get_repo_tree: "looking through the repository",
  read_repo_file: "reading the code",
  get_workspace: "pulled the code down",
  validate_submission: "checking its work before handing back",
  report_progress: "reported progress",
  request_clarification: "waiting on your answer",
  deployment_check: "checking the deployment",
};

export function activityPhrase(tool: string): string {
  return ACTIVITY_PHRASES[tool] ?? "working";
}

/** How long a stage can reasonably stay quiet before silence is worth
 * flagging. A code run legitimately spends many minutes writing files
 * between calls — us-13.6's blanket 20 minutes reads a healthy long write
 * as a stall — so the window follows what the agent was last seen doing.
 * Wrong in the generous direction on purpose: a false "stalled" teaches
 * managers to ignore the signal, which is worse than a late one. */
const QUIET_BUDGET_MINUTES: Record<string, number> = {
  claim_work: 25,
  get_work_context: 20,
  get_context_detail: 20,
  get_repo_tree: 15,
  read_repo_file: 15,
  get_workspace: 30, // downloads, then writes for a long time
  validate_submission: 15,
  report_progress: 25,
  request_clarification: Number.POSITIVE_INFINITY, // blocked on the manager
  deployment_check: 20,
};

export function quietBudgetMinutes(tool: string | null): number {
  if (!tool) return 20;
  return QUIET_BUDGET_MINUTES[tool] ?? 20;
}

export type RunActivityState = {
  /** What the agent was last observed doing, already phrased. */
  doing: string;
  /** Minutes since the factory last saw the worker do anything. */
  silentMinutes: number;
  /** True only when silence has outrun what this stage should need. */
  overdue: boolean;
  /** Set when the run is legitimately waiting on the manager. */
  blockedOnYou: boolean;
};

export function deriveActivity(
  lastTool: string | null,
  lastAt: string | null,
  now: number
): RunActivityState | null {
  if (!lastTool || !lastAt) return null;
  const silentMinutes = Math.max(
    0,
    Math.floor((now - new Date(lastAt).getTime()) / 60_000)
  );
  const budget = quietBudgetMinutes(lastTool);
  return {
    doing: activityPhrase(lastTool),
    silentMinutes,
    overdue: silentMinutes > budget,
    blockedOnYou: lastTool === "request_clarification",
  };
}
