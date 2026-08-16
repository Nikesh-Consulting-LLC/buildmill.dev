import type { TodoActionMode } from "./data";

/** us-106.1: what a `draft` work item's one click does on the Workbench.
 *
 * Not a guess: `dispatch_kind_for` (migration 255) already decides what a
 * plain dispatch of a draft means, and this names it — `plan` for a story,
 * `plan` for a bug (an RCA, us-96.5), `code` for a chore (no planning phase,
 * us-96.1).
 *
 * A feature is the one type it refuses outright: it infers `plan`, and the
 * us-11.2 guard answers "a feature is not planned directly — approve its PRD
 * and break it into stories". So a feature drafts its PRD instead, the same
 * call the item page's Draft PRD button makes.
 *
 * Its own module so it can be tested without pulling `data.ts` (and Supabase)
 * into the test runner. */
export function triageAction(type: string): {
  reason: string;
  action: string;
  mode: TodoActionMode;
  /** us-107.3: the run kind this click creates, so the button can wear the
   *  capability icon of the agent it needs rather than a generic rocket. */
  kind: string;
} {
  if (type === "feature")
    return {
      reason: "Draft — no PRD yet; draft one to define the requirement",
      action: "Draft PRD",
      mode: "draft-prd",
      kind: "prd",
    };
  if (type === "bug")
    return {
      reason: "Draft — dispatch the root cause analysis",
      action: "Dispatch RCA",
      mode: "dispatch",
      kind: "plan",
    };
  if (type === "chore")
    return {
      reason: "Draft — no planning phase; dispatch builds it",
      action: "Dispatch build",
      mode: "dispatch",
      kind: "code",
    };
  return {
    reason: "Draft — send it for planning",
    action: "Dispatch planning",
    mode: "dispatch",
    kind: "plan",
  };
}
