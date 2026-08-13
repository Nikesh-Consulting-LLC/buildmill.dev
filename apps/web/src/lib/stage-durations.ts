// US-62.10: a run's time, broken into named stages — checkout, invoke_cli,
// collect_output, commit_and_push — each timed by the supervisor's own
// control flow (`cli_base.py`'s `_stage`), never dependent on the CLI agent
// narrating anything. Encoded as `stage:<name> <ms>ms` on an ordinary
// `kind='step'` run_trace row rather than a new column — a run predating
// this change simply has no such lines.

const STAGE_LINE = /^stage:(\w+)\s+(\d+)ms$/;

export type StageDuration = {
  stage: string;
  totalMs: number;
  occurrences: number;
};

/** A repair retry can run the same stage twice; both are kept (summed,
 * with an occurrence count) rather than one silently overwriting the other. */
export function parseStageDurations(
  traceRows: { kind: string; content: string | null }[],
): StageDuration[] {
  const byStage = new Map<string, StageDuration>();
  for (const row of traceRows) {
    if (row.kind !== "step" || !row.content) continue;
    const m = STAGE_LINE.exec(row.content.trim());
    if (!m) continue;
    const stage = m[1];
    const ms = Number(m[2]);
    const existing = byStage.get(stage);
    if (existing) {
      existing.totalMs += ms;
      existing.occurrences += 1;
    } else {
      byStage.set(stage, { stage, totalMs: ms, occurrences: 1 });
    }
  }
  return [...byStage.values()];
}

/** The same trace rows, with stage-bookkeeping lines removed — they're
 * supervisor-internal timing data for the dedicated breakdown section, not
 * narration a manager reads line-by-line in the run's timeline. */
export function withoutStageLines<T extends { kind: string; content: string | null }>(
  traceRows: T[],
): T[] {
  return traceRows.filter(
    (row) => !(row.kind === "step" && row.content && STAGE_LINE.test(row.content.trim())),
  );
}
