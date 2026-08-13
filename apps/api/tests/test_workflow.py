"""US-2.3 / US-2.5: PRD draft + plan approve/send-back orchestration.
US-2.27: send-back dispatches a redraft. US-2.28: breakdown standing
instructions saved at PRD approval. US-2.33: breakdown dispatches a worker
run."""

import uuid

ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
ART_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())


def test_draft_prd_dispatches_a_run(client, make_token, monkeypatch):
    run_id = str(uuid.uuid4())

    async def fake_rpc(settings, token, fn, args):
        assert fn == "dispatch_prd_draft"
        assert args == {"p_issue": ISSUE_ID}
        return run_id

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/draft",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"run_id": run_id, "status": "queued"}


def test_draft_prd_wrong_status_is_409(client, make_token, monkeypatch):
    from app.supabase import RpcError

    async def fake_rpc(settings, token, fn, args):
        raise RpcError('cannot draft PRD from status "planning"')

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/draft",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_approve_plan_materializes_cases(client, make_token, monkeypatch):
    test_plan = (
        '## Test plan\n\n```json\n[{"title":"T1","steps":"1","expected_result":"ok",'
        '"test_types":["unit"],"environments":["dev"]}]\n```\n'
    )
    posts = []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    "id": ISSUE_ID,
                    "org_id": ORG_ID,
                    "project_id": PROJECT_ID,
                    "type": "bug",
                    "title": "Fix crash",
                    "body": "## Repro\nx\n\n## Expected\ny",
                    "acceptance_criteria": [],
                    "status": "plan-review",
                    "parent_id": None,
                    "epic_id": None,
                    "abandoned_at": None,
                }
            ]
        if path == "artifacts":
            return [
                {
                    "id": str(uuid.uuid4()),
                    "kind": "plan",
                    "content": "# Plan",
                    "version": 1,
                },
                {
                    "id": str(uuid.uuid4()),
                    "kind": "test_plan",
                    "content": test_plan,
                    "version": 1,
                },
            ]
        return []

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    patches = []

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/plan/approve",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "planned"
    assert data["materialized_test_cases"] == 1
    assert any(p[0] == "test_cases" for p in posts)
    assert any(p[0] == "approvals" for p in posts)
    # Re-approving a plan must supersede the prior agent set, not stack it.
    assert any(
        p[0] == "test_cases"
        and p[1].get("source") == "eq.agent"
        and p[2] == {"status": "abandoned"}
        for p in patches
    )


def test_approve_plan_resumes_a_half_applied_approval(client, make_token, monkeypatch):
    """A crash partway through approval (2026-08-10: a connect timeout on a
    test-case insert) leaves the artifacts approved but the issue still in
    plan-review. Clicking Approve again must finish the job, not 409."""
    test_plan = (
        '## Test plan\n\n```json\n[{"title":"T1","steps":"1","expected_result":"ok",'
        '"test_types":["unit"],"environments":["dev"]}]\n```\n'
    )
    plan_art, test_art = ART_ID, str(uuid.uuid4())
    posts = []
    patches = []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    "id": ISSUE_ID,
                    "org_id": ORG_ID,
                    "project_id": PROJECT_ID,
                    "type": "bug",
                    "title": "Fix crash",
                    "body": "",
                    "acceptance_criteria": [],
                    "status": "plan-review",
                    "parent_id": None,
                    "epic_id": None,
                    "abandoned_at": None,
                }
            ]
        if path == "artifacts":
            # The first click got this far and then died.
            assert params["status"] == "in.(draft,approved)"
            return [
                {
                    "id": plan_art,
                    "kind": "plan",
                    "content": "# Plan",
                    "version": 1,
                    "status": "approved",
                },
                {
                    "id": test_art,
                    "kind": "test_plan",
                    "content": test_plan,
                    "version": 1,
                    "status": "approved",
                },
            ]
        if path == "approvals":
            return [{"subject_id": plan_art}, {"subject_id": test_art}]
        return []

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/plan/approve",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "planned"
    # The unfinished half runs: cases materialize, the issue moves on.
    assert any(p[0] == "test_cases" for p in posts)
    assert any(
        p[0] == "issues" and p[2] == {"status": "planned"} for p in patches
    )
    # The finished half does not run twice.
    assert not any(p[0] == "artifacts" for p in patches)
    assert not any(p[0] == "approvals" for p in posts)


def test_approve_plan_resume_still_records_a_missing_approval(
    client, make_token, monkeypatch
):
    """The crash can land between the artifact patch and its approval row.
    Resuming must record the decision that was lost, not assume an approved
    artifact means an approval was already written."""
    posts = []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    "id": ISSUE_ID,
                    "org_id": ORG_ID,
                    "project_id": PROJECT_ID,
                    "type": "bug",
                    "title": "Fix crash",
                    "body": "",
                    "acceptance_criteria": [],
                    "status": "plan-review",
                    "parent_id": None,
                    "epic_id": None,
                    "abandoned_at": None,
                }
            ]
        if path == "artifacts":
            return [
                {
                    "id": ART_ID,
                    "kind": "plan",
                    "content": "# Plan",
                    "version": 1,
                    "status": "approved",
                }
            ]
        return []  # no approvals row survived

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        return [body]

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/plan/approve",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    approvals = [p for p in posts if p[0] == "approvals"]
    assert len(approvals) == 1
    assert approvals[0][1]["subject_id"] == ART_ID


def _prd_review_issue():
    return {
        "id": ISSUE_ID,
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "type": "feature",
        "title": "Big feature",
        "body": "the raw idea",
        "acceptance_criteria": [],
        "status": "prd-review",
        "parent_id": None,
        "epic_id": None,
        "abandoned_at": None,
    }


def test_send_back_prd_dispatches_redraft(client, make_token, monkeypatch):
    """US-2.27: send-back keeps its contract and additionally supersedes the
    draft and dispatches a new prd run."""
    run_id = str(uuid.uuid4())
    posts, patches, rpc_calls = [], [], []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [_prd_review_issue()]
        if path == "runs":
            return []
        if path == "artifacts":
            return [{"id": ART_ID}]
        return []

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    async def fake_rpc(settings, token, fn, args):
        rpc_calls.append((fn, args))
        return run_id

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)
    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/send-back",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"comment": "tighten the goals"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "prd-review", "run_id": run_id}
    assert rpc_calls == [("dispatch_prd_draft", {"p_issue": ISSUE_ID})]
    assert any(
        p[0] == "approvals" and p[1]["decision"] == "sent-back" for p in posts
    )
    assert any(
        p[0] == "issue_events" and p[1]["type"] == "prd-sent-back" for p in posts
    )
    # the sent-back draft is superseded at send-back time, not at next submit
    assert any(
        p[0] == "artifacts"
        and p[1] == {"id": f"eq.{ART_ID}"}
        and p[2] == {"status": "superseded"}
        for p in patches
    )


def test_send_back_prd_with_active_run_is_409(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [_prd_review_issue()]
        if path == "runs":
            return [{"id": str(uuid.uuid4()), "status": "queued"}]
        return []

    async def fake_rpc(settings, token, fn, args):
        raise AssertionError("must not double-dispatch")

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/send-back",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"comment": "please change X"},
    )
    assert resp.status_code == 409
    assert "already queued or running" in resp.json()["detail"]


def test_send_back_prd_dispatch_failure_is_recoverable(
    client, make_token, monkeypatch
):
    """A failed dispatch never fails the send-back: feedback is recorded, the
    draft is superseded, and the response says dispatch didn't happen."""
    from app.supabase import RpcError

    patches = []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [_prd_review_issue()]
        if path == "runs":
            return []
        if path == "artifacts":
            return [{"id": ART_ID}]
        return []

    async def fake_post(settings, token, path, body):
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    async def fake_rpc(settings, token, fn, args):
        raise RpcError("no such function")

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)
    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/send-back",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"comment": "please change X"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "prd-review"
    assert data["run_id"] is None
    assert data["dispatch_error"]
    assert any(p[2] == {"status": "superseded"} for p in patches)


def test_approve_prd_saves_standing_values(client, make_token, monkeypatch):
    """US-2.28: the dialog's mode + instructions persist on the feature and
    ride the approval row's payload for audit."""
    posts, patches = [], []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    **_prd_review_issue(),
                    "breakdown_mode": "automatic",
                    "breakdown_instructions": None,
                }
            ]
        if path == "artifacts":
            return [{"id": ART_ID, "version": 2}]
        return []

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/approve",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={
            "breakdown_mode": "single",
            "breakdown_instructions": "keep API and UI together",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["breakdown_mode"] == "single"
    issue_patch = next(p for p in patches if p[0] == "issues")
    assert issue_patch[2]["status"] == "ready"
    assert issue_patch[2]["breakdown_mode"] == "single"
    assert issue_patch[2]["breakdown_instructions"] == "keep API and UI together"
    approval = next(p[1] for p in posts if p[0] == "approvals")
    assert approval["payload"] == {
        "breakdown_mode": "single",
        "breakdown_instructions": "keep API and UI together",
    }


def test_approve_prd_without_body_keeps_standing_values(
    client, make_token, monkeypatch
):
    """A bare approve (or a pre-us-2.28 caller) re-approves with the
    feature's current standing values instead of resetting them."""
    posts, patches = [], []

    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    **_prd_review_issue(),
                    "breakdown_mode": "multiple",
                    "breakdown_instructions": "split by surface",
                }
            ]
        if path == "artifacts":
            return [{"id": ART_ID, "version": 1}]
        return []

    async def fake_post(settings, token, path, body):
        posts.append((path, body))
        return [{**body, "id": str(uuid.uuid4())}]

    async def fake_patch(settings, token, path, params, body):
        patches.append((path, params, body))
        return [body]

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.workflow.postgrest_post", fake_post)
    monkeypatch.setattr("app.routers.workflow.postgrest_patch", fake_patch)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/prd/approve",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    issue_patch = next(p for p in patches if p[0] == "issues")
    assert issue_patch[2]["breakdown_mode"] == "multiple"
    assert issue_patch[2]["breakdown_instructions"] == "split by surface"
    approval = next(p[1] for p in posts if p[0] == "approvals")
    assert approval["payload"]["breakdown_mode"] == "multiple"


def test_dispatch_breakdown_queues_a_run(client, make_token, monkeypatch):
    """US-2.33: breakdown is a worker run — the endpoint dispatches
    dispatch_breakdown into the pool, it no longer calls the LLM inline."""
    run_id = str(uuid.uuid4())

    async def fake_rpc(settings, token, fn, args):
        assert fn == "dispatch_breakdown"
        assert args == {"p_issue": ISSUE_ID}
        return run_id

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/breakdown/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"run_id": run_id, "status": "queued"}


def test_dispatch_breakdown_needs_approved_prd_is_409(
    client, make_token, monkeypatch
):
    from app.supabase import RpcError

    async def fake_rpc(settings, token, fn, args):
        raise RpcError("approved PRD required")

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/breakdown/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_dispatch_breakdown_children_exist_is_409(
    client, make_token, monkeypatch
):
    from app.supabase import RpcError

    async def fake_rpc(settings, token, fn, args):
        raise RpcError("feature already has children — use Add story instead")

    monkeypatch.setattr("app.routers.workflow.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/breakdown/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_send_back_plan_requires_comment(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "issues":
            return [
                {
                    "id": ISSUE_ID,
                    "org_id": ORG_ID,
                    "project_id": PROJECT_ID,
                    "type": "chore",
                    "title": "Bump deps",
                    "body": "do it",
                    "acceptance_criteria": [],
                    "status": "plan-review",
                    "parent_id": None,
                    "epic_id": None,
                    "abandoned_at": None,
                }
            ]
        return []

    monkeypatch.setattr("app.routers.workflow.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/plan/send-back",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={},
    )
    assert resp.status_code == 422
