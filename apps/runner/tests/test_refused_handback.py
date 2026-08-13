"""US-31.1: a refused hand-back is not a finished run.

On 2026-07-26 two agents had failure reports refused with a 500, logged
`run -> failed` anyway, and their runs sat `running` until the lease looped
them. These tests pin the new contract: retry with backoff, never log an
outcome the server refused, release the run, raise an incident, and never
lose command evidence to a dropped socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from supervisor import audit as audit_mod
from supervisor.audit import SocketAuditor, flush_pending
from supervisor.workloop import Supervisor, describe_refusal, sanitize_payload

# The literal body the 2026-07-28 plan batch was refused with, fifteen runs
# over — one identical error per test case in the payload (US-42.2).
BATCH_422 = json.dumps(
    {
        "detail": [
            {
                "type": "string_type",
                "loc": ["body", "test_cases", i, "steps"],
                "msg": "Input should be a valid string",
            }
            for i in range(15)
        ]
    }
)


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    """WorkerClient stand-in with a scripted submit."""

    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.submits = []
        self.releases = []

    async def submit(self, run_id, payload):
        self.submits.append(payload)
        status = self.statuses.pop(0) if self.statuses else 200
        if status == -1:
            raise ConnectionError("transport down")
        return FakeResponse(status, text=f"http {status}")

    async def release(self, run_id, note=""):
        self.releases.append((run_id, note))
        return FakeResponse(200)


class FakeConnection:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.notifications = []

    async def notify(self, method, params):
        if self.fail:
            raise ConnectionError("socket down")
        self.notifications.append((method, params))


def make_supervisor(client, connection=None):
    sup = Supervisor(client, config_provider=lambda: {}, connection=connection)
    return sup


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr("supervisor.workloop.SUBMIT_RETRY_DELAYS", (0, 0, 0))


def test_transient_500_is_retried_and_accepted():
    client = FakeClient([500, 200])
    sup = make_supervisor(client)
    accepted = run(sup._hand_back("r1", {"error": "boom"}))
    assert accepted is True
    assert len(client.submits) == 2
    assert client.releases == []  # delivered — nothing to release


def test_persistent_refusal_releases_and_raises_incident():
    conn = FakeConnection()
    client = FakeClient([500, 500, 500, 500])
    sup = make_supervisor(client, connection=conn)
    accepted = run(sup._hand_back("r1", {"error": "boom"}))
    assert accepted is False
    assert len(client.submits) == 4  # first try + 3 retries
    assert client.releases and client.releases[0][0] == "r1"
    methods = [m for m, _ in conn.notifications]
    assert "runner.incident" in methods
    incident = dict(conn.notifications)[("runner.incident")]
    assert "refused" in incident["message"]


def test_client_error_is_final_no_retry():
    client = FakeClient([409])
    sup = make_supervisor(client)
    accepted = run(sup._hand_back("r1", {"error": "boom"}))
    assert accepted is False
    assert len(client.submits) == 1  # a 4xx is an answer, not a flake


def test_transport_error_counts_as_retryable():
    client = FakeClient([-1, 200])
    sup = make_supervisor(client)
    assert run(sup._hand_back("r1", {"error": "x"})) is True


def test_payload_nul_bytes_are_stripped():
    cleaned = sanitize_payload(
        {"error": "bad\x00byte", "stories": [{"title": "a\x00b"}], "n": 3}
    )
    assert cleaned["error"] == "badbyte"
    assert cleaned["stories"][0]["title"] == "ab"
    assert cleaned["n"] == 3


def test_hand_back_sanitizes_before_sending():
    client = FakeClient([200])
    sup = make_supervisor(client)
    run(sup._hand_back("r1", {"error": "a\x00b"}))
    assert client.submits[0]["error"] == "ab"


# ---------------------------------------------- US-42.2: a legible refusal


def test_repeated_field_errors_collapse_to_one_fact():
    """Fifteen identical errors, one per list element, are one problem."""
    msg = describe_refusal(422, BATCH_422)
    assert msg.startswith(
        "submit refused (422): test_cases[].steps — Input should be a valid "
        "string (x15)"
    )


def test_the_call_and_status_lead_the_message():
    assert describe_refusal(409, '{"detail":"another worker holds this run"}') == (
        "submit refused (409): another worker holds this run"
    )


def test_distinct_fields_are_named_then_counted():
    body = json.dumps(
        {
            "detail": [
                {"loc": ["body", f"f{i}"], "msg": "bad"} for i in range(5)
            ]
        }
    )
    msg = describe_refusal(422, body)
    assert "f0 — bad; f1 — bad; f2 — bad; +2 more" in msg


def test_a_non_json_body_still_produces_a_message():
    msg = describe_refusal(500, "<html>502 Bad Gateway</html>")
    assert msg.startswith("submit refused (500): <html>502 Bad Gateway</html>")


def test_no_response_is_named_not_blank():
    assert describe_refusal(None, "").startswith("submit refused (no response):")


def test_truncation_never_cuts_mid_token():
    msg = describe_refusal(422, "word " * 400)
    assert msg.endswith("…")
    assert "wor…" not in msg  # a boundary, not a character count


def test_the_raw_body_is_kept_behind_the_summary():
    msg = describe_refusal(422, BATCH_422)
    assert "\nraw: " in msg
    assert "string_type" in msg  # the detail survives for whoever needs it


def test_incident_carries_the_legible_message():
    conn = FakeConnection()

    class Refusing(FakeClient):
        async def submit(self, run_id, payload):
            self.submits.append(payload)
            return FakeResponse(422, text=BATCH_422)

    client = Refusing([])
    sup = make_supervisor(client, connection=conn)
    assert run(sup._hand_back("r1", {"plan": "x"})) is False
    incident = dict(conn.notifications)[("runner.incident")]
    assert "test_cases[].steps" in incident["message"]
    assert incident["kind"] == "runner-fault"


def test_command_result_buffered_while_socket_down_then_flushed():
    audit_mod._PENDING.clear()
    down = FakeConnection(fail=True)
    auditor = SocketAuditor(down, run_id="r1")
    run(auditor.report("audit-1", 1, "the reason it died"))
    assert len(audit_mod._PENDING) == 1  # evidence kept, not dropped

    up = FakeConnection(fail=False)
    sent = run(flush_pending(up))
    assert sent == 1
    assert audit_mod._PENDING == audit_mod._PENDING.__class__(maxlen=200)
    method, payload = up.notifications[0]
    assert method == "command.result"
    assert payload["audit_id"] == "audit-1"
    assert payload["output"] == "the reason it died"
