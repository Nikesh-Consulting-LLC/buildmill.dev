// US-74.5: whether a Things-to-Do row's action is held by the build rules.
//
// The reason itself always comes from the database (org_issue_dispatch_blocks,
// which reads the very functions dispatch_issue and the claim gate use). What
// lives here is only the question of WHICH ACTIONS a block applies to — a UI
// decision, not a build rule, and the one piece worth pinning with a test.

/** The shape this rule needs off a to-do row. Structural on purpose: the row
 * type lives in the dashboard's data module, which pulls in Supabase. */
export type BlockableRow = {
  mode: string;
  blocked?: { reason: string; hard: boolean } | null;
};

/** The reason this row's action can't run yet, or null if it can.
 *
 * Only dispatch-shaped actions are ever held. An approval is never
 * dependency-blocked: approving a plan is a decision about that plan, and
 * withholding it because the CODE cannot start yet would strand the pipeline
 * at the one gate the manager was able to clear. When the follow-on dispatch
 * is held, the approval still lands and the row returns wearing an hourglass. */
export function heldReason(row: BlockableRow): string | null {
  if (row.mode !== "dispatch" && row.mode !== "redispatch") return null;
  return row.blocked?.reason ?? null;
}
