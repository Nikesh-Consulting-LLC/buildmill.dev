"""Reading token usage out of a provider response as it streams past (US-33.1).

The gateway forwards with `resp.aiter_raw()` — raw passthrough — so metering
means teeing the stream and parsing provider-shaped usage out of it without
touching the bytes the caller receives.

Two shapes matter. Anthropic reports input tokens in `message_start` and output
tokens in each `message_delta`; OpenAI-shaped providers report both in a final
chunk that only appears when `stream_options.include_usage` was requested. Both
also have a non-streaming form where the whole body is one JSON object.

The rule that shapes everything here: **a shape that cannot be read is recorded
as unparsed, never as zero.** A zero is indistinguishable from a free call and
would quietly understate every total in the system.
"""

from __future__ import annotations

import json
from typing import Any

# How much of a non-SSE body to keep while looking for a usage object. A JSON
# completion response is small; a streamed one never needs this path. The cap
# exists so a pathological response cannot grow the buffer without bound.
MAX_BODY_BUFFER = 256 * 1024

# The longest partial SSE line we will hold while waiting for its newline.
MAX_LINE_BUFFER = 1024 * 1024

OPENAI_SHAPED = ("openai", "groq", "xai", "ollama")


class UsageMeter:
    """Feed it the response bytes; ask it what the call used.

    Deliberately tolerant: every parse is wrapped, and anything unexpected
    leaves `parsed` False with a note rather than raising into the relay.
    """

    def __init__(self, provider_type: str = ""):
        self.provider_type = (provider_type or "").lower()
        self.tokens_in: int | None = None
        self.tokens_out: int | None = None
        # US-38.1: subsets of tokens_in, not siblings of it. A cache read bills
        # at 0.1x the input rate and a write at 1.25x, so folding all three into
        # one number and multiplying by one rate charges a cached token nine to
        # twelve times what it actually cost.
        self.cache_read: int | None = None
        self.cache_write: int | None = None
        self.parse_note: str | None = None
        self._line = bytearray()
        self._body = bytearray()
        self._body_truncated = False
        self._saw_sse = False

    # ------------------------------------------------------------------ input

    def feed(self, chunk: bytes) -> None:
        """Absorb one raw chunk. Never raises."""
        try:
            self._feed(chunk)
        except Exception:  # noqa: BLE001 — measuring loses every conflict
            self.parse_note = self.parse_note or "meter raised while feeding"

    def _feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        # The non-streaming path: keep a bounded prefix of the body.
        if len(self._body) < MAX_BODY_BUFFER:
            self._body.extend(chunk[: MAX_BODY_BUFFER - len(self._body)])
            if len(self._body) >= MAX_BODY_BUFFER:
                self._body_truncated = True

        self._line.extend(chunk)
        if len(self._line) > MAX_LINE_BUFFER:
            # A stream with no newline in a megabyte is not SSE; stop buffering
            # lines rather than growing forever.
            self._line.clear()
            return
        while True:
            nl = self._line.find(b"\n")
            if nl == -1:
                break
            line = bytes(self._line[:nl])
            del self._line[: nl + 1]
            self._consume_line(line)

    def _consume_line(self, line: bytes) -> None:
        text = line.strip()
        if not text.startswith(b"data:"):
            return
        self._saw_sse = True
        payload = text[5:].strip()
        if not payload or payload == b"[DONE]":
            return
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            return
        if isinstance(event, dict):
            self._absorb(event)

    # ----------------------------------------------------------------- parsing

    def _absorb(self, event: dict[str, Any]) -> None:
        """Take whatever usage this event carries, from either shape."""
        # Anthropic streaming: input arrives once, output is restated and grows.
        message = event.get("message")
        if isinstance(message, dict):
            self._take(message.get("usage"))
        self._take(event.get("usage"))

    def _take(self, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        # Anthropic names them input_tokens/output_tokens; OpenAI-shaped
        # providers use prompt_tokens/completion_tokens.
        tin = _int(usage.get("input_tokens"))
        if tin is None:
            tin = _int(usage.get("prompt_tokens"))
        tout = _int(usage.get("output_tokens"))
        if tout is None:
            tout = _int(usage.get("completion_tokens"))
        # US-38.1: the cache classes, kept apart AND still folded into the input
        # total. `tokens_in` goes on meaning "all input tokens" -- every existing
        # aggregate reads it, and redefining it to fresh-only would change every
        # historical figure in the app on the day this ships.
        #
        # The two provider shapes differ in a way that matters. Anthropic
        # reports both classes ALONGSIDE input_tokens, so they are added.
        # OpenAI-shaped providers report reads only, nested under
        # prompt_tokens_details, and their prompt_tokens ALREADY includes them --
        # adding those would double count, which is why only the Anthropic keys
        # are summed into tin.
        c_write = _int(usage.get("cache_creation_input_tokens"))
        c_read = _int(usage.get("cache_read_input_tokens"))
        for key in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            extra = _int(usage.get(key))
            if extra:
                tin = (tin or 0) + extra
        if c_read is None:
            details = usage.get("prompt_tokens_details")
            if isinstance(details, dict):
                c_read = _int(details.get("cached_tokens"))
        if c_read is not None:
            self.cache_read = max(self.cache_read or 0, c_read)
        if c_write is not None:
            self.cache_write = max(self.cache_write or 0, c_write)
        if tin is not None:
            self.tokens_in = max(self.tokens_in or 0, tin)
        if tout is not None:
            # Anthropic's message_delta restates the running total, so the
            # largest value seen is the final one. Taking the max is also right
            # for a single OpenAI usage chunk.
            self.tokens_out = max(self.tokens_out or 0, tout)

    def finish(self) -> None:
        """Called once the stream is done. Never raises."""
        try:
            self._finish()
        except Exception:  # noqa: BLE001
            self.parse_note = self.parse_note or "meter raised while finishing"

    def _finish(self) -> None:
        # A trailing line with no newline still counts.
        if self._line:
            self._consume_line(bytes(self._line))
            self._line.clear()
        if self.parsed:
            return
        # Non-streaming: the whole body is one JSON object.
        if not self._saw_sse and self._body and not self._body_truncated:
            try:
                event = json.loads(bytes(self._body))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # UnicodeDecodeError is what a compressed (binary) body raises.
                self.parse_note = "response body was not JSON and carried no SSE usage"
                return
            if isinstance(event, dict):
                self._absorb(event)
                if self.parsed:
                    return
                self.parse_note = "JSON response carried no usage object"
                return
        if not self.parse_note:
            self.parse_note = (
                "no usage in the stream — an OpenAI-shaped provider reports it "
                "only when stream_options.include_usage is requested"
                if self._saw_sse
                else "response carried no readable usage"
            )

    # ------------------------------------------------------------------ output

    @property
    def parsed(self) -> bool:
        """Both halves read. A half-read call is not a measured call: reporting
        input with a null output would look like a call that produced nothing."""
        return self.tokens_in is not None and self.tokens_out is not None

    def as_row(self) -> dict[str, Any]:
        # US-38.1: the cache figures are nullable and NULL means "not reported",
        # never "zero cache". A provider that says nothing about caching must not
        # be recorded as having cached nothing -- the same mistake as recording
        # an unreadable usage object as a free call.
        #
        # Clamped to the total they are subsets of. Migration 165 enforces it
        # too, but a constraint violation here would lose the whole row, and
        # losing a usage row is worse than clamping one.
        cache_read = self.cache_read
        cache_write = self.cache_write
        if self.tokens_in is not None:
            total = (cache_read or 0) + (cache_write or 0)
            if total > self.tokens_in:
                if cache_read is not None:
                    cache_read = min(cache_read, self.tokens_in)
                if cache_write is not None:
                    cache_write = max(0, self.tokens_in - (cache_read or 0))
        cache = {"cache_read_tokens": cache_read, "cache_write_tokens": cache_write}
        if self.parsed:
            return {
                "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out,
                **cache,
                "parsed": True,
                "parse_note": None,
            }
        return {
            # Deliberately null, not 0.
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            **cache,
            "parsed": False,
            "parse_note": self.parse_note or "usage could not be read",
        }


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


# ---------------------------------------------------------------------------
# Asking for usage we would otherwise not be given
# ---------------------------------------------------------------------------


def ensure_usage_requested(
    provider_type: str, body: bytes
) -> tuple[bytes, bool]:
    """Add `stream_options.include_usage` to an OpenAI-shaped streaming request.

    Returns the body to forward and whether it changed. An OpenAI-shaped
    provider sends no usage at all on a stream unless this is asked for, so
    without it every such call would be recorded as unparsed — which is honest
    but useless.

    Never raises and never mangles: anything it cannot confidently rewrite is
    returned untouched.
    """
    if (provider_type or "").lower() not in OPENAI_SHAPED:
        return body, False
    if not body:
        return body, False
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body, False
    if not isinstance(payload, dict) or payload.get("stream") is not True:
        return body, False
    options = payload.get("stream_options")
    if isinstance(options, dict) and options.get("include_usage") is True:
        return body, False
    payload["stream_options"] = {
        **(options if isinstance(options, dict) else {}),
        "include_usage": True,
    }
    try:
        return json.dumps(payload).encode("utf-8"), True
    except (TypeError, ValueError):
        return body, False


def cost_for(
    tokens_in: int | None,
    tokens_out: int | None,
    rate_in: float | None,
    rate_out: float | None,
    cache_read: int | None = None,
    cache_write: int | None = None,
    rate_cache_read: float | None = None,
    rate_cache_write: float | None = None,
) -> float | None:
    """Money from tokens and the rates in force, or None when either is unknown.

    None rather than 0.0 on purpose: "we do not have a price for this model" and
    "this call was free" must not read the same on a spend report.

    US-38.1: input is priced in three classes because it is sold in three
    classes. `tokens_in` is the TOTAL and the cache figures are subsets of it,
    so fresh input is whatever is left after both are taken out.

    Two defaults, both deliberately the conservative direction. An unset cache
    rate charges those tokens at `rate_in` -- exactly today's behaviour, so no
    figure drops without a rate being configured. A NULL cache count (a row that
    predates the split, or a provider that said nothing) prices the whole of
    `tokens_in` as fresh, which is also what it costs today. us-33.1's rule
    stands: unknown cost must never read as free.
    """
    if rate_in is None and rate_out is None:
        return None
    if tokens_in is None and tokens_out is None:
        return None

    read = cache_read or 0
    write = cache_write or 0
    fresh = max(0, (tokens_in or 0) - read - write)

    r_read = rate_in if rate_cache_read is None else rate_cache_read
    r_write = rate_in if rate_cache_write is None else rate_cache_write

    total = 0.0
    total += (fresh / 1_000_000) * float(rate_in or 0)
    total += (read / 1_000_000) * float(r_read or 0)
    total += (write / 1_000_000) * float(r_write or 0)
    total += ((tokens_out or 0) / 1_000_000) * float(rate_out or 0)
    return round(total, 6)
