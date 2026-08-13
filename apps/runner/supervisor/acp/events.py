"""ACP `session/update` → the progress lines this runner already speaks (US-78.2).

The other modules scrape a CLI's stdout for narration, and that format has
changed under us twice in one CLI generation. ACP emits these as a protocol
obligation with typed variants, so this file is a mapping rather than a parser.

Shapes are from the ACP schema (agentclientprotocol.com/protocol/schema): the
notification is `{"sessionId": ..., "update": {"sessionUpdate": "<variant>", ...}}`
— camelCase keys, a snake_case discriminator.

    {"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"..."}}
    {"sessionUpdate":"agent_thought_chunk","content":{"type":"text","text":"..."}}
    {"sessionUpdate":"tool_call","toolCallId":"...","title":"...","kind":"read",
                                 "status":"pending","rawInput":{...}}
    {"sessionUpdate":"tool_call_update","toolCallId":"...","status":"completed"}
    {"sessionUpdate":"plan","entries":[{"content":"...","status":"pending"}]}

Anything else is dropped. The spec says the variant list is open, so an unknown
`sessionUpdate` must be ignorable rather than fatal — and `user_message_chunk`
is dropped on purpose: it is our own prompt echoed back, and replaying it into
the trace would show the manager their own words as if the agent had said them.
"""

from __future__ import annotations

from typing import Any

from ..progress import MAX_LINE, _clip

# The variants worth a line, and the `run_trace` kind each becomes. The kinds
# are migration 118's `run_trace_kind_check` set — using one outside it is what
# killed the control socket on 2026-07-27.
UPDATE_KINDS = {
    "agent_message_chunk": "output",
    "agent_thought_chunk": "step",
    "tool_call": "tool",
    "tool_call_update": "tool",
    "plan": "decision",
}

# Chunked variants arrive token by token. One trace row per token would drown
# the table and the realtime channel, so these are coalesced (see Coalescer).
CHUNKED = ("agent_message_chunk", "agent_thought_chunk")


# US-88.1: a streamed row is not a progress line, and `MAX_LINE`'s 400
# characters is the wrong ceiling for one. The console renders an agent's
# answer as markdown, and markdown is a *block* format: a table cut at 400
# characters is no longer a table, on either side of the cut. This is the size
# of one such block — big enough that a normal answer arrives whole, still
# bounded so a runaway agent cannot write a row without end. Nothing is lost at
# the boundary: the Coalescer carries the remainder into the next row rather
# than clipping it away, which is why streamed text needs no clipper of its own.
# (`progress._clip` is not one either — it collapses every run of whitespace,
# which is right for a one-line progress note scraped off stdout and fatal for
# a slice of an agent's answer.)
MAX_BLOCK = 4000


def _content_text(content: Any) -> str:
    """The text out of a ContentBlock, or "" for a block that carries none."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if content.get("type") == "text":
            return str(content.get("text") or "")
        return ""
    if isinstance(content, list):
        return " ".join(t for t in (_content_text(c) for c in content) if t)
    return ""


def _tool_line(update: dict) -> str:
    """A tool call as one line: what it is, and the one argument that says what
    it is doing. Never the whole input — a write's input is a whole file."""
    title = str(update.get("title") or update.get("kind") or "tool")
    raw = update.get("rawInput")
    if isinstance(raw, dict):
        for key in ("command", "file_path", "path", "pattern", "query", "url"):
            if raw.get(key):
                return f"{title}: {_clip(str(raw[key]), 200)}"
    status = update.get("status")
    if status and status != "pending":
        return f"{title} — {status}"
    return title


def _plan_line(update: dict) -> str:
    entries = update.get("entries")
    if not isinstance(entries, list) or not entries:
        return "plan updated"
    done = sum(1 for e in entries if isinstance(e, dict) and e.get("status") == "completed")
    head = next(
        (
            str(e.get("content"))
            for e in entries
            if isinstance(e, dict) and e.get("status") == "in_progress"
        ),
        "",
    )
    label = f"plan {done}/{len(entries)}"
    return f"{label} — {_clip(head, 200)}" if head else label


def describe_update(params: dict) -> tuple[str, str] | None:
    """(kind, line) for one `session/update`, or None to drop it."""
    if not isinstance(params, dict):
        return None
    update = params.get("update")
    if not isinstance(update, dict):
        return None
    variant = update.get("sessionUpdate")
    kind = UPDATE_KINDS.get(str(variant))
    if kind is None:
        return None
    if variant in CHUNKED:
        # US-88.1: deliberately NOT stripped and not whitespace-collapsed. A
        # chunk is a slice of a string the agent is still writing, and the
        # whitespace at its edges is the sentence — stripping each slice and
        # rejoining with a space is what put a space in front of every full
        # stop. The Coalescer below trims once, on the finished line.
        text = _content_text(update.get("content"))
        return (kind, text) if text else None
    if variant == "plan":
        return kind, _clip(_plan_line(update))
    return kind, _clip(_tool_line(update))


class Coalescer:
    """Joins consecutive chunks of the same kind into one readable block.

    Flushes when the kind changes, when the buffer reaches a full block, or when
    the caller says time is up — so a long answer becomes a handful of rows
    instead of a row per token, and a slow one still appears while it is being
    written rather than only at the end.

    US-88.1: it cuts at a line ending wherever one is available. Both flushes
    used to cut wherever the buffer happened to reach, which lands mid-word
    during prose and mid-table-row during a table — and the console renders
    these rows as markdown, where half a line is not half a table but no table
    at all. Cutting on a newline costs nothing and keeps every block whole.
    """

    def __init__(self, max_chars: int = MAX_BLOCK, max_seconds: float = 2.0):
        self.max_chars = max_chars
        self.max_seconds = max_seconds
        self._kind: str | None = None
        self._buf: list[str] = []
        self._opened_at: float | None = None

    def feed(self, kind: str, line: str, now: float) -> list[tuple[str, str]]:
        """Take one described event; return the lines ready to emit."""
        out: list[tuple[str, str]] = []
        if kind not in ("output", "step"):
            # Tool calls and plans are already whole events, and holding them
            # behind a text buffer would reorder them against the text.
            out.extend(self._flush())
            out.append((kind, line))
            return out
        if self._kind is not None and kind != self._kind:
            out.extend(self._flush())
        if self._kind is None or not self._buf:
            self._opened_at = now
        self._kind = kind
        self._buf.append(line)
        # A loop, not an `if`: an agent that does not stream sends its whole
        # answer as one chunk, and that chunk can be several blocks long. Each
        # pass consumes at least one character, so this always terminates.
        while self._length() >= self.max_chars:
            out.extend(self._emit(self._cut(self.max_chars), now))
        if self._expired(now):
            out.extend(self._emit(self._cut(None), now))
        return out

    def tick(self, now: float) -> list[tuple[str, str]]:
        """Flush a buffer that has been open too long, with nothing new to add."""
        return self._emit(self._cut(None), now) if self._expired(now) else []

    def drain(self) -> list[tuple[str, str]]:
        """Everything still buffered — call at the end of a turn."""
        return self._flush()

    def _length(self) -> int:
        return sum(len(p) for p in self._buf)

    def _expired(self, now: float) -> bool:
        return (
            bool(self._buf)
            and self._opened_at is not None
            and (now - self._opened_at) >= self.max_seconds
        )

    def _cut(self, limit: int | None) -> int:
        """Where to cut the buffer: the last line ending at or before `limit`,
        or `limit` itself when the block has no line ending to cut on.

        `limit=None` means "as much as is there" — the time-based flush, which
        keeps the unfinished last line buffered so the row it lands in is whole.
        """
        text = "".join(self._buf)
        head = text if limit is None else text[:limit]
        end = head.rfind("\n")
        if end >= 0:
            return end + 1
        return len(text) if limit is None else limit

    def _emit(self, upto: int, now: float) -> list[tuple[str, str]]:
        """Emit the first `upto` characters as one row; keep the rest buffered."""
        if not self._buf or self._kind is None:
            self._buf, self._opened_at = [], None
            return []
        # US-88.1: joined with nothing. The buffer holds consecutive slices of
        # one string, so any separator is text the agent did not write. Trimmed
        # once here, because a row should not start with the blank line that
        # ended the previous one — and a buffer that was only whitespace has
        # nothing to say.
        text = "".join(self._buf)
        head, tail = text[:upto], text[upto:]
        kind = self._kind
        if tail:
            # The clock restarts with the remainder: it is a new row now, and
            # inheriting the old deadline would flush it a line at a time.
            self._buf, self._opened_at = [tail], now
        else:
            self._buf, self._kind, self._opened_at = [], None, None
        line = head.strip()
        return [(kind, line)] if line else []

    def _flush(self) -> list[tuple[str, str]]:
        """Everything buffered, in one row, regardless of where the lines fall."""
        return self._emit(self._length(), 0.0)
