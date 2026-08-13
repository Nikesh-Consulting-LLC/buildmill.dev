"""US-62.x: OpenCode's `--format json` NDJSON stream, reassembled.

Every module parses `res.stdout` as if it were plain text -- the PRD is
`res.stdout.strip()`, `parse_stories(res.stdout)` for a breakdown. Passing the
raw NDJSON through (what happened on the first real run) breaks all of it.

The events below are copied from real `opencode-ai` 1.18.15 runs against Groq
through the gateway, captured while diagnosing that failure — not invented
from docs.
"""

from __future__ import annotations

from supervisor.modules.base import ShellResult
from supervisor.modules.opencode import _OpenCodeCollector, _OpenCodeStream

STEP_START = (
    '{"type":"step_start","timestamp":1786124861870,'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP",'
    '"part":{"id":"prt_1","messageID":"msg_1",'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP",'
    '"snapshot":"63d8d5c","type":"step-start"}}'
)
TEXT = (
    '{"type":"text","timestamp":1786124862422,'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP",'
    '"part":{"id":"prt_2","messageID":"msg_1",'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP","type":"text",'
    '"text":"## Problem\\nThe problem.\\n\\n## Goals\\nThe goals.",'
    '"time":{"start":1786124861855,"end":1786124862417}}}'
)
STEP_FINISH = (
    '{"type":"step_finish","timestamp":1786124862722,'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP",'
    '"part":{"id":"prt_3","reason":"stop","messageID":"msg_1",'
    '"sessionID":"ses_022a98acbffe5hYmOtNFs7tUnP","type":"step-finish",'
    '"tokens":{"total":20185,"input":20023,"output":162,"reasoning":0,'
    '"cache":{"write":0,"read":0}},"cost":0.01194155}}'
)
TOOL_USE = (
    '{"type":"tool_use","timestamp":1786122247638,'
    '"sessionID":"ses_1",'
    '"part":{"type":"tool","tool":"write","callID":"c1",'
    '"state":{"status":"completed"}}}'
)
ERROR = (
    '{"type":"error","timestamp":1786122250403,"sessionID":"ses_1",'
    '"error":{"name":"UnknownError",'
    '"data":{"message":"Model not found: openai/llama-3.3-70b-versatile."}}}'
)


def test_reassembles_the_final_text_from_a_real_prd_run():
    """The exact failure mode this guards against: a PRD field that was the
    whole NDJSON transcript, unreadable and unparseable by the breakdown
    step that reads it next."""
    collector = _OpenCodeCollector()
    for line in (STEP_START, TEXT, STEP_FINISH):
        collector.feed(line)
    final = collector.final_text()
    assert final is not None
    assert "## Problem" in final and "## Goals" in final
    assert "step_start" not in final and "sessionID" not in final


def test_multiple_text_events_join_with_a_blank_line():
    collector = _OpenCodeCollector()
    collector.feed(
        '{"type":"text","part":{"type":"text","text":"first turn"}}'
    )
    collector.feed(
        '{"type":"text","part":{"type":"text","text":"second turn"}}'
    )
    assert collector.final_text() == "first turn\n\nsecond turn"


def test_a_malformed_line_is_skipped_not_raised():
    collector = _OpenCodeCollector()
    assert collector.feed("not json at all") is None
    assert collector.feed("") is None
    assert collector.final_text() is None


def test_error_event_is_captured_for_the_no_text_fallback():
    collector = _OpenCodeCollector()
    collector.feed(TOOL_USE)
    collector.feed(ERROR)
    assert collector.final_text() is None  # no text part was ever seen
    assert "Model not found" in collector.error_message


def test_stream_finalize_replaces_stdout_with_the_reassembled_text():
    lines = []
    watcher = _OpenCodeStream(sink=lambda kind, line: lines.append((kind, line)))
    for line in (STEP_START, TEXT, STEP_FINISH):
        watcher.on_line(line)
    raw = ShellResult(argv=["opencode"], exit_code=0, stdout="\n".join(
        (STEP_START, TEXT, STEP_FINISH)
    ))
    result = watcher.finalize(raw)
    assert result.stdout is not None
    assert "## Problem" in result.stdout
    assert "step_start" not in result.stdout
    assert any(kind == "tool" for kind, _ in lines) is False  # no tool call here


def test_stream_narrates_tool_use_and_error_without_raising():
    lines = []
    watcher = _OpenCodeStream(sink=lambda kind, line: lines.append((kind, line)))
    watcher.on_line(TOOL_USE)
    watcher.on_line(ERROR)
    kinds = [k for k, _ in lines]
    assert "tool" in kinds
    assert "error" in kinds


def test_finalize_with_no_text_seen_reports_via_sink_instead_of_raw_ndjson():
    lines = []
    watcher = _OpenCodeStream(sink=lambda kind, line: lines.append((kind, line)))
    watcher.on_line(TOOL_USE)
    raw = ShellResult(argv=["opencode"], exit_code=0, stdout=TOOL_USE)
    result = watcher.finalize(raw)
    # No text event was ever seen, so the raw stream passes through --
    # but the sink is told why, rather than leaving a silent mystery.
    assert result is raw
    assert any(kind == "error" for kind, _ in lines)
