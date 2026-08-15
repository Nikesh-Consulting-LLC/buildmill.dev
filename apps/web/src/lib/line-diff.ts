/** US-100.5: a unified line diff between two texts, in the `diff --git`
 * shape `DiffView` renders — so a proposed instruction file can be shown
 * against the current one without shipping a diff library to the browser.
 *
 * Plain LCS over lines. Instruction files are hundreds of lines at most, so
 * the quadratic table is cheap; a pathological pair (over the cap) falls
 * back to "everything removed, everything added", which is still a truthful
 * diff, just not a minimal one.
 */

const CELL_CAP = 4_000_000;
const CONTEXT = 3;

type Op = { kind: " " | "-" | "+"; text: string };

function splitLines(text: string): string[] {
  if (text === "") return [];
  const lines = text.split("\n");
  // A trailing newline is not an extra empty line.
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function lcsOps(a: string[], b: string[]): Op[] {
  if (a.length * b.length > CELL_CAP) {
    return [
      ...a.map((text) => ({ kind: "-" as const, text })),
      ...b.map((text) => ({ kind: "+" as const, text })),
    ];
  }
  const n = a.length;
  const m = b.length;
  // dp[i][j] = LCS length of a[i:], b[j:]
  const dp: Uint32Array[] = [];
  for (let i = 0; i <= n; i++) dp.push(new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      dp[i][j] =
        a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      ops.push({ kind: " ", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      ops.push({ kind: "-", text: a[i] });
      i++;
    } else {
      ops.push({ kind: "+", text: b[j] });
      j++;
    }
  }
  while (i < n) ops.push({ kind: "-", text: a[i++] });
  while (j < m) ops.push({ kind: "+", text: b[j++] });
  return ops;
}

/** Unified diff of `before` → `after` for one file, or "" when identical.
 * The output starts with `diff --git a/<path> b/<path>` so `DiffView` splits
 * and titles it like any other diff. */
export function unifiedDiff(path: string, before: string, after: string): string {
  if (before === after) return "";
  const ops = lcsOps(splitLines(before), splitLines(after));

  // Group changed ops into hunks with CONTEXT lines either side.
  const changed = ops.map((o) => o.kind !== " ");
  const hunks: { start: number; end: number }[] = [];
  let k = 0;
  while (k < ops.length) {
    if (!changed[k]) {
      k++;
      continue;
    }
    const start = Math.max(0, k - CONTEXT);
    let end = k;
    // extend while the gap to the next change is within 2*CONTEXT
    let cursor = k;
    while (cursor < ops.length) {
      if (changed[cursor]) {
        end = cursor;
        cursor++;
        continue;
      }
      let gap = 0;
      while (cursor + gap < ops.length && !changed[cursor + gap]) gap++;
      if (cursor + gap >= ops.length || gap > 2 * CONTEXT) break;
      cursor += gap;
    }
    end = Math.min(ops.length - 1, end + CONTEXT);
    // Merge with previous hunk if overlapping.
    const prev = hunks[hunks.length - 1];
    if (prev && start <= prev.end + 1) {
      prev.end = end;
    } else {
      hunks.push({ start, end });
    }
    k = end + 1;
  }

  const out: string[] = [
    `diff --git a/${path} b/${path}`,
    `--- a/${path}`,
    `+++ b/${path}`,
  ];
  // Line numbers per side, walking ops.
  let aLine = 1;
  let bLine = 1;
  let opIndex = 0;
  for (const h of hunks) {
    // advance counters up to h.start
    while (opIndex < h.start) {
      const o = ops[opIndex++];
      if (o.kind !== "+") aLine++;
      if (o.kind !== "-") bLine++;
    }
    let aCount = 0;
    let bCount = 0;
    const body: string[] = [];
    for (let idx = h.start; idx <= h.end; idx++) {
      const o = ops[idx];
      body.push(o.kind + o.text);
      if (o.kind !== "+") aCount++;
      if (o.kind !== "-") bCount++;
    }
    out.push(`@@ -${aLine},${aCount} +${bLine},${bCount} @@`);
    out.push(...body);
    while (opIndex <= h.end) {
      const o = ops[opIndex++];
      if (o.kind !== "+") aLine++;
      if (o.kind !== "-") bLine++;
    }
  }
  return out.join("\n");
}

/** Counts for a summary line: lines added / removed. */
export function diffStats(before: string, after: string): { added: number; removed: number } {
  if (before === after) return { added: 0, removed: 0 };
  const ops = lcsOps(splitLines(before), splitLines(after));
  let added = 0;
  let removed = 0;
  for (const o of ops) {
    if (o.kind === "+") added++;
    else if (o.kind === "-") removed++;
  }
  return { added, removed };
}
