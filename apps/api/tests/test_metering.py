"""US-33.1: the gateway meters every call.

`runs.tokens_in`, `tokens_out` and `cost_usd` have been in the schema since
migration 005 and nothing has ever written them. These tests pin the parsing of
both provider shapes, the rule that an unreadable shape is recorded as UNPARSED
rather than zero (a zero is indistinguishable from a free call and would
understate every total), and the invariant that metering cannot alter the bytes
the caller receives or fail the call.
"""

from __future__ import annotations

import json

import pytest

from app import metering


def _sse(*events) -> bytes:
    out = b""
    for e in events:
        out += b"data: " + json.dumps(e).encode() + b"\n\n"
    return out


# ------------------------------------------------------- Anthropic streaming


def test_anthropic_usage_comes_from_message_start_and_message_delta():
    m = metering.UsageMeter("anthropic")
    m.feed(
        b"event: message_start\n"
        + b'data: {"type":"message_start","message":{"usage":'
        + b'{"input_tokens":1200,"output_tokens":1}}}\n\n'
    )
    m.feed(_sse({"type": "content_block_delta", "delta": {"text": "hi"}}))
    m.feed(_sse({"type": "message_delta", "usage": {"output_tokens": 350}}))
    m.finish()
    assert m.as_row() == {
        "tokens_in": 1200,
        "tokens_out": 350,
        # US-38.1: NULL, not 0 -- this fixture's usage object says nothing about
        # caching, and "the provider did not report it" must not read as
        # "nothing was cached". Priced as fully fresh, exactly as before.
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "parsed": True,
        "parse_note": None,
    }


def test_the_running_output_total_takes_the_largest_not_the_last():
    """Anthropic restates the total in each message_delta; a sum would multiply
    the bill and a last-write-wins would be wrong if a later event omits it."""
    m = metering.UsageMeter("anthropic")
    m.feed(_sse({"type": "message_start", "message": {"usage": {"input_tokens": 10, "output_tokens": 1}}}))
    m.feed(_sse({"type": "message_delta", "usage": {"output_tokens": 100}}))
    m.feed(_sse({"type": "message_delta", "usage": {"output_tokens": 250}}))
    m.feed(_sse({"type": "message_stop"}))
    m.finish()
    assert m.tokens_out == 250


def test_cache_tokens_are_counted_as_input_not_dropped():
    """The org paid for them; dropping them understates the bill."""
    m = metering.UsageMeter("anthropic")
    m.feed(
        _sse(
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 0,
                        "cache_creation_input_tokens": 900,
                        "cache_read_input_tokens": 4000,
                    }
                },
            }
        )
    )
    m.feed(_sse({"type": "message_delta", "usage": {"output_tokens": 5}}))
    m.finish()
    assert m.tokens_in == 5000
    assert m.parsed


# -------------------------------------------------------- OpenAI-shaped stream


def test_openai_usage_comes_from_the_final_chunk():
    m = metering.UsageMeter("groq")
    m.feed(_sse({"choices": [{"delta": {"content": "hi"}}], "usage": None}))
    m.feed(
        _sse({"choices": [], "usage": {"prompt_tokens": 88, "completion_tokens": 12}})
    )
    m.feed(b"data: [DONE]\n\n")
    m.finish()
    row = m.as_row()
    assert (row["tokens_in"], row["tokens_out"], row["parsed"]) == (88, 12, True)


def test_a_stream_with_no_usage_is_unparsed_and_says_why():
    """The OpenAI-shaped failure mode: no usage unless include_usage was asked
    for. Recorded as unparsed with the reason, never as a zero."""
    m = metering.UsageMeter("openai")
    m.feed(_sse({"choices": [{"delta": {"content": "hi"}}]}))
    m.feed(b"data: [DONE]\n\n")
    m.finish()
    row = m.as_row()
    assert row["parsed"] is False
    assert row["tokens_in"] is None and row["tokens_out"] is None
    assert "include_usage" in row["parse_note"]


# ------------------------------------------------------------- non-streaming


def test_a_plain_json_response_is_metered_too():
    m = metering.UsageMeter("anthropic")
    m.feed(json.dumps({"usage": {"input_tokens": 7, "output_tokens": 9}}).encode())
    m.finish()
    assert (m.tokens_in, m.tokens_out, m.parsed) == (7, 9, True)


def test_a_json_response_without_usage_is_unparsed():
    m = metering.UsageMeter("openai")
    m.feed(json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode())
    m.finish()
    row = m.as_row()
    assert row["parsed"] is False
    assert "no usage object" in row["parse_note"]


def test_a_non_json_body_is_unparsed_not_a_crash():
    m = metering.UsageMeter("anthropic")
    m.feed(b"<html>502 Bad Gateway</html>")
    m.finish()
    assert m.as_row()["parsed"] is False


def test_a_binary_body_is_unparsed_with_the_honest_note_not_a_crash():
    """The 2026-07-27 failure: a gzip body is not UTF-8, and json.loads raises
    UnicodeDecodeError — not JSONDecodeError — so every metered call recorded
    the internal 'meter raised while finishing' instead of saying what it saw."""
    import gzip

    m = metering.UsageMeter("anthropic")
    m.feed(gzip.compress(json.dumps({"usage": {"input_tokens": 7}}).encode()))
    m.finish()
    row = m.as_row()
    assert row["parsed"] is False
    assert "not JSON" in row["parse_note"]


# ------------------------------------------------------- robustness of the tee


def test_chunk_boundaries_do_not_matter():
    """The provider decides where the TCP boundaries fall, so a usage line split
    across chunks must still parse."""
    payload = _sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 5, "output_tokens": 0}}},
        {"type": "message_delta", "usage": {"output_tokens": 77}},
    )
    for size in (1, 3, 7, 64, len(payload)):
        m = metering.UsageMeter("anthropic")
        for i in range(0, len(payload), size):
            m.feed(payload[i : i + size])
        m.finish()
        assert (m.tokens_in, m.tokens_out) == (5, 77), size


def test_a_final_line_with_no_trailing_newline_still_counts():
    m = metering.UsageMeter("anthropic")
    m.feed(b'data: {"usage":{"input_tokens":3,"output_tokens":4}}')
    m.finish()
    assert m.parsed


def test_half_read_usage_is_not_a_measured_call():
    """Input with a null output would look like a call that produced nothing."""
    m = metering.UsageMeter("anthropic")
    m.feed(_sse({"type": "message_start", "message": {"usage": {"input_tokens": 10}}}))
    m.finish()
    assert m.parsed is False
    assert m.as_row()["tokens_in"] == 10  # what we did read is still recorded


def test_the_line_buffer_is_bounded():
    """A stream with no newlines must not grow the buffer without bound."""
    m = metering.UsageMeter("anthropic")
    m.feed(b"x" * (metering.MAX_LINE_BUFFER + 10))
    m.feed(b"y" * 100)
    m.finish()
    assert len(m._line) < metering.MAX_LINE_BUFFER
    assert m.as_row()["parsed"] is False


def test_feeding_junk_never_raises():
    m = metering.UsageMeter("anthropic")
    for junk in (b"", b"\n\n\n", b"data:\n", b"data: not json\n", b"\x00\xff\n"):
        m.feed(junk)
    m.finish()
    assert m.as_row()["parsed"] is False


def test_a_meter_that_raises_internally_degrades_to_unparsed(monkeypatch):
    """The stated rule: a parse failure is logged and the response is unchanged."""
    m = metering.UsageMeter("anthropic")

    def boom(self, chunk):
        raise RuntimeError("nope")

    monkeypatch.setattr(metering.UsageMeter, "_feed", boom)
    m.feed(b"anything")  # must not raise
    m.finish()
    assert m.as_row()["parsed"] is False


# ------------------------------------------------- asking for usage we'd not get


def test_include_usage_is_injected_into_an_openai_stream():
    body = json.dumps({"model": "m", "stream": True, "messages": []}).encode()
    out, changed = metering.ensure_usage_requested("groq", body)
    assert changed
    assert json.loads(out)["stream_options"] == {"include_usage": True}
    # and nothing else about the request moved
    assert json.loads(out)["messages"] == []
    assert json.loads(out)["model"] == "m"


def test_existing_stream_options_are_preserved():
    body = json.dumps(
        {"stream": True, "stream_options": {"something": 1}}
    ).encode()
    out, changed = metering.ensure_usage_requested("openai", body)
    assert changed
    assert json.loads(out)["stream_options"] == {
        "something": 1,
        "include_usage": True,
    }


def test_an_already_asking_request_is_left_alone():
    body = json.dumps(
        {"stream": True, "stream_options": {"include_usage": True}}
    ).encode()
    out, changed = metering.ensure_usage_requested("openai", body)
    assert not changed and out == body


@pytest.mark.parametrize(
    "provider,body",
    [
        ("anthropic", json.dumps({"stream": True}).encode()),  # wrong shape
        ("openai", json.dumps({"stream": False}).encode()),  # not streaming
        ("openai", json.dumps({}).encode()),  # no stream key
        ("openai", b""),  # no body
        ("openai", b"not json"),  # unparseable
        ("openai", b'"a string"'),  # JSON but not an object
    ],
)
def test_anything_it_cannot_confidently_rewrite_is_forwarded_untouched(
    provider, body
):
    out, changed = metering.ensure_usage_requested(provider, body)
    assert out == body and changed is False


# ----------------------------------------------------------------------- cost


def test_cost_is_tokens_times_the_rate():
    # 1M in at $3, 500k out at $15
    assert metering.cost_for(1_000_000, 500_000, 3.0, 15.0) == 10.5


def test_an_unpriced_model_costs_none_not_zero():
    """"We have no price for this" and "it was free" must not read the same."""
    assert metering.cost_for(1000, 1000, None, None) is None


def test_a_half_priced_model_still_computes_what_it_can():
    assert metering.cost_for(1_000_000, 1_000_000, 3.0, None) == 3.0


def test_unparsed_tokens_cost_none():
    assert metering.cost_for(None, None, 3.0, 15.0) is None


def test_cost_rounds_to_the_micro_dollar():
    value = metering.cost_for(1, 1, 3.0, 15.0)
    assert value == round(value, 6)
