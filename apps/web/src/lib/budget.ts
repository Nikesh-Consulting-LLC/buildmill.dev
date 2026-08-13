/**
 * US-37.1: one place that decides what a project's budget means.
 *
 * Three surfaces read this — the project's Budget card, the projects list, and
 * the Things to Do banner — and they must agree on where "close" starts and
 * what counts as exhausted. Three copies of `spent >= budget` is how one of
 * them ends up saying a project is fine while dispatch refuses it.
 *
 * The database is the authority: migration 164's trigger refuses a run on
 * `spent >= budget_usd`, and `exhausted` below is the same comparison. If they
 * ever diverge, the trigger wins and the UI is lying.
 */

/** Where "close to the limit" starts. The projects list is the early warning;
 *  the dashboard banner deliberately fires only at exhaustion. */
export const BUDGET_NEAR_FRACTION = 0.8;

export type BudgetSource = {
  budget_enabled: boolean | null;
  budget_usd: number | null;
  budget_started_at?: string | null;
};

export type BudgetState = {
  /** Dollars spent since `budget_started_at`. Always a number — a project that
   *  has spent nothing has spent $0.00, which is not the same as unmeasured. */
  spent: number;
  /** Calls on models with no rate. Real money no budget can see. */
  unmeasured: number;
  /** The budget, or null when none is set. */
  budget: number | null;
  /** 0–1+ against the budget; null with no budget, because a percentage of
   *  nothing is not a number. Not clamped: 140% should read as 140%. */
  fraction: number | null;
  /** Dispatch will be refused. Matches migration 164's trigger exactly. */
  exhausted: boolean;
  /** At or past BUDGET_NEAR_FRACTION but not yet exhausted. */
  near: boolean;
};

export function budgetState(
  project: BudgetSource,
  spend: { spent: number; unmeasured: number } | undefined
): BudgetState {
  const spent = spend?.spent ?? 0;
  const unmeasured = spend?.unmeasured ?? 0;
  const on = !!project.budget_enabled && project.budget_usd != null;
  const budget = on ? Number(project.budget_usd) : null;

  if (budget == null || budget <= 0) {
    // A budget of zero is not a budget that stops everything — it is a budget
    // nobody has set yet. The trigger treats a null the same way.
    return { spent, unmeasured, budget: null, fraction: null, exhausted: false, near: false };
  }

  const fraction = spent / budget;
  const exhausted = spent >= budget;
  return {
    spent,
    unmeasured,
    budget,
    fraction,
    exhausted,
    near: !exhausted && fraction >= BUDGET_NEAR_FRACTION,
  };
}

/** Dollars, at the precision the number deserves. Mirrors the Spend page:
 *  sub-dollar figures lose their meaning at two decimals. */
export function money(n: number): string {
  return `$${n.toFixed(n !== 0 && Math.abs(n) < 1 ? 4 : 2)}`;
}

export function percent(fraction: number): string {
  // Below 1% but non-zero, "0%" reads as "nothing spent". One decimal keeps it
  // honest without turning every card into a precision instrument.
  if (fraction > 0 && fraction < 0.01) return "<1%";
  return `${Math.round(fraction * 100)}%`;
}

/** The three states, as words. Colour carries the same information as the
 *  text, never instead of it — the cards must work without it. */
export function budgetLabel(b: BudgetState): string | null {
  if (b.budget == null) return null;
  if (b.exhausted) return "Over budget";
  if (b.near) return "Near budget";
  return "Within budget";
}
