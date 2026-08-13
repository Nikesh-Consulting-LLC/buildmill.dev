/**
 * US-88.1: what the console shows as one row, and what it folds away.
 *
 * A turn is mostly working-out. The agent thinks, reads three files, waits,
 * thinks again — and then says something. Rendering all of that at equal weight
 * buries the sentence the manager came to read under the search that produced
 * it, and by the time the answer arrives the answer is off the top of the
 * screen.
 *
 * So consecutive working rows collapse into one, titled by the most recent of
 * them, and open on a click. Pure functions, in their own file, because they
 * are the part of this worth a test.
 */

export type Line = { kind: string; content: string };

/**
 * Rows that are the agent working rather than the agent talking.
 *
 * `decision` is in this set, which is not obvious: it carries plan counters and
 * the runner's permission notices (`permission selected for
 * factory__list_factory_queue (allow_once)`), and a turn that calls three tools
 * emits one of those per call. They are a record of the working-out, not a
 * message to the manager — and leaving them out left a fold broken into
 * fragments by exactly the rows that made it worth folding.
 *
 * What is never folded is what is *addressed to the manager*: the agent's own
 * speech (`output`), an error, and the manager's own line.
 */
export const WORKING = new Set(["step", "tool", "progress", "decision"]);

export type Block =
  | { type: "line"; key: number; line: Line }
  | { type: "group"; key: number; lines: Line[] };

/**
 * Fold every run of consecutive working rows into a group — including a run of
 * one.
 *
 * A lone thought is not a small clog: it is a paragraph, and flat it wraps to
 * three or four lines between two sentences of the answer. Folded it is one
 * line that still says what it was, and it opens like every other fold. The
 * rule being unconditional also means the transcript does not change shape as a
 * turn streams — a row does not become a fold because a second row landed
 * behind it.
 *
 * `key` is the index of the block's first line, which is stable while the
 * transcript only ever grows — so a group that gains a line keeps its identity,
 * and with it whether the manager opened it.
 */
export function toBlocks(lines: Line[]): Block[] {
  const out: Block[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (!WORKING.has(lines[i].kind)) {
      out.push({ type: "line", key: i, line: lines[i] });
      continue;
    }
    let end = i;
    while (end + 1 < lines.length && WORKING.has(lines[end + 1].kind)) end++;
    out.push({ type: "group", key: i, lines: lines.slice(i, end + 1) });
    i = end;
  }
  return out;
}

/**
 * The one line a collapsed group shows: the opening line of its most recent
 * row. The newest row is what the agent is doing *now*, which is the only part
 * of a fold worth a title — and its first line rather than all of it, because a
 * thought is a paragraph and this is one row tall.
 */
export function groupTitle(lines: Line[]): string {
  for (let i = lines.length - 1; i >= 0; i--) {
    const first = lines[i].content
      .split("\n")
      .map((l) => l.trim())
      .find(Boolean);
    if (first) return first;
  }
  return "working";
}
