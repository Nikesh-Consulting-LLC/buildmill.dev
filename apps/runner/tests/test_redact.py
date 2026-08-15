"""us-96.11: a key never rides the trace.

The 2026-08-14 incident replayed: run 22b807a5's zombie probed the loopback
broker with curl, and the tool summaries streamed to run_trace carried the
X-Factory-Local-Key value verbatim, twice. The supervisor knows every secret
it holds, so nothing it emits off the box may contain one.
"""

import base64

import pytest

from supervisor import redact


@pytest.fixture(autouse=True)
def clean_registry():
    redact.clear()
    yield
    redact.clear()


def test_the_incident_shape_is_masked():
    key = "wJalrXUtnFEMI-K7MDENG-bPxRfiCY"
    redact.register("factory-local-key", key)
    line = (
        "ran: curl -s -H 'X-Factory-Local-Key: " + key + "' "
        "http://127.0.0.1:8321/tools — answered 401"
    )
    out = redact.scrub(line)
    assert key not in out
    assert "[redacted:factory-local-key]" in out
    # The rest of the line survives — evidence stays legible.
    assert "curl -s" in out and "answered 401" in out


def test_an_env_echo_of_the_worker_token_is_masked():
    token = "bm-worker-9f8e7d6c5b4a3210"
    redact.register("worker-token", token)
    out = redact.scrub(f"TOKEN_PREFIX={token}\nPATH=/usr/bin")
    assert token not in out
    assert "[redacted:worker-token]" in out


def test_encoded_forms_are_masked_too():
    key = "super-secret-value-123"
    redact.register("gateway-key", key)
    encoded = base64.b64encode(key.encode()).decode()
    out = redact.scrub(f"auth blob: {encoded}")
    assert encoded not in out
    assert "[redacted:gateway-key]" in out


def test_ordinary_prose_about_keys_is_untouched():
    redact.register("worker-token", "bm-worker-9f8e7d6c5b4a3210")
    line = "rotating the worker token is a Settings action"
    assert redact.scrub(line) == line


def test_short_values_are_never_registered():
    redact.register("worker-token", "abc")
    assert redact.scrub("abc def") == "abc def"


def test_scrub_params_walks_string_fields_only():
    key = "wJalrXUtnFEMI-K7MDENG-bPxRfiCY"
    redact.register("factory-local-key", key)
    params = {"run_id": "r1", "kind": "progress", "content": f"saw {key}", "n": 3}
    out = redact.scrub_params(params)
    assert out["run_id"] == "r1" and out["n"] == 3
    assert key not in out["content"]
