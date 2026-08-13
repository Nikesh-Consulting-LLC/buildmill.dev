"""US-31.1: a failure report must land — never 500, never lost.

Endpoint-level: db is monkeypatched. Pins three things:
- inbound strings are NUL-stripped before anything touches the DB
  (the 2026-07-26 class: psycopg refuses 0x00 client-side, so one NUL in a
  CLI's output 500'd the submit and left the run `running` on its lease);
- when complete_run's full bookkeeping raises anyway, the failure is
  recorded through the minimal path and the endpoint still answers 200;
- prd, plan and code failure reports all land run-failed.
"""

import uuid

import pytest

from app.routers.worker import Submit, _strip_nul

RUN_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())

WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner (Claude Code)",
    "type": "autonomous",
    "status": "active",
}
HDR = {"X-Worker-Token": "sfw_testtoken"}


@pytest.fixture
def worker_auth(monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_by_token",
        lambda settings, token: dict(WORKER) if token == "sfw_testtoken" else None,
    )
    return WORKER


def _run_row(kind: str) -> dict:
    return {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": ISSUE_ID,
        "kind": kind,
        "status": "running",
        "worker_id": WORKER["id"],
    }


@pytest.fixture
def failed_run(monkeypatch):
    """A claimed running run of a parameterizable kind, with the failure
    side-effects stubbed."""
    state = {"kind": "plan", "completed": [], "minimal": []}

    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, rid, org: _run_row(state["kind"]),
    )

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        state["completed"].append({"outcome": outcome, "error": error, "stdout": stdout})
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    monkeypatch.setattr(
        "app.routers.worker.db.fail_run_minimal",
        lambda s, rid, err, worker_name=None: state["minimal"].append(err) or True,
    )
    # US-59.5: no open question on these runs — a real failure report.
    monkeypatch.setattr(
        "app.routers.worker.db.has_pending_clarification", lambda s, run: False
    )

    async def fake_push(settings, issue_id, st):
        return None

    monkeypatch.setattr(
        "app.routers.worker.issue_sync.push_issue_state_via_db", fake_push
    )
    return state


def test_strip_nul_is_recursive():
    assert _strip_nul("a\x00b") == "ab"
    assert _strip_nul({"k": ["x\x00", {"y": "\x00z"}]}) == {"k": ["x", {"y": "z"}]}
    assert _strip_nul(7) == 7


def test_submit_model_strips_nul_everywhere():
    s = Submit(error="dead\x00beef", stdout="out\x00put", stories=[{"title": "a\x00"}])
    assert s.error == "deadbeef"
    assert s.stdout == "output"
    assert s.stories == [{"title": "a"}]


@pytest.mark.parametrize("kind", ["prd", "plan", "code"])
def test_failure_report_with_nul_lands_200(client, worker_auth, failed_run, kind):
    failed_run["kind"] = kind
    r = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"error": "CLI died\x00 hard", "stdout": "tail\x00"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["issue_status"] == "failed"
    rec = failed_run["completed"][-1]
    assert rec["outcome"] == "failed"
    assert "\x00" not in rec["error"] and "\x00" not in rec["stdout"]


def test_bookkeeping_exception_falls_back_to_minimal_and_200s(
    client, worker_auth, failed_run, monkeypatch
):
    def exploding_complete(*a, **kw):
        raise ValueError("whatever complete_run tripped on")

    monkeypatch.setattr("app.routers.worker.db.complete_run", exploding_complete)
    r = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"error": "CLI died"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["issue_status"] == "failed"
    assert failed_run["minimal"] == ["CLI died"]
