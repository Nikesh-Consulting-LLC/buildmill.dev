"""us-96.11 AC2: the API assumes the runner failed — header-shaped
credentials are masked at write time, without knowing any value."""

from app.db import scrub_credential_patterns


def test_the_incident_header_shape_is_masked():
    line = (
        "ran: curl -s -H 'X-Factory-Local-Key: wJalrXUtnFEMIK7MDENG' "
        "http://127.0.0.1:8321/tools"
    )
    out = scrub_credential_patterns(line)
    assert "wJalrXUtnFEMIK7MDENG" not in out
    assert "X-Factory-Local-Key: [redacted]" in out
    assert "curl -s" in out


def test_worker_token_and_bearer_shapes_are_masked():
    line = (
        "headers = {'X-Worker-Token': 'bm-worker-abc123', "
        "'Authorization': 'Bearer eyJhbGciOi.payload.sig'}"
    )
    out = scrub_credential_patterns(line)
    assert "bm-worker-abc123" not in out
    assert "eyJhbGciOi.payload.sig" not in out
    assert out.count("[redacted]") == 2


def test_case_and_separator_variants_are_masked():
    out = scrub_credential_patterns("x-worker-token=bm-secret-value-1")
    assert "bm-secret-value-1" not in out


def test_prose_and_hashes_pass_through():
    line = "commit 2545a3512bd loaded; authorization is a Settings concern"
    assert scrub_credential_patterns(line) == line
    assert scrub_credential_patterns(None) is None
    assert scrub_credential_patterns("") == ""
