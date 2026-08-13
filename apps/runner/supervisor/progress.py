"""US-39.1: turning Claude's stream-json events into lines a human can watch.

Two jobs, and they must not be confused:

  * `describe()` maps ONE event to a short progress line, or None to drop it.
  * `StreamCollector` reassembles the FINAL ANSWER, because switching the CLI to
    `--output-format stream-json` replaces stdout with NDJSON and every module
    parses that stdout — `parse_stories(res.stdout)`, the PRD, the plan. If the
    reassembly is wrong, every run in the factory breaks. It is the riskiest
    part of this story and the reason for the fallbacks below.

Shapes verified against Claude Code 2.1.215 by capturing a real run, not taken
from documentation:

    {"type":"system","subtype":"init","model":"...","tools":[...],"mcp_servers":[...]}
    {"type":"assistant","message":{"content":[{"type":"text","text":"OK"}], ...}}
    {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash", ...}]}}
    {"type":"system","subtype":"post_turn_summary","status_detail":"replied with OK"}
    {"type":"rate_limit_event","rate_limit_info":{"status":"allowed", ...}}
    {"type":"result","subtype":"success","result":"OK","num_turns":1,"duration_ms":2961}

Everything else is dropped. That is deliberate rather than lazy: the same
capture showed `system/hook_response` events carrying ~10KB of embedded prompt
text apiece. Relaying those would flood the trace with content nobody asked to
see, and that content is untrusted text which has no business being replayed
into a UI as if the agent had said it.

US-59.1: `session_id` below is read off `init`/`result` on the strength of
Anthropic's own SDK/CLI documentation (both carry it, per the Claude Agent SDK
reference), NOT re-verified against a captured 2.1.215 run the way the shapes
above were — this project's own standard for "verified", stated honestly
because the gap matters: if the field is ever absent or renamed, resume simply
never engages (`claude_session_id` stays null) rather than raising, so a
change here degrades resume silently instead of breaking a run.
"""

from __future__ import annotations

import json
from typing import Any

# The trace `kind` values migration 118's `run_trace_kind_check` permits. Using
# one outside this set is what killed the control socket on 2026-07-27
# (us-36.1), so the mapping below only ever produces these.
KINDS = ("step", "tool", "decision", "output", "progress", "error")

# One progress line should be readable, not a transcript.
MAX_LINE = 400


def _clip(text: str, limit: int = MAX_LINE) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _tool_summary(block: dict[str, Any]) -> str:
    """A tool call as one line: the name, and the one argument that says what it
    is actually doing. Never the whole input — a Write's input is a whole file."""
    name = str(block.get("name") or "tool")
    args = block.get("input")
    if not isinstance(args, dict):
        return name
    for key in ("command", "file_path", "path", "pattern", "query", "url", "prompt"):
        if args.get(key):
            return f"{name}: {_clip(args[key], 200)}"
    if args:
        return f"{name}({', '.join(list(args)[:4])})"
    return name


def describe(event: dict[str, Any]) -> tuple[str, str] | None:
    """(kind, line) for one event, or None to drop it."""
    if not isinstance(event, dict):
        return None
    etype = event.get("type")

    if etype == "system":
        sub = event.get("subtype")
        if sub == "init":
            tools = event.get("tools") or []
            mcp = [
                str(m.get("name"))
                for m in (event.get("mcp_servers") or [])
                if isinstance(m, dict) and m.get("name")
            ]
            bits = [f"model {event.get('model') or 'unknown'}", f"{len(tools)} tools"]
            if mcp:
                bits.append("mcp: " + ", ".join(mcp))
            return "step", "agent started — " + " · ".join(bits)
        if sub == "post_turn_summary":
            # The CLI's own one-line summary of the turn it just finished. The
            # single most useful progress line available, and free.
            detail = event.get("status_detail") or event.get("status_category")
            return ("progress", _clip(detail)) if detail else None
        return None

    if etype == "assistant":
        message = event.get("message")
        if not isinstance(message, dict):
            return None
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                return "tool", _clip(_tool_summary(block))
        texts = [
            b.get("text", "")
            for b in message.get("content") or []
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        joined = " ".join(t for t in texts if t).strip()
        return ("output", _clip(joined)) if joined else None

    if etype == "rate_limit_event":
        info = event.get("rate_limit_info")
        if not isinstance(info, dict):
            return None
        status = info.get("status")
        # "allowed" is the normal case and would otherwise appear after every
        # single turn. Only a limit that actually bites is worth a line.
        if status and status != "allowed":
            return "error", f"rate limited ({status}) — {info.get('rateLimitType') or 'unknown window'}"
        return None

    if etype == "result":
        turns = event.get("num_turns")
        ms = event.get("duration_ms")
        bits = []
        if turns is not None:
            bits.append(f"{turns} turn{'' if turns == 1 else 's'}")
        if isinstance(ms, (int, float)):
            bits.append(f"{ms / 1000:.0f}s")
        ok = not event.get("is_error") and event.get("subtype") == "success"
        head = "agent finished" if ok else f"agent ended ({event.get('subtype') or 'error'})"
        return ("step" if ok else "error", head + (" — " + " · ".join(bits) if bits else ""))

    return None


class StreamCollector:
    """Reassembles the final answer from the event stream.

    The contract that matters: `final_text()` must return what
    `--output-format text` would have printed, because that is what every module
    parses. Three sources, in descending order of trust:

      1. the `result` event's `result` field — the CLI's own final answer;
      2. the assistant messages' text blocks, concatenated;
      3. nothing, which tells the caller to pass the raw bytes through.

    Never raises. A malformed line is skipped, because losing one event is
    survivable and losing the run is not.
    """

    def __init__(self) -> None:
        self.result_text: str | None = None
        self.assistant_text: list[str] = []
        self.saw_json = False
        self.lines = 0
        # US-54.1: the result event's own verdict on how the session ended.
        self.result_subtype: str | None = None
        self.num_turns: int | None = None
        # US-59.1: captured the moment it is observed (usually on `init`,
        # the first event of the stream) rather than deferred to `result` —
        # a killed process may never emit one, and by the time it might, the
        # id is already sitting on `self` waiting to be read.
        self.session_id: str | None = None

    def feed(self, line: str) -> dict[str, Any] | None:
        """Parse one NDJSON line; returns the event, or None if it is not one."""
        self.lines += 1
        text = line.strip()
        if not text.startswith("{"):
            return None
        try:
            event = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None
        self.saw_json = True
        # US-59.1: whichever event carries it first, and never overwritten —
        # a resumed session continues the SAME id, so the first sighting is
        # authoritative for the whole run.
        if self.session_id is None:
            sid = event.get("session_id")
            if isinstance(sid, str) and sid:
                self.session_id = sid
        if event.get("type") == "result":
            subtype = event.get("subtype")
            if isinstance(subtype, str):
                self.result_subtype = subtype
            turns = event.get("num_turns")
            if isinstance(turns, int):
                self.num_turns = turns
            if isinstance(event.get("result"), str):
                self.result_text = event["result"]
        elif event.get("type") == "assistant":
            message = event.get("message")
            if isinstance(message, dict):
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        chunk = block.get("text")
                        if chunk:
                            self.assistant_text.append(str(chunk))
        return event

    def final_text(self) -> str | None:
        if self.result_text is not None:
            return self.result_text
        if self.assistant_text:
            return "\n".join(self.assistant_text)
        return None
