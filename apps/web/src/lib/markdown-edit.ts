/** US-5.15: pure selection/line manipulation helpers behind the shared
 * MarkdownEditor's toolbar and keyboard shortcuts. Every function takes the
 * current value + selection and returns the next value + selection — no DOM
 * access, so they're trivially testable and reusable. */

export type EditResult = {
  value: string;
  selectionStart: number;
  selectionEnd: number;
};

/** Wrap the selection in `prefix`/`suffix` (bold, italic, inline code…),
 * or unwrap if it's already wrapped. An empty selection inserts the
 * placeholder text, selected, so typing replaces it. */
export function wrapSelection(
  value: string,
  start: number,
  end: number,
  prefix: string,
  suffix: string = prefix,
  placeholder = "text"
): EditResult {
  const selected = value.slice(start, end);

  const before = value.slice(Math.max(0, start - prefix.length), start);
  const after = value.slice(end, end + suffix.length);
  if (before === prefix && after === suffix) {
    // already wrapped immediately outside the selection — unwrap
    return {
      value:
        value.slice(0, start - prefix.length) +
        selected +
        value.slice(end + suffix.length),
      selectionStart: start - prefix.length,
      selectionEnd: end - prefix.length,
    };
  }
  if (
    selected.length >= prefix.length + suffix.length &&
    selected.startsWith(prefix) &&
    selected.endsWith(suffix)
  ) {
    // the wrap markers are inside the selection — unwrap
    const inner = selected.slice(prefix.length, selected.length - suffix.length);
    return {
      value: value.slice(0, start) + inner + value.slice(end),
      selectionStart: start,
      selectionEnd: start + inner.length,
    };
  }

  const content = selected || placeholder;
  return {
    value: value.slice(0, start) + prefix + content + suffix + value.slice(end),
    selectionStart: start + prefix.length,
    selectionEnd: start + prefix.length + content.length,
  };
}

function lineRange(value: string, start: number, end: number) {
  const lineStart = value.lastIndexOf("\n", start - 1) + 1;
  let lineEnd = value.indexOf("\n", end);
  if (lineEnd === -1) lineEnd = value.length;
  return { lineStart, lineEnd };
}

/** Toggle a fixed prefix ("### ", "> ", "- ", "- [ ] ") on every line the
 * selection touches: if all lines already have it, remove it; otherwise
 * add it (skipping blank lines when adding across a multi-line block). */
export function toggleLinePrefix(
  value: string,
  start: number,
  end: number,
  prefix: string
): EditResult {
  const { lineStart, lineEnd } = lineRange(value, start, end);
  const lines = value.slice(lineStart, lineEnd).split("\n");
  const relevant = lines.filter((l) => l.trim().length > 0);
  const allPrefixed =
    relevant.length > 0 && relevant.every((l) => l.startsWith(prefix));

  const next = lines.map((l) => {
    if (l.trim().length === 0 && lines.length > 1) return l;
    return allPrefixed
      ? l.startsWith(prefix)
        ? l.slice(prefix.length)
        : l
      : prefix + l;
  });
  const block = next.join("\n");
  return {
    value: value.slice(0, lineStart) + block + value.slice(lineEnd),
    selectionStart: lineStart,
    selectionEnd: lineStart + block.length,
  };
}

/** Toggle "1. " / "2. " … numbering on the selected lines. */
export function toggleNumberedList(
  value: string,
  start: number,
  end: number
): EditResult {
  const { lineStart, lineEnd } = lineRange(value, start, end);
  const lines = value.slice(lineStart, lineEnd).split("\n");
  const relevant = lines.filter((l) => l.trim().length > 0);
  const allNumbered =
    relevant.length > 0 && relevant.every((l) => /^\d+\.\s/.test(l));

  let n = 0;
  const next = lines.map((l) => {
    if (l.trim().length === 0 && lines.length > 1) return l;
    if (allNumbered) return l.replace(/^\d+\.\s/, "");
    n += 1;
    return `${n}. ${l}`;
  });
  const block = next.join("\n");
  return {
    value: value.slice(0, lineStart) + block + value.slice(lineEnd),
    selectionStart: lineStart,
    selectionEnd: lineStart + block.length,
  };
}

/** [selection](url) with "url" selected — or [text](url) with "text"
 * selected when nothing is highlighted. */
export function insertLink(
  value: string,
  start: number,
  end: number
): EditResult {
  const selected = value.slice(start, end);
  if (selected) {
    const insert = `[${selected}](url)`;
    return {
      value: value.slice(0, start) + insert + value.slice(end),
      selectionStart: start + selected.length + 3,
      selectionEnd: start + selected.length + 6,
    };
  }
  const insert = "[text](url)";
  return {
    value: value.slice(0, start) + insert + value.slice(end),
    selectionStart: start + 1,
    selectionEnd: start + 5,
  };
}

/** Fenced code block around the selection, on its own lines. */
export function insertCodeBlock(
  value: string,
  start: number,
  end: number
): EditResult {
  const selected = value.slice(start, end) || "code";
  const needsLeadingNewline = start > 0 && value[start - 1] !== "\n";
  const lead = needsLeadingNewline ? "\n" : "";
  const insert = `${lead}\`\`\`\n${selected}\n\`\`\`\n`;
  const contentStart = start + lead.length + 4;
  return {
    value: value.slice(0, start) + insert + value.slice(end),
    selectionStart: contentStart,
    selectionEnd: contentStart + selected.length,
  };
}

/** A 2×2 GFM table skeleton at the cursor, cursor left in the first cell. */
export function insertTable(
  value: string,
  start: number,
  end: number
): EditResult {
  const needsLeadingNewline = start > 0 && value[start - 1] !== "\n";
  const lead = needsLeadingNewline ? "\n\n" : "";
  const table = `${lead}| Column | Column |\n| --- | --- |\n|  |  |\n`;
  const cursor = start + lead.length + 2;
  return {
    value: value.slice(0, start) + table + value.slice(end),
    selectionStart: cursor,
    selectionEnd: cursor + 6,
  };
}

/** Enter inside a list continues the marker (numbered lists increment);
 * Enter on an empty item clears the marker. Returns null when the cursor
 * isn't on a list line — the caller lets the default newline happen. */
export function continueListOnEnter(
  value: string,
  cursor: number
): EditResult | null {
  const lineStart = value.lastIndexOf("\n", cursor - 1) + 1;
  const line = value.slice(lineStart, cursor);
  const match = /^(\s*)(?:([-*+])( \[[ xX]\])?|(\d+)\.)\s(.*)$/.exec(line);
  if (!match) return null;

  const [, indent, bullet, task, num, content] = match;
  if (!content.trim()) {
    // empty item: clear the marker instead of continuing the list
    return {
      value: value.slice(0, lineStart) + value.slice(cursor),
      selectionStart: lineStart,
      selectionEnd: lineStart,
    };
  }
  const marker = bullet
    ? `${bullet}${task ? " [ ]" : ""} `
    : `${Number(num) + 1}. `;
  const insert = `\n${indent}${marker}`;
  return {
    value: value.slice(0, cursor) + insert + value.slice(cursor),
    selectionStart: cursor + insert.length,
    selectionEnd: cursor + insert.length,
  };
}
