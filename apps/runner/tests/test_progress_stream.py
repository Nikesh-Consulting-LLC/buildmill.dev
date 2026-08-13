"""US-39.1: you can watch the agent work.

The failure this guards against is not "no progress shown" — it is the change
that makes progress possible breaking every run in the factory.
`--output-format stream-json` replaces the CLI's stdout with NDJSON, and every
module parses that stdout: `parse_stories(res.stdout)`, the PRD is
`res.stdout.strip()`, the plan is pulled out of it. So the first and most
important assertions here are about the REASSEMBLED stdout, not about the
narration.

The events below are copied from a real Claude Code 2.1.215 run captured while
building this story, trimmed for width. They are not invented from docs.
"""

from __future__ import annotations

import asyncio
import json

from supervisor.modules.base import ShellResult
from supervisor.modules.claude import MAX_TRACED_LINES, ClaudeModule, _ClaudeStream
from supervisor.primitives import LocalPrimitives
from supervisor.progress import StreamCollector, describe

INIT = {
    "type": "system",
    "subtype": "init",
    "model": "claude-opus-4-8[1m]",
    "tools": ["Bash", "Read", "Write"],
    "mcp_servers": [{"name": "factory", "status": "pending"}],
}
ASSISTANT_TEXT = {
    "type": "assistant",
    "message": {"role": "assistant", "content": [{"type": "text", "text": "OK"}]},
}
ASSISTANT_TOOL = {
    "type": "assistant",
    "message": {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q"}}
        ],
    },
}
SUMMARY = {
    "type": "system",
    "subtype": "post_turn_summary",
    "status_category": "review_ready",
    "status_detail": "replied with OK as requested",
}
RATE_OK = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
RATE_BAD = {
    "type": "rate_limit_event",
    "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour"},
}
RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "num_turns": 1,
    "duration_ms": 2961,
    "result": "OK",
}
# The real capture carried ~10KB of embedded prompt text in one of these.
HOOK = {
    "type": "system",
    "subtype": "hook_response",
    "hook_name": "SessionStart:startup",
    "output": "x" * 9000,
}


def _stream(*events) -> str:
    return "".join(json.dumps(e) + "\n" for e in events)


# ------------------------------------------------- the answer must survive


def test_the_final_answer_is_reassembled_from_the_result_event():
    """The one that matters: downstream parsing must see what text mode gave."""
    c = StreamCollector()
    for line in _stream(INIT, ASSISTANT_TEXT, SUMMARY, RESULT).splitlines():
        c.feed(line)
    assert c.final_text() == "OK"


def test_without_a_result_event_the_assistant_text_stands_in():
    c = StreamCollector()
    for line in _stream(INIT, ASSISTANT_TEXT).splitlines():
        c.feed(line)
    assert c.final_text() == "OK"


def test_with_neither_the_caller_is_told_to_pass_the_raw_bytes_through():
    c = StreamCollector()
    for line in _stream(INIT, HOOK).splitlines():
        c.feed(line)
    assert c.final_text() is None


def test_a_malformed_line_is_skipped_not_fatal():
    c = StreamCollector()
    for line in ["{not json", "", "plain text", json.dumps(RESULT)]:
        c.feed(line)
    assert c.final_text() == "OK"


def test_the_watcher_rewrites_stdout_to_the_final_answer():
    w = _ClaudeStream(sink=None)
    for line in _stream(INIT, ASSISTANT_TOOL, ASSISTANT_TEXT, RESULT).splitlines():
        w.on_line(line)
    res = w.finalize(ShellResult(argv=["claude"], exit_code=0, stdout="<ndjson>"))
    assert res.stdout == "OK"
    # Everything else about the result is left exactly as it was.
    assert res.exit_code == 0 and res.argv == ["claude"]


def test_an_unreassemblable_stream_keeps_its_raw_output_and_says_so():
    said = []
    w = _ClaudeStream(sink=lambda kind, line: said.append((kind, line)))
    for line in _stream(HOOK).splitlines():
        w.on_line(line)
    raw = ShellResult(argv=["claude"], exit_code=0, stdout="<ndjson>")
    assert w.finalize(raw).stdout == "<ndjson>"
    assert any(k == "error" for k, _ in said)


# ------------------------------------------------------------------ US-54.1


MAX_TURNS_RESULT = {
    "type": "result",
    "subtype": "error_max_turns",
    "is_error": True,
    "num_turns": 41,
    "duration_ms": 247_000,
}


def test_the_collector_keeps_the_result_events_verdict():
    c = StreamCollector()
    for line in _stream(INIT, ASSISTANT_TEXT, MAX_TURNS_RESULT).splitlines():
        c.feed(line)
    assert c.result_subtype == "error_max_turns"
    assert c.num_turns == 41


def test_finalize_carries_the_verdict_on_both_paths():
    # With reassembled text (assistant messages stand in for the answer)…
    w = _ClaudeStream(sink=None)
    for line in _stream(ASSISTANT_TEXT, MAX_TURNS_RESULT).splitlines():
        w.on_line(line)
    res = w.finalize(ShellResult(argv=["claude"], exit_code=1, stdout="<ndjson>"))
    assert res.end_subtype == "error_max_turns" and res.num_turns == 41

    # …and on raw pass-through, when nothing could be reassembled.
    w2 = _ClaudeStream(sink=None)
    for line in _stream(MAX_TURNS_RESULT).splitlines():
        w2.on_line(line)
    raw = w2.finalize(ShellResult(argv=["claude"], exit_code=1, stdout="<ndjson>"))
    assert raw.end_subtype == "error_max_turns" and raw.num_turns == 41


def test_a_turn_limit_failure_names_the_ceiling_not_the_exit_code():
    """2026-07-30: a healthy code run died at turn 41 of max_turns=40 and the
    error said only "the claude CLI exited 1". The CLI's own verdict was in
    the stream the whole time — the failure must lead with it."""
    mod = ClaudeModule()
    mod._last_max_turns = 40
    mod._last_duration = 247
    res = ShellResult(
        argv=["claude"], exit_code=1, stdout="Now the ai_config resolver:",
        end_subtype="error_max_turns", num_turns=41,
    )
    failed = mod._failed(res, "")
    assert "hit its turn ceiling" in failed.error
    assert "max_turns=40" in failed.error
    assert "41 turns" in failed.error
    assert "exited 1" not in failed.error.splitlines()[0]


# ------------------------------------------------------------- narration


def test_every_kind_is_one_the_database_permits():
    from supervisor.progress import KINDS

    for event in (INIT, ASSISTANT_TEXT, ASSISTANT_TOOL, SUMMARY, RATE_BAD, RESULT):
        described = describe(event)
        assert described is not None, event
        assert described[0] in KINDS, described


def test_a_tool_call_names_the_tool_and_what_it_is_doing():
    kind, line = describe(ASSISTANT_TOOL)
    assert kind == "tool"
    assert "Bash" in line and "pytest -q" in line


def test_the_turn_summary_is_used_verbatim():
    assert describe(SUMMARY) == ("progress", "replied with OK as requested")


def test_an_allowed_rate_limit_is_not_noise():
    """It appears after every turn; only a limit that bites is worth a line."""
    assert describe(RATE_OK) is None
    assert describe(RATE_BAD)[0] == "error"


def test_hook_events_are_dropped_not_relayed():
    """The real capture carried ~10KB of embedded prompt text per hook event.
    Replaying that into a UI as if the agent had said it is wrong twice over:
    it is noise, and it is untrusted text."""
    assert describe(HOOK) is None


def test_a_long_line_is_clipped():
    kind, line = describe(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "y" * 5000}]}}
    )
    assert kind == "output" and len(line) <= 400


def test_the_trace_is_capped_and_says_so_while_the_console_is_not(caplog):
    said = []
    w = _ClaudeStream(sink=lambda kind, line: said.append((kind, line)))
    for _ in range(MAX_TRACED_LINES + 50):
        w.on_line(json.dumps(SUMMARY))
    # One cap notice, and nothing after it.
    assert len(said) == MAX_TRACED_LINES + 1
    assert "not traced" in said[-1][1]


def test_narration_can_never_break_the_run():
    def boom(kind, line):
        raise RuntimeError("sink exploded")

    w = _ClaudeStream(sink=boom)
    w.on_line(json.dumps(SUMMARY))  # must not raise
    assert w.finalize(ShellResult(argv=[], exit_code=0, stdout="x")).stdout == "x"


# ------------------------------------------------------------- the flags


def test_streaming_is_the_default_and_asks_for_verbose():
    argv = ClaudeModule().build_argv("do it", "code")
    assert "stream-json" in argv
    # The CLI refuses stream-json with -p unless --verbose is present.
    assert "--verbose" in argv


def test_the_escape_hatch_restores_text_mode(monkeypatch):
    monkeypatch.setenv("RUNNER_STREAM_PROGRESS", "0")
    argv = ClaudeModule().build_argv("do it", "code")
    assert "text" in argv and "stream-json" not in argv
    assert ClaudeModule().stream_watcher(None) is None


# ------------------------------------------------- the incremental read


def _run(argv, **kw):
    return asyncio.run(LocalPrimitives().run_shell(argv, **kw))


def test_lines_arrive_while_the_process_runs_and_stdout_is_unchanged():
    import sys

    seen: list[str] = []
    res = _run(
        [sys.executable, "-c", "print('one'); print('two'); print('three')"],
        on_line=seen.append,
    )
    assert seen == ["one", "two", "three"]
    # The captured text is what communicate() would have returned.
    assert res.stdout.splitlines() == ["one", "two", "three"]
    assert res.exit_code == 0


def test_an_event_larger_than_the_streamreader_line_limit_survives():
    """asyncio's StreamReader caps a line at 64KiB and RAISES past it. The real
    stream already carries ~10KB events and a tool result holding a file goes
    far higher, which is why this reads chunks and splits by hand."""
    import sys

    big = 300_000
    seen: list[str] = []
    res = _run(
        [sys.executable, "-c", f"print('z' * {big})"],
        on_line=seen.append,
    )
    assert len(seen) == 1 and len(seen[0]) == big
    assert len(res.stdout.strip()) == big


def test_a_nonzero_exit_still_reports_its_code_and_output():
    import sys

    res = _run([sys.executable, "-c", "import sys; print('bad'); sys.exit(3)"])
    assert res.exit_code == 3 and "bad" in res.stdout
