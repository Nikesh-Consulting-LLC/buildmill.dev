"""US-16.2: the public ingestion endpoint and the rules that keep it safe to
expose — one indistinguishable 401, a minimal response, capped payloads, a
per-deployment rate limit, and org/project resolved from the deployment row
rather than from anything the caller sent.

Hermetic: the database is never touched. The two functions that talk to
Postgres are stubbed; everything else here is the real code path.
"""

import json

import pytest

from app import app_issues

DEPLOYMENT = {
    "id": "11111111-1111-4111-8111-111111111111",
    "org_id": "22222222-2222-4222-8222-222222222222",
    "project_id": "33333333-3333-4333-8333-333333333333",
    "issue_report_key_hash": "unused-here",
}
URL = f"/api/v1/report/{DEPLOYMENT['id']}/issues"


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    app_issues.reset_rate_limits()
    yield
    app_issues.reset_rate_limits()


@pytest.fixture()
def accept_key(monkeypatch):
    """Authenticate exactly one key, so a wrong one exercises the real path."""

    def _auth(settings, deployment_id, key):
        if deployment_id == DEPLOYMENT["id"] and key == "sfr_good":
            return DEPLOYMENT
        return None

    monkeypatch.setattr(app_issues, "authenticate_deployment", _auth)


@pytest.fixture()
def captured(monkeypatch, accept_key):
    """Record what ingest_report was handed, without writing anything."""
    seen: list[dict] = []

    def _ingest(settings, deployment, payload):
        seen.append({"deployment": deployment, "payload": payload})
        return {"id": "44444444-4444-4444-8444-444444444444", "deduped": False}

    monkeypatch.setattr(app_issues, "ingest_report", _ingest)
    return seen


# --- authentication is one answer, whatever went wrong ----------------------


def _bodies():
    return {"source": "automated", "error_type": "TypeError", "message": "boom"}


def test_valid_key_is_accepted_with_a_minimal_body(client, captured):
    resp = client.post(URL, json=_bodies(), headers={"X-Report-Key": "sfr_good"})
    assert resp.status_code == 201
    # Exactly two keys. Anything more is something a stranger has learned.
    assert resp.json() == {
        "id": "44444444-4444-4444-8444-444444444444",
        "status": "accepted",
    }


@pytest.mark.parametrize(
    "deployment_id, key",
    [
        (DEPLOYMENT["id"], "sfr_wrong"),          # wrong key
        (DEPLOYMENT["id"], ""),                    # no key
        ("55555555-5555-4555-8555-555555555555", "sfr_good"),  # unknown deployment
        ("not-a-uuid", "sfr_good"),                # malformed id
    ],
)
def test_every_failure_is_the_same_401(client, accept_key, deployment_id, key):
    resp = client.post(
        f"/api/v1/report/{deployment_id}/issues",
        json=_bodies(),
        headers={"X-Report-Key": key},
    )
    assert resp.status_code == 401
    # Identical body: nothing distinguishes "wrong key" from "no such
    # deployment", so the endpoint cannot be walked to enumerate ids.
    assert resp.json() == {"detail": "invalid report key"}


def test_disabled_reporting_is_the_same_401(client, monkeypatch):
    """authenticate_deployment already filters on issue_reporting_enabled; the
    endpoint must not report that differently from a bad key."""
    monkeypatch.setattr(
        app_issues, "authenticate_deployment", lambda *a, **k: None
    )
    resp = client.post(URL, json=_bodies(), headers={"X-Report-Key": "sfr_good"})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid report key"}


# --- what the caller may and may not decide ---------------------------------


def test_org_and_project_are_never_taken_from_the_body(client, captured):
    """A valid key for one deployment must not be able to write against
    another org — so the ids the row is built from come from the deployment."""
    resp = client.post(
        URL,
        json={
            **_bodies(),
            "org_id": "99999999-9999-4999-8999-999999999999",
            "project_id": "88888888-8888-4888-8888-888888888888",
        },
        headers={"X-Report-Key": "sfr_good"},
    )
    assert resp.status_code == 201
    call = captured[0]
    assert call["deployment"] is DEPLOYMENT
    assert "org_id" not in call["payload"]
    assert "project_id" not in call["payload"]


def test_user_report_shape_reaches_the_service(client, captured):
    resp = client.post(
        URL,
        json={
            "source": "user_report",
            "title": "Checkout button does nothing",
            "message": "it just spins forever",
            "reporter_email": "someone@example.com",
        },
        headers={"X-Report-Key": "sfr_good"},
    )
    assert resp.status_code == 201
    payload = captured[0]["payload"]
    assert payload["source"] == "user_report"
    assert payload["reporter_email"] == "someone@example.com"


def test_rate_limit_answers_429(client, monkeypatch, accept_key):
    def _ingest(settings, deployment, payload):
        raise app_issues.RateLimited()

    monkeypatch.setattr(app_issues, "ingest_report", _ingest)
    resp = client.post(URL, json=_bodies(), headers={"X-Report-Key": "sfr_good"})
    assert resp.status_code == 429


def test_an_ingestion_failure_says_nothing_about_why(client, monkeypatch, accept_key):
    def _ingest(settings, deployment, payload):
        raise RuntimeError("relation public.app_issues does not exist")

    monkeypatch.setattr(app_issues, "ingest_report", _ingest)
    resp = client.post(URL, json=_bodies(), headers={"X-Report-Key": "sfr_good"})
    assert resp.status_code == 500
    assert "app_issues" not in resp.text


def test_browser_reporters_get_cors(client, captured):
    """The SDK and widget run on origins the factory has never heard of, so
    the configured allow-list would block every report before it was sent."""
    preflight = client.options(
        URL,
        headers={
            "Origin": "https://some-customer-app.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-report-key",
        },
    )
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == "*"
    assert "X-Report-Key" in preflight.headers["access-control-allow-headers"]

    resp = client.post(
        URL,
        json=_bodies(),
        headers={"X-Report-Key": "sfr_good", "Origin": "https://some-customer-app.example"},
    )
    assert resp.headers["access-control-allow-origin"] == "*"
    # No credentials on a wildcard origin — the browser must not attach cookies.
    assert "access-control-allow-credentials" not in resp.headers


# --- fingerprinting: what makes two crashes one row -------------------------


def test_the_same_crash_fingerprints_the_same():
    stack = "at handler (/app/routes.js:12:9)\nat next (/app/mw.js:4:1)"
    a = app_issues.compute_fingerprint("TypeError", "Cannot read x of undefined", stack)
    b = app_issues.compute_fingerprint("TypeError", "Cannot read x of undefined", stack)
    assert a == b


def test_variable_content_does_not_split_one_crash_into_many():
    """A crash loop whose message embeds a timestamp, a request uuid or a
    memory address is the case dedup exists for."""
    stack = "at handler (/app/routes.js:12:9)"
    first = app_issues.compute_fingerprint(
        "TypeError",
        "failed at 2026-07-28T10:00:00Z for 3f1a9c62-1f6e-4b0e-9a1d-2c8f5b7e4d31 at 0xdeadbeef",
        stack,
    )
    later = app_issues.compute_fingerprint(
        "TypeError",
        "failed at 2026-07-28T10:00:41Z for 91bb2d40-7c33-4a55-8f21-6de0a4c19b77 at 0xfeedface",
        stack,
    )
    assert first == later


def test_different_errors_do_not_collapse_together():
    stack = "at handler (/app/routes.js:12:9)"
    a = app_issues.compute_fingerprint("TypeError", "cannot read x", stack)
    b = app_issues.compute_fingerprint("RangeError", "cannot read x", stack)
    c = app_issues.compute_fingerprint("TypeError", "cannot read y", stack)
    d = app_issues.compute_fingerprint(
        "TypeError", "cannot read x", "at other (/app/thing.js:1:1)"
    )
    assert len({a, b, c, d}) == 4


def test_only_the_top_frames_count():
    """The tail of a stack is framework plumbing unrelated errors share."""
    head = "at handler (/app/routes.js:12:9)\nat next (/app/mw.js:4:1)\nat run (/app/x.js:1:1)"
    a = app_issues.compute_fingerprint("Error", "boom", head + "\nat serve (/node_modules/a.js:9:9)")
    b = app_issues.compute_fingerprint("Error", "boom", head + "\nat serve (/node_modules/b.js:1:1)")
    assert a == b


# --- caps: what a stranger can make the factory store -----------------------


def test_an_oversized_stack_trace_is_truncated_not_rejected():
    huge = "at frame (/app/x.js:1:1)\n" * 5000
    kept = app_issues._truncate(huge, app_issues.STACK_LIMIT)
    assert len(kept) < len(huge)
    # ...and says it was cut, rather than reading as a trace ending mid-frame.
    assert "truncated" in kept


def test_context_is_capped_and_still_parses():
    context = {"url": "https://x.example", "blob": "x" * 50_000}
    trimmed = app_issues._truncate_context(context)
    encoded = json.dumps(trimmed)
    assert len(encoded) <= app_issues.CONTEXT_LIMIT
    assert json.loads(encoded)["url"] == "https://x.example"


def test_a_non_object_context_is_kept_rather_than_dropped():
    assert app_issues._truncate_context("just a string") == {"value": "just a string"}
    assert app_issues._truncate_context(None) == {}


def test_the_rate_limiter_trips_at_the_documented_threshold():
    for _ in range(app_issues.RATE_LIMIT_PER_MINUTE):
        app_issues._check_rate("dep-1")
    with pytest.raises(app_issues.RateLimited):
        app_issues._check_rate("dep-1")
    # ...and it is per deployment, so one noisy app cannot silence another.
    app_issues._check_rate("dep-2")


# --- US-79.1 (prod BUG-1): the wiring check lands already resolved -----------


class _FakeConn:
    """Captures the insert without a database; the status parameter is the
    assertion target."""

    def __init__(self):
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.params = params
        return self

    def fetchone(self):
        return {"id": "66666666-6666-4666-8666-666666666666", "deduped": False}

    def commit(self):
        pass


def _ingest_with_context(monkeypatch, context):
    conn = _FakeConn()
    monkeypatch.setattr(app_issues, "_connect", lambda settings: conn)
    result = app_issues.ingest_report(
        None,
        DEPLOYMENT,
        {
            "source": "automated",
            "error_type": "WiringCheck",
            "message": "self-monitoring wiring verification",
            "context": context,
        },
    )
    assert result["deduped"] is False
    return conn


def test_a_wiring_check_lands_already_resolved(monkeypatch):
    """Proving the pipe is its whole job — it must never sit in the inbox
    styled like a crash, waiting to be promoted into a bug (prod BUG-1)."""
    conn = _ingest_with_context(monkeypatch, {"component": "verification"})
    assert conn.params[-1] == "ignored"


def test_every_other_automated_report_still_lands_new(monkeypatch):
    conn = _ingest_with_context(monkeypatch, {"component": "apps/api"})
    assert conn.params[-1] == "new"
