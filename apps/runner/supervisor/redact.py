"""us-96.11: a key never rides the trace.

The us-89.1 rule is "files travel; secrets must not ride them" — and
telemetry travels further than files: into the database, the dashboard, and
content_audit. On 2026-08-14 (run 22b807a5) a tool summary streamed to
run_trace carried the loopback broker's X-Factory-Local-Key value verbatim,
twice. That key happened to be machine-local and short-lived; nothing about
the path that leaked it knew that — the same pipe would have shipped the
worker token or a gateway key just as faithfully.

The supervisor knows every secret it holds, so nothing it emits off the box
may contain one. Every holder REGISTERS its live value here (the broker's
local key at mint, the worker token at startup, gateway/model keys and the
subscription token at env-mint), and the two posting layers — the control
socket's notify() and the WorkerClient's submit() — SCRUB through here.
Values are matched exactly, plus their base64 and URL-encoded forms, so
ordinary prose about keys is untouched. No entropy guessing: hashes and
shas legitimately ride traces, and flagging them would bury the signal.
"""

from __future__ import annotations

import base64
import urllib.parse

# value -> kind. A dict, not a list: re-registering the same value (every
# run re-mints its env) stays one entry.
_SECRETS: dict[str, str] = {}

# Anything shorter is more likely to be a real word than a credential, and
# replacing it would shred ordinary text.
_MIN_LEN = 8


def register(kind: str, value: str | None) -> None:
    """Remember a live secret so scrub() can catch it in any outbound text."""
    if not value or len(value) < _MIN_LEN:
        return
    _SECRETS[value] = kind
    encoded = base64.b64encode(value.encode()).decode()
    _SECRETS[encoded] = kind
    quoted = urllib.parse.quote(value, safe="")
    if quoted != value:
        _SECRETS[quoted] = kind


def scrub(text: str | None) -> str | None:
    """Replace every registered secret in `text` with a mask naming its
    kind — `[redacted:worker-token]` — exact-match only."""
    if not text:
        return text
    for value, kind in _SECRETS.items():
        if value in text:
            text = text.replace(value, f"[redacted:{kind}]")
    return text


def scrub_params(params: dict | None) -> dict | None:
    """Shallow-scrub every string value of an outbound message's params.
    Ids and enum fields pass through untouched (a uuid never matches a
    registered secret); free-text fields are where credentials leak."""
    if not params:
        return params
    return {
        k: scrub(v) if isinstance(v, str) else v for k, v in params.items()
    }


def clear() -> None:
    """Tests only."""
    _SECRETS.clear()
