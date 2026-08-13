"""US-85.2: POST /api/v1/issues/batch-dispatch — one ordered request.

The 2026-08-12 incident this pins: the dashboard looped checkbox-CLICK order
(story 9 planned before 4–8) and a mid-batch failure or closed tab silently
truncated the rest. The batch is now server-ordered with per-item outcomes.
"""

import uuid

import pytest

from app.routers.issues import batch_order_key
from app.supabase import RpcError

PROJECT_ID = str(uuid.uuid4())


def _issue(sub_no, issue_id=None, item_no=1, epic=2):
    return {
        "id": issue_id or str(uuid.uuid4()),
        "status": "ready",
        "project_id": PROJECT_ID,
        "item_no": item_no,
        "sub_no": sub_no,
        "created_at": f"2026-08-12T11:02:{10 + (sub_no or 0):02d}+00:00",
        "epics": {"number": epic},
    }


def test_batch_order_key_sorts_build_order_nulls_last():
    rows = [_issue(9), _issue(2), _issue(None), _issue(5)]
    ordered = sorted(rows, key=batch_order_key)
    assert [r["sub_no"] for r in ordered] == [2, 5, 9, None]


def _wire(monkeypatch, rows, rpc_behavior):
    dispatched_order = []

    async def fake_get(settings, token, path, params):
        assert path == "issues"
        return rows

    async def fake_rpc(settings, token, fn, args):
        assert fn == "dispatch_issue"
        issue_id = args["p_issue"]
        dispatched_order.append(issue_id)
        return rpc_behavior(issue_id)

    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    # The docs-sync coroutine must be closed, not just recorded, or the test
    # run drowns in "never awaited" warnings.
    synced = []
    monkeypatch.setattr(
        "app.routers.issues.repo_docs.spawn_background",
        lambda coro: (synced.append(True), coro.close()),
    )
    return dispatched_order, synced


def test_batch_dispatches_in_build_order_not_request_order(
    client, make_token, monkeypatch
):
    a, b, c = _issue(9), _issue(2), _issue(5)
    order, synced = _wire(monkeypatch, [a, b, c], lambda i: str(uuid.uuid4()))

    resp = client.post(
        "/api/v1/issues/batch-dispatch",
        json={"issue_ids": [a["id"], b["id"], c["id"]]},  # click order: 9, 2, 5
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 202
    assert order == [b["id"], c["id"], a["id"]]  # build order: 2, 5, 9
    body = resp.json()
    assert [d["id"] for d in body["dispatched"]] == [b["id"], c["id"], a["id"]]
    assert body["skipped"] == []
    assert len(synced) == 1  # one docs sync for the one touched project


def test_a_refusal_never_aborts_the_batch(client, make_token, monkeypatch):
    a, b, c = _issue(1), _issue(2), _issue(3)

    def behavior(issue_id):
        if issue_id == b["id"]:
            raise RpcError("issue is not dispatchable from status \"running\"")
        return str(uuid.uuid4())

    order, _ = _wire(monkeypatch, [a, b, c], behavior)

    resp = client.post(
        "/api/v1/issues/batch-dispatch",
        json={"issue_ids": [a["id"], b["id"], c["id"]]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    # b is skipped with the dispatcher's own words; a and c both dispatched.
    assert [d["id"] for d in body["dispatched"]] == [a["id"], c["id"]]
    assert body["skipped"] == [
        {"id": b["id"], "reason": 'issue is not dispatchable from status "running"'}
    ]
    assert order == [a["id"], b["id"], c["id"]]  # c was still attempted


def test_unknown_ids_are_reported_not_dropped(client, make_token, monkeypatch):
    a = _issue(1)
    ghost = str(uuid.uuid4())
    _wire(monkeypatch, [a], lambda i: str(uuid.uuid4()))

    resp = client.post(
        "/api/v1/issues/batch-dispatch",
        json={"issue_ids": [ghost, a["id"]]},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert {"id": ghost, "reason": "not found"} in body["skipped"]
    assert [d["id"] for d in body["dispatched"]] == [a["id"]]


@pytest.mark.parametrize(
    "ids", [[], [str(uuid.uuid4()) for _ in range(101)]]
)
def test_empty_and_oversized_batches_are_422(client, make_token, ids):
    resp = client.post(
        "/api/v1/issues/batch-dispatch",
        json={"issue_ids": ids},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422
