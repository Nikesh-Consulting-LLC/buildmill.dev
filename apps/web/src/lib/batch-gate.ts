/** US-84.1: the unanimous batch gate a feature's header row may clear.
 *
 * us-24.1's rule is that a feature header in Waiting on you carries no
 * action — it must not compete with the story rows below it. us-25.2 carved
 * one exception: the whole plan gate, when every child sits at it. This
 * generalizes that exception without loosening it: the action exists only
 * when EVERY non-abandoned child sits at the same gate (the caller feeds
 * all of them, never just the rows one dashboard group happened to load),
 * and there is more than one — a batch of one is the row's own action
 * wearing a hat.
 */

export type BatchGateKind = "curate" | "plan" | "code" | "approve";

export type BatchGate = { kind: BatchGateKind; count: number };

/** Status → the batch mechanism that clears it. Only the gates the factory
 * already has a batch action for; anything else — mixed statuses, a set of
 * `in-review` diffs (N distinct decisions about code), work in flight —
 * stays on the per-story rows. */
const GATE_BY_STATUS: Record<string, BatchGateKind> = {
  draft: "curate", // curate_feature_stories (us-41.2)
  ready: "plan", // batch-dispatch, phase=plan (us-20.6 / us-41.1)
  planned: "code", // batch-dispatch, phase=code
  "plan-review": "approve", // plans/approve-all (us-20.6 / us-25.2)
};

export function deriveBatchGate(statuses: string[]): BatchGate | null {
  if (statuses.length < 2) return null;
  const first = statuses[0];
  if (!statuses.every((s) => s === first)) return null;
  const kind = GATE_BY_STATUS[first];
  return kind ? { kind, count: statuses.length } : null;
}

/** The button's words, in one place so the two dashboard variants (desktop
 * table and mobile cards) can never drift into saying different things. */
export function batchGateLabel(gate: BatchGate): string {
  const n = gate.count;
  const stories = n === 1 ? "story" : "stories";
  switch (gate.kind) {
    case "curate":
      return `Curate all ${n} ${stories}`;
    case "plan":
      return `Plan all ${n} ${stories}`;
    case "code":
      return `Code all ${n} ${stories}`;
    case "approve":
      return `Approve all ${n} plans`;
  }
}
