"""US-10.11: a runner-fault submit records an incident + notifies managers;
a work-fault does not."""

import uuid

import pytest

RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner",
    "type": "autonomous",
    "status": "active",
}
HDR = {"X-Worker-Token": "sfw_tok"}


@pytest.fixture
def stubs(monkeypatch):
    state = {"incident": None, "notified": 0}
    run = {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": str(uuid.uuid4()),
        "worker_id": WORKER["id"],
        "status": "running",
        "kind": "code",
    }
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_by_token",
        lambda s, t: dict(WORKER) if t == "sfw_tok" else None,
    )
    monkeypatch.setattr("app.routers.worker.db.get_worker_run", lambda s, rid, org: dict(run))
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)
    # US-59.5: no open question on this run — a real failure report.
    monkeypatch.setattr(
        "app.routers.worker.db.has_pending_clarification", lambda s, run: False
    )

    async def fake_push(s, iid, st):
        return None

    monkeypatch.setattr("app.routers.worker.issue_sync.push_issue_state_via_db", fake_push)

    def rec(s, org, wid, run_id, kind, msg):
        state["incident"] = {"org": org, "worker": wid, "kind": kind, "msg": msg}
        return "inc-1"

    monkeypatch.setattr("app.routers.worker.db.record_runner_incident", rec)

    def notif(s, org, typ, payload):
        state["notified"] += 1
        return 2

    monkeypatch.setattr("app.routers.worker.db.notify_org_managers", notif)
    return state


def test_runner_fault_records_incident_and_notifies(client, stubs):
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"error": "git clone failed", "fault_class": "runner-fault"},
    )
    assert resp.status_code == 200
    assert resp.json()["issue_status"] == "failed"
    assert stubs["incident"]["kind"] == "runner-fault"
    assert stubs["notified"] == 1


def test_work_fault_records_no_incident(client, stubs):
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"error": "assertion failed in logic", "fault_class": "work-fault"},
    )
    assert resp.status_code == 200
    assert stubs["incident"] is None
    assert stubs["notified"] == 0
