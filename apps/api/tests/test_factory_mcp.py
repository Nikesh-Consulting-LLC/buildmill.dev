"""US-3.3: factory MCP server — auth rejection and tool → handler
delegation (the behavior itself is tested in the us-3.2 suites)."""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "MCP tester",
    "type": "human",
    "status": "active",
}
HDR = {
    "X-Worker-Token": "sfw_testtoken",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


# US-80.1: the MCP tools read and write through a real database; the fakes stop at the transport, so this is Full QA (--full).
pytestmark = pytest.mark.needs_db

def _rpc(method, params=None, id=1):
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


@pytest.fixture(scope="module")
def mcp_client():
    """One lifespan-running client for the module — the MCP session
    manager can only start once per process."""
    mp = pytest.MonkeyPatch()
    # lifespan runs at client entry — keep the reapers off the live DB
    mp.setattr("app.main.deploy.reap_orphaned_runs", lambda s: 0)
    mp.setattr("app.main.db.reap_orphaned_provider_runs", lambda s: 0)
    mp.setattr(
        "app.factory_mcp.db.get_worker_by_token",
        lambda s, t: dict(WORKER) if t == "sfw_testtoken" else None,
    )
    # US-7.3: the branch resolver + branch_ref writer hit the DB; stub them to
    # the deterministic legacy name and a no-op so the MCP tools stay unit-safe.
    mp.setattr(
        "app.factory_mcp.db.resolve_working_branch",
        lambda s, run: (
            f"factory/issue-{run.get('issue_id')}",
            (run.get("dev_branch_strategy") or "story"),
            "direct" if run.get("dev_branch_strategy") == "main" else "pr",
        ),
    )
    mp.setattr("app.factory_mcp.db.set_run_branch_ref", lambda s, rid, b: None)
    # us-110.1: the no-claim reads default their project from the grant list.
    # Default to "granted several" — no default, argument required — so a test
    # that wants the sole-grant path opts into it with _worker_with_sole_project.
    mp.setattr("app.factory_mcp.db.sole_granted_project", lambda s, w: None)

    # US-7.9: build-config fetch hits Storage; default to none.
    async def _no_build_config(s, org, proj):
        return {}

    mp.setattr(
        "app.factory_mcp.build_config.fetch_build_config_values", _no_build_config
    )
    from app.main import app

    with TestClient(app) as c:
        yield c
    mp.undo()


def test_mcp_rejects_missing_and_bad_tokens(mcp_client):
    resp = mcp_client.post("/mcp", json=_rpc("tools/list"))
    assert resp.status_code == 401

    resp = mcp_client.post(
        "/mcp", json=_rpc("tools/list"), headers=HDR | {"X-Worker-Token": "bad"}
    )
    assert resp.status_code == 401


def test_all_tools_listed(mcp_client):
    resp = mcp_client.post("/mcp", json=_rpc("tools/list"), headers=HDR)
    assert resp.status_code == 200
    for tool in (
        "list_available_work",
        "list_factory_queue",
        "list_my_work",
        "report_progress",
        "request_clarification",
        "get_clarifications",
        "claim_work",
        "get_work_context",
        "get_repo_tree",
        "read_repo_file",
        "get_workspace",
        "validate_submission",
        "get_instructions",
        "add_comment",
        "get_project_guidelines",
        "get_project_learnings",
        "submit_learning",
        "list_project_documents",
        "get_document",
        "submit_plan",
        "submit_code_work",
        "submit_changeset",
        "submit_prd",
        "report_test_results",
        "get_run_status",
        "get_pr_status",
        "release_work",
        # us-98.5: a merge that cannot finish must have a way to say so —
        # before this, an MCP agent could only release the claim silently.
        "report_merge_failure",
        "get_instruction_file",
    ):
        assert tool in resp.text


# The pure reads — clients may call these speculatively (prefetch, poll).
_READ_ONLY_TOOLS = {
    "get_instruction_file",
    "list_available_work",
    "list_factory_queue",
    "list_my_work",
    "get_work_context",
    "get_context_detail",
    "get_deployment_run_status",
    "get_deployment_health",
    "get_repo_tree",
    "read_repo_file",
    # US-64.x: no-claim reads — same shape, keyed by project_id.
    "get_project_tree",
    "read_project_file",
    "get_project_workspace",
    # US-63.1: reads the release's commit range; writes nothing but the
    # lease extension every run-scoped read tool performs.
    "get_release_changes",
    # US-63.3: lists the pool, claims nothing.
    "list_release_prep_work",
    "get_workspace",
    "validate_submission",
    "get_instructions",
    "get_project_guidelines",
    "get_project_learnings",
    "get_run_status",
    "get_pr_status",
    "list_project_documents",
    "get_document",
    "get_clarifications",
}


def test_all_tools_carry_annotations(mcp_client):
    """US-5.10: every listed tool declares a title and an accurate
    readOnlyHint — the regression guard so new tools don't ship
    unannotated. Mutating tools must not claim idempotency (a
    report_progress call, for one, extends the lease every time)."""
    resp = mcp_client.post("/mcp", json=_rpc("tools/list"), headers=HDR)
    assert resp.status_code == 200
    tools = resp.json()["result"]["tools"]
    assert len(tools) >= 19
    listed = {t["name"] for t in tools}
    assert _READ_ONLY_TOOLS <= listed
    for tool in tools:
        ann = tool.get("annotations")
        assert ann, f"{tool['name']} shipped without annotations"
        assert ann.get("title"), f"{tool['name']} has no title annotation"
        expected_read_only = tool["name"] in _READ_ONLY_TOOLS
        assert ann.get("readOnlyHint") is expected_read_only, (
            f"{tool['name']} readOnlyHint should be {expected_read_only}"
        )
        if not expected_read_only:
            assert ann.get("destructiveHint") is False, tool["name"]
            assert ann.get("idempotentHint") is False, tool["name"]


def test_server_instructions_match_final_surface(mcp_client):
    """US-5.10: the initialize instructions cover the full loop — pre-claim
    peek, heartbeat, clarifications, post-submit follow-up — and the scope
    paragraph denies nothing the server now exposes."""
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    instructions = resp.json()["result"]["instructions"]
    for phrase in (
        "get_instructions",
        "report_progress",
        "request_clarification",
        "get_run_status",
        "submit_learning",
        "retry of run",
    ):
        assert phrase in instructions, phrase
    # the old scope text denied PR status and submitted-run visibility —
    # both are exposed now and must not be denied
    assert "does not expose its status afterward" not in instructions
    assert "no search tool" in instructions
    # US-5.26: both code hand-back transports are described
    assert "submit_changeset" in instructions
    assert "get_workspace" in instructions
    assert "submit_code_work" in instructions
    assert "get_pr_status" in instructions


def _pool_stub(captured):
    def stub(s, w):
        captured["called"] = True
        return [
            {
                "id": "run-1",
                "kind": "code",
                "issue_id": "issue-1",
                "issue_title": "Add CSV export",
                "issue_type": "story",
                "project_id": PROJECT_ID,
                "project_name": "Webshop",
                "repo_full_name": "acme/webshop",
            }
        ]

    return stub


def test_list_available_work_delegates(mcp_client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_pool", _pool_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_available_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Add CSV export" in resp.text
    assert "run-1" in resp.text


def _queue_stub(captured, rows=None):
    def stub(s, org_id):
        captured["org_id"] = org_id
        return rows if rows is not None else [
            {
                "id": "run-1",
                "kind": "code",
                "status": "queued",
                "queue_rank": None,
                "paused_at": None,
                "issue_id": "issue-1",
                "issue_title": "Add CSV export",
                "issue_type": "story",
                "item_no": 4,
                "sub_no": 1,
                "epic_number": 1,
                "epic_title": "Exports",
                "parent_id": "feature-1",
                "project_id": PROJECT_ID,
                "project_name": "Webshop",
                "worker_name": None,
                "held": False,
                "held_sibling_count": 0,
                "last_tool": None,
                "last_at": None,
            }
        ]

    return stub


def test_list_factory_queue_delegates(mcp_client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_factory_queue", _queue_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_factory_queue", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Add CSV export" in resp.text
    assert "US-1.4.1" in resp.text
    assert "queued" in resp.text


def test_list_factory_queue_marks_held_paused_running(mcp_client, monkeypatch):
    rows = [
        {
            "id": "run-running",
            "kind": "code",
            "status": "running",
            "queue_rank": None,
            "paused_at": None,
            "issue_id": "issue-a",
            "issue_title": "In flight",
            "issue_type": "story",
            "item_no": 1,
            "sub_no": 1,
            "epic_number": 1,
            "epic_title": "Epic",
            "parent_id": None,
            "project_name": "Webshop",
            "worker_name": "worker-x",
            "hold_reason": None,
            "last_tool": None,
            "last_at": None,
        },
        {
            "id": "run-paused",
            "kind": "plan",
            "status": "queued",
            "queue_rank": 1,
            "paused_at": "2026-07-23T00:00:00Z",
            "issue_id": "issue-b",
            "issue_title": "Paused item",
            "issue_type": "story",
            "item_no": 2,
            "sub_no": 1,
            "epic_number": 1,
            "epic_title": "Epic",
            "parent_id": None,
            "project_name": "Webshop",
            "worker_name": None,
            "hold_reason": None,
            "last_tool": None,
            "last_at": None,
        },
        {
            "id": "run-held",
            "kind": "code",
            "status": "queued",
            "queue_rank": None,
            "paused_at": None,
            "issue_id": "issue-c",
            "issue_title": "Held item",
            "issue_type": "story",
            "item_no": 3,
            "sub_no": 1,
            "epic_number": 1,
            "epic_title": "Epic",
            "parent_id": "feature-1",
            "project_name": "Webshop",
            "worker_name": None,
            "hold_reason": "waiting on sibling stories to be approved",
            "last_tool": None,
            "last_at": None,
        },
    ]
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_factory_queue", _queue_stub(captured, rows)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_factory_queue", "arguments": {}}),
        headers=HDR,
    )
    text = resp.text
    assert "worker-x" in text  # holder shown for the running run
    assert "paused by the manager" in text
    assert "waiting on sibling stories" in text
    assert '"claimable":false' in text
    assert '"claimable":true' not in text  # none of these three are claimable


# --------------------------------------------- US-5.1: list_my_work


def _my_work_stub(captured, claimed=None, submitted=None):
    def stub(s, w):
        captured["worker_id"] = w["id"]
        return {"claimed": claimed or [], "submitted": submitted or []}

    return stub


def test_list_my_work_shows_claims_and_submissions(mcp_client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_runs",
        _my_work_stub(
            captured,
            claimed=[
                {
                    "id": "run-c",
                    "kind": "code",
                    "claim_expires_at": "2026-07-17 12:00",
                    "issue_title": "Add CSV export",
                    "project_name": "Webshop",
                }
            ],
            submitted=[
                {
                    "id": "run-s",
                    "kind": "plan",
                    "finished_at": "2026-07-16 09:00",
                    "issue_title": "Fix login",
                    "issue_status": "needs-fixes",
                    "project_name": "Webshop",
                }
            ],
        ),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_my_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["worker_id"] == WORKER["id"]
    assert captured["project_id"] is None
    assert "Add CSV export" in resp.text
    assert "run-c" in resp.text
    assert "get_work_context" in resp.text  # resume nudge
    assert "Fix login" in resp.text
    assert "run-s" in resp.text
    assert "needs-fixes" in resp.text or "rejected" in resp.text


def test_list_my_work_empty_cases_are_not_errors(mcp_client, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_runs", _my_work_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_my_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "hold no claimed work" in resp.text
    assert "No recent submissions" in resp.text
    assert '"error"' not in resp.text


def test_list_my_work_is_every_project_this_worker_holds(mcp_client, monkeypatch):
    """us-110.1: what you hold is what you hold. The recovery view used to be
    narrowed by the MCP scope, which could hide a live claim from the agent
    that owned it."""
    import inspect

    from app import db as real_db

    assert list(
        inspect.signature(real_db.list_worker_runs).parameters
    ) == ["settings", "worker"]

    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_runs", _my_work_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_my_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["worker_id"] == WORKER["id"]


# ----------------------------------------- US-5.2: report_progress


def _claimed_run(run_id, worker_id=None):
    return {
        "id": run_id,
        "org_id": WORKER["org_id"],
        "issue_id": str(uuid.uuid4()),
        "worker_id": worker_id or WORKER["id"],
        "status": "running",
        "kind": "code",
    }


def test_report_progress_extends_lease_and_records_note(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id),
    )

    def fake_extend(s, r, w, tool=None):
        captured["extended"] = (r, w)
        return {"id": r, "claim_expires_at": "2026-07-17 15:00"}

    monkeypatch.setattr("app.factory_mcp.db.extend_claim", fake_extend)

    def fake_note(s, run, worker, note):
        captured["note"] = note

    monkeypatch.setattr("app.factory_mcp.db.record_progress_note", fake_note)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "report_progress",
                "arguments": {"run_id": run_id, "note": "tests passing, wiring UI"},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["extended"] == (run_id, WORKER["id"])
    assert captured["note"] == "tests passing, wiring UI"
    assert "2026-07-17 15:00" in resp.text  # the agent can plan its next beat


def test_report_progress_without_note_skips_recording(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.extend_claim",
        lambda s, r, w, tool=None: {"id": r, "claim_expires_at": "2026-07-17 15:00"},
    )

    def never_note(s, run, worker, note):
        raise AssertionError("no note should be recorded")

    monkeypatch.setattr("app.factory_mcp.db.record_progress_note", never_note)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "report_progress", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Lease extended" in resp.text


def test_report_progress_rejects_non_holder(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id, worker_id=str(uuid.uuid4())),
    )

    def never_extend(s, r, w, tool=None):
        raise AssertionError("must not extend someone else's claim")

    monkeypatch.setattr("app.factory_mcp.db.extend_claim", never_extend)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "report_progress", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "you do not hold this run" in resp.text


# ---------------------------------------- US-3.22: get_project_guidelines


def test_get_project_guidelines_requires_project_id_on_org_wide_url(
    mcp_client, monkeypatch
):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call", {"name": "get_project_guidelines", "arguments": {}}
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "no project specified" in resp.text


def test_get_project_guidelines_explicit_project_id(mcp_client, monkeypatch):
    captured: dict = {}

    def stub(s, project_id, org_id):
        captured["project_id"] = project_id
        captured["org_id"] = org_id
        return {"name": "Webshop", "guidelines": "## Conventions\n\nUse tabs."}

    monkeypatch.setattr("app.factory_mcp.db.get_project_guidelines_md", stub)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_guidelines",
                "arguments": {"project_id": PROJECT_ID},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Use tabs" in resp.text
    assert captured["project_id"] == PROJECT_ID
    assert captured["org_id"] == ORG_ID


def test_get_project_guidelines_unknown_project_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md", lambda s, p, o: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_guidelines",
                "arguments": {"project_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "project not found" in resp.text


def test_get_project_guidelines_empty_is_not_an_error(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md",
        lambda s, p, o: {"name": "Webshop", "guidelines": ""},
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_guidelines",
                "arguments": {"project_id": PROJECT_ID},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "no guidelines configured yet" in resp.text
    assert '"error"' not in resp.text


def test_get_project_guidelines_defaults_to_the_sole_granted_project(
    mcp_client, monkeypatch
):
    _worker_with_sole_project(monkeypatch, PROJECT_ID)
    captured: dict = {}

    def stub(s, project_id, org_id):
        captured["project_id"] = project_id
        return {"name": "Webshop", "guidelines": "## Conventions"}

    monkeypatch.setattr("app.factory_mcp.db.get_project_guidelines_md", stub)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call", {"name": "get_project_guidelines", "arguments": {}}
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["project_id"] == PROJECT_ID


# ---------------------------------------- US-5.3: get_project_learnings


def test_get_project_learnings_requires_project_id_on_org_wide_url(mcp_client):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call", {"name": "get_project_learnings", "arguments": {}}
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "no project specified" in resp.text


def test_get_project_learnings_explicit_project_id(mcp_client, monkeypatch):
    captured: dict = {}

    def stub(s, project_id, org_id):
        captured["project_id"] = project_id
        captured["org_id"] = org_id
        return {"name": "Webshop", "learnings": "## Gotchas\n\nBuild needs Node 22."}

    monkeypatch.setattr("app.factory_mcp.db.get_project_learnings_md", stub)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_learnings",
                "arguments": {"project_id": PROJECT_ID},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Build needs Node 22" in resp.text
    assert captured["project_id"] == PROJECT_ID
    assert captured["org_id"] == ORG_ID


def test_get_project_learnings_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_learnings_md", lambda s, p, o: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_learnings",
                "arguments": {"project_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "project not found" in resp.text


def test_get_project_learnings_empty_is_not_an_error(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_learnings_md",
        lambda s, p, o: {"name": "Webshop", "learnings": ""},
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_project_learnings",
                "arguments": {"project_id": PROJECT_ID},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "no learnings recorded yet" in resp.text
    assert '"error"' not in resp.text


def test_get_project_learnings_defaults_to_the_sole_granted_project(
    mcp_client, monkeypatch
):
    _worker_with_sole_project(monkeypatch, PROJECT_ID)
    captured: dict = {}

    def stub(s, project_id, org_id):
        captured["project_id"] = project_id
        return {"name": "Webshop", "learnings": "## Gotchas"}

    monkeypatch.setattr("app.factory_mcp.db.get_project_learnings_md", stub)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call", {"name": "get_project_learnings", "arguments": {}}
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["project_id"] == PROJECT_ID


# ------------------------------------------- US-5.6: submit_learning


def _stub_learning_pipeline(monkeypatch, captured):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_learnings_md",
        lambda s, p, o: {"name": "Webshop", "learnings": "## Old"},
    )

    # US-5.31: NOTHING may merge or write the document at submit time.
    async def never_merge(*a, **k):
        raise AssertionError("submit must not run the LLM merge")

    monkeypatch.setattr(
        "app.factory_mcp.llm.merge_learnings_as_org", never_merge
    )

    def never_upsert(*a, **k):
        raise AssertionError("submit must not write the learnings document")

    monkeypatch.setattr(
        "app.factory_mcp.db.upsert_project_learnings", never_upsert
    )

    def fake_record(s, worker, org, p, text):
        captured.setdefault("queued", (worker["name"], org, p, text))
        return "sub-123"

    monkeypatch.setattr(
        "app.factory_mcp.db.record_learning_submission", fake_record
    )


def test_submit_learning_queues_pending(mcp_client, monkeypatch):
    """US-5.31: submit queues for the manager's review — no LLM call, no
    document write, and no LLM-not-configured early error at submit."""
    captured: dict = {}
    _stub_learning_pipeline(monkeypatch, captured)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_learning",
                "arguments": {
                    "project_id": PROJECT_ID,
                    "text": "The build fails unless Node 22 is active.",
                },
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert payload["status"] == "pending"
    assert payload["submission_id"] == "sub-123"
    assert "queued for the manager's review" in payload["markdown"]
    assert captured["queued"] == (
        WORKER["name"],
        ORG_ID,
        PROJECT_ID,
        "The build fails unless Node 22 is active.",
    )


def test_submit_learning_requires_project_scope(mcp_client, monkeypatch):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "submit_learning", "arguments": {"text": "A discovery."}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "no project specified" in resp.text

    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_learnings_md", lambda s, p, o: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_learning",
                "arguments": {
                    "project_id": str(uuid.uuid4()),
                    "text": "A discovery.",
                },
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "project not found" in resp.text


def test_submit_learning_rejects_empty_and_oversize(mcp_client, monkeypatch):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_learning",
                "arguments": {"project_id": PROJECT_ID, "text": "   "},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "text is required" in resp.text

    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_learning",
                "arguments": {"project_id": PROJECT_ID, "text": "x" * 5000},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "too long" in resp.text


# (US-5.31 removed the LLM-not-configured early error at submit — the
# merge, and therefore that failure mode, moved to approval time.)


# ------------------------------------------------- single-endpoint scoping
# (supersedes the old /mcp/<org-shortname>[/<project-slug>] URL scheme —
# scope now lives on the worker token itself, via workers.project_id.)


def _worker_with_sole_project(monkeypatch, project_id):
    """us-110.1: a worker granted exactly one project. That — not a scope
    column — is what makes project_id optional on the no-claim reads."""
    monkeypatch.setattr(
        "app.factory_mcp.db.sole_granted_project", lambda s, w: project_id
    )


def test_extra_path_segments_after_mcp_is_404(mcp_client):
    resp = mcp_client.post(
        "/mcp/anything",
        json=_rpc("tools/list"),
        headers=HDR,
    )
    assert resp.status_code == 404


def test_the_pool_is_not_narrowed_by_any_second_scope(mcp_client, monkeypatch):
    """us-110.1: list_worker_pool takes no project argument any more. The
    capability grant list is the only project filter, applied inside it — a
    second one is what used to hide a granted project's runs from the agent
    that was granted them."""
    import inspect

    from app import db as real_db

    assert list(
        inspect.signature(real_db.list_worker_pool).parameters
    ) == ["settings", "worker"]

    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_pool", _pool_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_available_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["called"] is True


def test_available_work_carries_the_project_id(mcp_client, monkeypatch):
    """us-110.1: with no scope to default from, a worker granted several
    projects learns a project_id here — so the no-claim reads have one to
    pass. The name alone was not addressable."""
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_pool", _pool_stub(captured)
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_available_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert PROJECT_ID in resp.text


def test_claim_is_gated_only_by_the_capability_refusal(mcp_client, monkeypatch):
    """us-110.1: the out-of-scope refusal is gone. A run this worker is
    allowed to take is taken, whichever granted project it belongs to."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.worker_run_refusal", lambda s, w, r: None
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.claim_run",
        lambda s, r, w: {
            "id": r,
            "kind": "code",
            "claim_expires_at": "2026-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_run_activity", lambda s, r, t: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call", {"name": "claim_work", "arguments": {"run_id": run_id}}
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Claimed code run" in resp.text
    assert "project-scoped" not in resp.text


def test_no_refusal_still_recommends_the_retired_mcp_url(mcp_client):
    """The /mcp/<org-shortname>/<project-slug> form 404s (migration 216
    superseded it), yet ten refusals went on telling agents to use it. A
    refusal that names an impossible cure is worse than no hint."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "app" / "factory_mcp.py"
    text = src.read_text(encoding="utf-8")
    assert "<project-slug>" not in text
    assert "project-scoped MCP url" not in text


def test_claim_race_lost_is_actionable_not_error(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.worker_allowed_for_run", lambda s, w, r: True
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.claim_run", lambda s, r, w: None
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: {"id": r, "status": "running"},
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "claim_work", "arguments": {"run_id": str(uuid.uuid4())}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "someone else took it" in resp.text
    assert "list_available_work" in resp.text  # actionable next step


def _stub_work_context(
    monkeypatch,
    run_id,
    run_commands=None,
    extra_context=None,
    environment=None,
    kind="code",
):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: {
            "id": r,
            "org_id": WORKER["org_id"],
            "issue_id": str(uuid.uuid4()),
            "worker_id": WORKER["id"],
            "status": "running",
            "kind": kind,
            "project_id": str(uuid.uuid4()),
            "issue_title": "Add CSV export",
            "issue_type": "story",
            "epic_number": 1,
            "item_no": 4,
            "sub_no": 1,
            "project_summary": "A tool for exporting reports.",
            "org_shortname": "acme",
            "project_slug": "webshop",
            # US-13.2: the project row carries the repo, whatever the
            # run's frozen context says.
            "project_repo_full_name": "acme/webshop",
            "default_branch": "main",
            "instruction_set": "Ship the CSV export with a rollout note.",
            "input_context": {
                "story": "As a user…",
                "acceptance_criteria": [],
                **(extra_context or {}),
            },
        },
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_instruction",
        lambda s, p, k, issue_id=None: "Run the linter before submitting.",
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_commands_section",
        lambda s, p: run_commands,
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_environment",
        lambda s, p: environment,
    )
    # US-7.2: environment websites default to none in unit context.
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_environment_websites",
        lambda s, p: {},
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.list_run_documents", lambda s, r: []
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.list_issue_comments_for_run",
        lambda s, r, org: [
            {
                "id": "c1",
                "author": "Kaushlesh",
                "author_kind": "user",
                "body": "Ship the header row too.",
                "created_at": "2026-07-17 12:00",
            }
        ],
    )


def test_get_work_context_code_leads_with_mcp_handback(mcp_client, monkeypatch):
    """US-5.28: code-run context opens with the MCP-only hand-back loop —
    workspace → work → changeset → results — BEFORE the branch/remote
    lines, which stay as the labeled git-native alternative."""
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    md = payload["markdown"]
    handback = md.index("Hand-back (MCP-only")
    remote_line = md.index("Factory git remote")
    assert handback < remote_line
    # The loop's four steps, in order.
    order = [
        md.index("get_workspace"),
        md.index("submit_changeset"),
        md.index("report_test_results"),
    ]
    assert order == sorted(order)
    assert "Git-native alternative" in md
    assert md.index("Git-native alternative") < remote_line
    # Structured fields keep their names and meaning (no breaking change).
    assert payload["branch_name"].startswith("factory/issue-")
    assert "/git/acme/webshop.git" in payload["git_remote_url"]


def test_work_context_phase7_fields(mcp_client, monkeypatch):
    """US-7.15: a code run's context carries the readable id, project summary,
    submit mode, and branching strategy."""
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id, kind="code")
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["work_item_id"] == "US-1.4.1"
    assert payload["project_summary"] == "A tool for exporting reports."
    assert payload["submit_mode"] == "pr"
    assert payload["dev_branch_strategy"] == "story"
    assert "US-1.4.1" in payload["markdown"]
    assert "## Project summary" in payload["markdown"]


def test_build_config_present_on_code_absent_on_plan(mcp_client, monkeypatch):
    """US-7.9: build config is injected into a CODE run's context (the owning
    project's, delivered by fetch_build_config_values keyed on the claimed
    run's project) and absent from a plan run."""
    async def _values(s, org, proj):
        return {"TEST_DB_URL": "postgres://x", "API_KEY": "k"}

    monkeypatch.setattr(
        "app.factory_mcp.build_config.fetch_build_config_values", _values
    )

    code_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, code_id, kind="code")
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": code_id}},
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["build_config"] == {"TEST_DB_URL": "postgres://x", "API_KEY": "k"}
    assert "## Build configuration" in payload["markdown"]
    # names surface in the markdown, values do not
    assert "TEST_DB_URL" in payload["markdown"]
    assert "postgres://x" not in payload["markdown"]

    plan_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, plan_id, kind="plan")
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": plan_id}},
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    # plan runs never carry build config
    assert not payload.get("build_config")
    assert "## Build configuration" not in payload["markdown"]


def test_get_work_context_plan_points_at_repo_browsing(mcp_client, monkeypatch):
    """US-5.28: plan-run context steers at get_repo_tree/read_repo_file
    instead of implying a checkout; no hand-back block (plans submit
    markdown, not changesets)."""
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id, kind="plan")
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    md = payload["markdown"]
    assert "get_repo_tree" in md and "read_repo_file" in md
    assert "no clone or checkout is needed" in md
    assert "Hand-back (MCP-only" not in md
    # Structured branch/remote stay available for the code run that follows.
    assert payload["branch_name"].startswith("factory/issue-")


def test_get_work_context_includes_live_instructions(mcp_client, monkeypatch):
    """US-5.14: the MCP context carries the project's instruction template —
    an `instructions` field plus an Instructions section in the markdown."""
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "## Instructions" in resp.text
    assert "Run the linter before submitting." in resp.text
    # US-5.11: the item's living instruction set rides along too
    assert "## Instruction set" in resp.text
    assert "Ship the CSV export with a rollout note." in resp.text
    # US-5.12: the comment thread is part of the context
    assert "## Discussion" in resp.text
    assert "Ship the header row too." in resp.text
    # US-5.9: no Run commands section when the project hasn't declared any
    assert "## Run commands" not in resp.text


def test_get_work_context_surfaces_run_commands(mcp_client, monkeypatch):
    """US-5.9: the project's declared run commands appear prominently, with
    wording that tells the agent to verify before submitting."""
    run_id = str(uuid.uuid4())
    _stub_work_context(
        monkeypatch,
        run_id,
        run_commands="- Build: `npm run build`\n- Test: `npm test`",
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "## Run commands" in resp.text
    assert "npm run build" in resp.text
    assert "verify your work before submitting" in resp.text


# ------------------------------------ US-5.7: test cases in the context


def test_get_work_context_surfaces_environment(mcp_client, monkeypatch):
    """US-5.23: structured environment object (run commands folded in as
    an ordered list) plus the rendered markdown by the branch block."""
    run_id = str(uuid.uuid4())
    _stub_work_context(
        monkeypatch,
        run_id,
        run_commands="- `npm run build`\n- `npm test`",
        environment={
            "runtime": "Node 22",
            "setup_commands": ["npm install"],
            "notes": "copy .env.example to .env",
            "markdown": (
                "- Runtime: Node 22\n\nSetup, in order:\n- `npm install`"
                "\n\ncopy .env.example to .env"
            ),
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert "## Environment" in payload["markdown"]
    assert "Node 22" in payload["markdown"]
    env = payload["environment"]
    assert env["runtime"] == "Node 22"
    assert env["setup_commands"] == ["npm install"]
    assert env["notes"] == "copy .env.example to .env"
    assert env["run_commands"] == ["npm run build", "npm test"]


def test_get_work_context_environment_absent_when_empty(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert "environment" not in payload
    assert "## Environment" not in payload["markdown"]


def test_get_work_context_renders_test_cases(mcp_client, monkeypatch):
    """US-5.7: the issue's test cases appear as a section an agent can
    turn into automated tests — title, steps, expected result."""
    run_id = str(uuid.uuid4())
    _stub_work_context(
        monkeypatch,
        run_id,
        extra_context={
            "test_cases": [
                {
                    "id": str(uuid.uuid4()),
                    "title": "Export downloads a CSV",
                    "steps": "1. Open a project\n2. Click Export",
                    "expected_result": "A CSV file downloads with a header row.",
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "Empty project exports headers only",
                    "steps": "",
                    "expected_result": "The CSV has the header row and no data rows.",
                },
            ]
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    # US-13.5: the brief carries ids + titles; steps and expected results
    # are one get_context_detail call away.
    assert "## Test cases" in resp.text
    assert "Export downloads a CSV" in resp.text
    assert "Click Export" not in resp.text
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert "get_context_detail" in payload["markdown"]
    assert all(
        set(c.keys()) == {"test_case_id", "title"}
        for c in payload["test_cases"]
    )


def test_get_work_context_omits_test_cases_when_absent(mcp_client, monkeypatch):
    """US-5.7: runs dispatched before the bundling (no key) and issues
    with zero cases (empty list) both omit the section, no errors."""
    for extra in ({}, {"test_cases": []}):
        run_id = str(uuid.uuid4())
        _stub_work_context(monkeypatch, run_id, extra_context=extra)
        resp = mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {"name": "get_work_context", "arguments": {"run_id": run_id}},
            ),
            headers=HDR,
        )
        assert resp.status_code == 200
        assert "## Test cases" not in resp.text
        assert '"error"' not in resp.text


# ------------------------------------- US-5.4: request_clarification


def test_request_clarification_records_and_extends(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured: dict = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.extend_claim",
        lambda s, r, w, tool=None: captured.setdefault("extended", (r, w))
        or {"id": r, "claim_expires_at": "later"},
    )

    def fake_add(s, run, worker, question, options=None, multi_select=False):
        captured["question"] = question
        return {"id": str(uuid.uuid4()), "asked_at": "2026-07-17 12:00"}

    monkeypatch.setattr("app.factory_mcp.db.add_clarification", fake_add)
    # US-59.5: well under the round-trip cap, and the round gets recorded.
    monkeypatch.setattr(
        "app.factory_mcp.db.clarification_round_count", lambda s, r: (0, 3)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_clarification_round",
        lambda s, r: captured.setdefault("round_recorded", r),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "request_clarification",
                "arguments": {
                    "run_id": run_id,
                    "question": "Should exports include archived rows?",
                },
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["question"] == "Should exports include archived rows?"
    assert captured["extended"][0] == run_id  # lease extended like a heartbeat
    assert captured["round_recorded"] == run_id
    assert "Things to Do" in resp.text
    # US-59.5: the run now parks on the question — the response tells the
    # agent to stop, not to keep polling, and no longer suggests polling
    # get_clarifications as a next step.
    assert "End your turn now" in resp.text


def test_request_clarification_rejects_empty_and_non_holder(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "request_clarification",
                "arguments": {"run_id": run_id, "question": "   "},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "question is required" in resp.text

    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "request_clarification",
                "arguments": {"run_id": run_id, "question": "Real question?"},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "you do not hold this run" in resp.text


def test_get_clarifications_lists_answers_and_pending(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.list_run_clarifications",
        lambda s, run: [
            {
                "id": "c1",
                "run_id": run_id,
                "question": "Which currency format?",
                "answer": "EUR, two decimals",
                "asked_at": "2026-07-17 10:00",
                "answered_at": "2026-07-17 10:30",
            },
            {
                "id": "c2",
                "run_id": run_id,
                "question": "Include archived rows?",
                "answer": None,
                "asked_at": "2026-07-17 11:00",
                "answered_at": None,
            },
        ],
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_clarifications", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Which currency format?" in resp.text
    assert "EUR, two decimals" in resp.text
    assert "Include archived rows?" in resp.text
    assert "pending" in resp.text


# ------------------------------------------- US-5.8: project documents


def test_list_project_documents_marks_claim_links(mcp_client, monkeypatch):
    captured: dict = {}

    def stub(s, project_id, org_id, worker_id):
        captured.update(project_id=project_id, org_id=org_id, worker_id=worker_id)
        return [
            {
                "id": "doc-1",
                "name": "checkout-flow.md",
                "mime_type": "text/markdown",
                "attached_to": "prd",
                "source": "factory",
                "updated_at": "2026-07-16 10:00",
                "issue_id": "issue-1",
                "linked_to_claim": True,
            },
            {
                "id": "doc-2",
                "name": "logo.png",
                "mime_type": "image/png",
                "attached_to": "project",
                "source": "user",
                "updated_at": "2026-07-15 09:00",
                "issue_id": None,
                "linked_to_claim": False,
            },
        ]

    monkeypatch.setattr("app.factory_mcp.db.list_project_documents", stub)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "list_project_documents",
                "arguments": {"project_id": PROJECT_ID},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["project_id"] == PROJECT_ID
    assert captured["org_id"] == ORG_ID
    assert captured["worker_id"] == WORKER["id"]
    assert "checkout-flow.md" in resp.text
    assert "doc-1" in resp.text
    assert "linked to a work item you hold" in resp.text
    assert "get_document" in resp.text


def test_list_project_documents_cross_org_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.list_project_documents", lambda s, p, o, w: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "list_project_documents",
                "arguments": {"project_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "project not found" in resp.text


def test_get_document_returns_markdown_content(mcp_client, monkeypatch):
    doc_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.documents.get_document",
        lambda s, d: {
            "id": doc_id,
            "org_id": WORKER["org_id"],
            "name": "checkout-flow.md",
            "mime_type": "text/markdown",
            "storage_path": "x/y/z",
        },
    )

    async def fake_read(s, doc):
        return b"# Checkout flow\n\nStep one."

    monkeypatch.setattr("app.factory_mcp.documents.read_bytes", fake_read)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_document", "arguments": {"document_id": doc_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Step one." in resp.text


def test_get_document_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.documents.get_document",
        lambda s, d: {
            "id": d,
            "org_id": str(uuid.uuid4()),  # someone else's org
            "name": "secret.md",
            "mime_type": "text/markdown",
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_document",
                "arguments": {"document_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "document not found" in resp.text
    assert "secret" not in resp.text.replace("document not found", "")


def test_get_document_binary_is_refused(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.documents.get_document",
        lambda s, d: {
            "id": d,
            "org_id": WORKER["org_id"],
            "name": "logo.png",
            "mime_type": "image/png",
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_document",
                "arguments": {"document_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "only markdown/text" in resp.text


def test_get_work_context_points_at_linked_documents(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id)
    monkeypatch.setattr(
        "app.factory_mcp.db.list_run_documents",
        lambda s, r: [
            {
                "id": "doc-1",
                "name": "checkout-flow.md",
                "attached_to": "prd",
                "mime_type": "text/markdown",
            }
        ],
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "## Linked documents" in resp.text
    assert "checkout-flow.md" in resp.text
    assert "get_document" in resp.text
    # a pointer, not inlined content
    assert "Step one" not in resp.text


def test_add_comment_delegates(mcp_client, monkeypatch):
    captured: dict = {}

    async def fake_add(settings, worker, run_id, body):
        captured.update(run_id=run_id, body=body)
        return {"ok": True, "comment_id": "c9"}

    monkeypatch.setattr(
        "app.routers.worker.perform_add_comment", fake_add
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "add_comment",
                "arguments": {"run_id": "r1", "body": "Halfway done."},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured == {"run_id": "r1", "body": "Halfway done."}
    assert "Comment posted" in resp.text


def test_get_instructions_needs_no_claim(mcp_client, monkeypatch):
    """US-5.11: any org worker can read a queued item's instruction set
    before claiming; unknown/cross-org ids answer run-not-found."""
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_instructions",
        lambda s, r, org: {
            "id": r,
            "kind": "plan",
            "status": "queued",
            "issue_id": str(uuid.uuid4()),
            "issue_title": "Add CSV export",
            "instruction_set": "## Expectations — plan run\n\nDo the thing.",
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_instructions",
                "arguments": {"run_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Do the thing." in resp.text

    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_instructions", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_instructions",
                "arguments": {"run_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "run not found" in resp.text


def test_instructions_state_the_lease_and_how_to_hold_it(
    mcp_client, monkeypatch
):
    """US-27.2: run 11c564b0 worked 30 minutes on a 15-minute lease and never
    heartbeated — nothing told it to."""
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_instructions",
        lambda s, r, org: {
            "id": r,
            "kind": "plan",
            "status": "running",
            "issue_id": str(uuid.uuid4()),
            "issue_title": "Add CSV export",
            "instruction_set": "Do the thing.",
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_instructions",
                "arguments": {"run_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    # the fixture worker is `human`, so it gets the long lease — the point is
    # that the lease is STATED, whichever one applies
    assert "24 hours" in resp.text
    assert "report_progress" in resp.text
    assert "back to the pool" in resp.text


def test_instructions_spell_out_the_story_by_story_protocol(
    mcp_client, monkeypatch
):
    """US-27.1: stated where an agent reads before it claims."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_instructions",
        lambda s, r, org: {
            "id": run_id,
            "kind": "code",
            "status": "queued",
            "issue_id": str(uuid.uuid4()),
            "issue_title": "Login and auth",
            "instruction_set": "Build it.",
        },
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.run_members",
        lambda s, r: [_member(i) for i in range(1, 4)],
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_instructions", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert "covers 3 stories" in resp.text
    assert "final=false" in resp.text and "final=true" in resp.text
    assert "US-1.1.2" in resp.text


def test_submit_prd_delegates(mcp_client, monkeypatch):
    captured: dict = {}

    async def fake_perform_submit(settings, worker, run_id, body, trigger="submit"):
        captured.update(run_id=run_id, prd=body.prd)
        return {"ok": True, "issue_status": "prd-review"}

    monkeypatch.setattr(
        "app.routers.worker.perform_submit", fake_perform_submit
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_prd",
                "arguments": {"run_id": "r1", "prd": "## Problem\n\nx\n"},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["run_id"] == "r1"
    assert captured["prd"] == "## Problem\n\nx\n"
    assert "prd-review" in resp.text


# --------------------------------------- US-5.5: post-submit run status


def _status_view(
    kind="code",
    status="succeeded",
    issue_status="in-review",
    review=None,
    retry=None,
    **run_extra,
):
    run = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "status": status,
        "worker_id": WORKER["id"],
        "pr_url": None,
        "branch_ref": None,
        "error": None,
        "created_at": "2026-07-17 10:00",
        "finished_at": "2026-07-17 11:00",
        "claim_expires_at": "2026-07-17 12:00",
        "issue_id": str(uuid.uuid4()),
        "issue_title": "Add CSV export",
        "issue_status": issue_status,
        "worker_name": "codey",
    }
    run.update(run_extra)
    return {"run": run, "review": review, "retry": retry}


def _call_run_status(mcp_client):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_run_status", "arguments": {"run_id": str(uuid.uuid4())}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    return json.loads(resp.json()["result"]["content"][0]["text"])


def test_run_status_maps_pipeline_states(mcp_client, monkeypatch):
    """US-5.5: run rows map to the plain vocabulary — queued, claimed,
    in review, merged — with PR url where one exists."""
    cases = [
        (_status_view(status="queued"), "queued"),
        (_status_view(status="running"), "claimed"),
        (_status_view(), "in review"),
        (
            _status_view(
                review={"decision": "approved", "comment": None},
                issue_status="merged",
                pr_url="https://github.com/acme/webshop/pull/7",
            ),
            "merged",
        ),
        (_status_view(status="failed", error="clone timed out"), "failed"),
    ]
    for view, expected in cases:
        monkeypatch.setattr(
            "app.factory_mcp.db.get_run_status_view",
            lambda s, r, org, v=view: v,
        )
        payload = _call_run_status(mcp_client)
        assert payload["status"] == expected, expected
        if expected == "merged":
            assert payload["pr_url"] == "https://github.com/acme/webshop/pull/7"
        if expected == "failed":
            assert "clone timed out" in payload["markdown"]


def test_run_status_rejection_includes_feedback_and_retry(mcp_client, monkeypatch):
    """US-5.5: a rejected run answers with the manager's feedback and,
    once dispatched, the retry run's id and whether it's still unclaimed."""
    retry_id = str(uuid.uuid4())
    view = _status_view(
        issue_status="needs-fixes",
        review={"decision": "rejected", "comment": "The header row is missing."},
        retry={"id": retry_id, "status": "queued"},
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_status_view", lambda s, r, org: view
    )
    payload = _call_run_status(mcp_client)
    assert payload["status"] == "rejected"
    assert payload["feedback"] == "The header row is missing."
    assert payload["retry_run_id"] == retry_id
    assert payload["retry_unclaimed"] is True
    assert "## Rejection feedback" in payload["markdown"]
    assert "still unclaimed" in payload["markdown"]


def test_run_status_rejected_before_retry_dispatch(mcp_client, monkeypatch):
    """US-5.5: a sent-back prd run (no retry yet) states that the retry
    will appear in the pool — and carries no retry_run_id."""
    view = _status_view(
        kind="prd",
        issue_status="prd-review",
        review={"decision": "sent-back", "comment": "Tighten the goals."},
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_status_view", lambda s, r, org: view
    )
    payload = _call_run_status(mcp_client)
    assert payload["status"] == "rejected"
    assert payload["feedback"] == "Tighten the goals."
    assert "retry_run_id" not in payload
    assert "No retry dispatched yet" in payload["markdown"]
    assert "pr_url" not in payload  # prd runs have no PR


def test_run_status_cross_org_answers_not_found(mcp_client, monkeypatch):
    """US-5.5: unknown and cross-org run ids answer 'run not found' —
    no existence leak."""
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_status_view", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_run_status", "arguments": {"run_id": str(uuid.uuid4())}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "run not found" in resp.text


def test_list_available_work_flags_retries(mcp_client, monkeypatch):
    """US-5.5: pool items that follow an earlier same-kind run carry the
    'retry of run' flag so the linkage shows from the pool side too."""
    prior = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.list_worker_pool",
        lambda s, w, project_id=None: [
            {
                "id": "run-2",
                "kind": "code",
                "issue_id": "issue-1",
                "issue_title": "Add CSV export",
                "issue_type": "story",
                "project_name": "Webshop",
                "repo_full_name": "acme/webshop",
                "retry_of_run_id": prior,
            }
        ],
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": "list_available_work", "arguments": {}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "retry of run" in resp.text
    assert prior in resp.text


# --------------------------------------------- US-5.19: report_test_results


def test_test_cases_md_includes_ids():
    """US-5.19: the work-context Test cases section names each case's
    test_case_id so results can be addressed to it."""
    from app.factory_mcp import _test_cases_md

    md = _test_cases_md(
        {
            "test_cases": [
                {
                    "id": "case-1",
                    "title": "CSV downloads",
                    "steps": "Click export",
                    "expected_result": "File arrives",
                }
            ]
        }
    )
    # US-13.5: compact form — the id and title are present (addressable),
    # steps live behind get_context_detail.
    assert "case-1" in md
    assert "CSV downloads" in md
    assert "report_test_results" in md
    assert "Click export" not in md


def _report_call(results, run_id=None):
    return _rpc(
        "tools/call",
        {
            "name": "report_test_results",
            "arguments": {
                "run_id": run_id or str(uuid.uuid4()),
                "results": results,
            },
        },
    )


def test_report_test_results_records(mcp_client, monkeypatch):
    captured: dict = {}

    def fake_report(settings, run_id, worker, results):
        captured["run_id"] = run_id
        captured["worker"] = worker["id"]
        captured["results"] = results
        return {"ok": True, "test_run_id": "tr-1", "recorded": len(results)}

    monkeypatch.setattr("app.factory_mcp.db.report_test_results", fake_report)
    resp = mcp_client.post(
        "/mcp",
        json=_report_call(
            [
                {
                    "test_case_id": "case-1",
                    "status": "passed",
                    "evidence": "pytest 18 passed",
                }
            ]
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "agent-verified" in resp.text
    assert captured["results"][0]["status"] == "passed"


def test_report_test_results_rejects_bad_status(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.report_test_results",
        lambda *a, **k: pytest.fail("db reached with invalid status"),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_report_call([{"test_case_id": "case-1", "status": "green"}]),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "passed, failed, or blocked" in resp.text


def test_report_test_results_claim_enforced(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.report_test_results",
        lambda s, r, w, res: {
            "error": (
                "only the claim holder (or the submitter while the run is "
                "in review) can report test results for this run"
            )
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_report_call([{"test_case_id": "case-1", "status": "failed"}]),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "claim holder" in resp.text
    assert "claim_work" in resp.text


def test_report_test_results_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.report_test_results", lambda s, r, w, res: None
    )
    resp = mcp_client.post(
        "/mcp",
        json=_report_call([{"test_case_id": "case-1", "status": "passed"}]),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "run not found" in resp.text


# --------------------------------------------- US-5.20: repo browsing


def _repo_run(run_id, worker_id=None):
    run = _claimed_run(run_id, worker_id=worker_id)
    run["input_context"] = {
        "repo_full_name": "acme/webshop",
        "default_branch": "main",
    }
    return run


@pytest.fixture
def repo_claim(monkeypatch):
    """A held code run on acme/webshop whose work branch doesn't exist on
    GitHub (so refs default to the default branch)."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _repo_run(run_id)
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", fake_token
    )

    async def no_branch(token, owner, repo, branch):
        from app.github import GitHubError

        raise GitHubError(f"branch '{branch}' not found")

    monkeypatch.setattr("app.repo_browse.github.get_branch", no_branch)
    return run_id


def _tree_call(run_id, **arguments):
    return _rpc(
        "tools/call",
        {"name": "get_repo_tree", "arguments": {"run_id": run_id, **arguments}},
    )


def _file_call(run_id, path, **arguments):
    return _rpc(
        "tools/call",
        {
            "name": "read_repo_file",
            "arguments": {"run_id": run_id, "path": path, **arguments},
        },
    )


TREE = {
    "truncated": False,
    "tree": [
        {"path": "src", "type": "tree"},
        {"path": "src/app.py", "type": "blob", "size": 120},
        {"path": "README.md", "type": "blob", "size": 40},
    ],
}


def test_get_repo_tree_lists_at_default_branch(mcp_client, repo_claim, monkeypatch):
    captured = {}

    async def fake_tree(token, owner, repo, ref):
        captured["ref"] = ref
        return dict(TREE)

    monkeypatch.setattr("app.factory_mcp.github.get_tree", fake_tree)
    resp = mcp_client.post("/mcp", json=_tree_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert "src/app.py" in resp.text
    assert captured["ref"] == "main"


def test_get_repo_tree_prefers_existing_work_branch(
    mcp_client, repo_claim, monkeypatch
):
    async def branch_exists(token, owner, repo, branch):
        return {"name": branch}

    monkeypatch.setattr("app.repo_browse.github.get_branch", branch_exists)
    captured = {}

    async def fake_tree(token, owner, repo, ref):
        captured["ref"] = ref
        return dict(TREE)

    monkeypatch.setattr("app.factory_mcp.github.get_tree", fake_tree)
    resp = mcp_client.post("/mcp", json=_tree_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert captured["ref"].startswith("factory/issue-")


def test_get_repo_tree_narrows_by_path_and_truncates(
    mcp_client, repo_claim, monkeypatch
):
    async def fake_tree(token, owner, repo, ref):
        return dict(TREE)

    monkeypatch.setattr("app.factory_mcp.github.get_tree", fake_tree)
    monkeypatch.setattr("app.repo_browse.MAX_TREE_ENTRIES", 1)
    resp = mcp_client.post(
        "/mcp", json=_tree_call(repo_claim, path="src"), headers=HDR
    )
    assert resp.status_code == 200
    assert "src" in resp.text
    assert "README.md" not in resp.text
    assert "Truncated" in resp.text
    assert "narrow with path" in resp.text


def test_read_repo_file_returns_text(mcp_client, repo_claim, monkeypatch):
    import base64

    async def fake_content(token, owner, repo, path, ref):
        assert path == "README.md"
        return {
            "type": "file",
            "size": 11,
            "encoding": "base64",
            "content": base64.b64encode(b"hello world").decode(),
        }

    monkeypatch.setattr("app.factory_mcp.github.get_content", fake_content)
    resp = mcp_client.post(
        "/mcp", json=_file_call(repo_claim, "README.md"), headers=HDR
    )
    assert resp.status_code == 200
    assert "hello world" in resp.text


def test_read_repo_file_size_cap(mcp_client, repo_claim, monkeypatch):
    async def fake_content(token, owner, repo, path, ref):
        return {"type": "file", "size": 10_000_000, "content": ""}

    monkeypatch.setattr("app.factory_mcp.github.get_content", fake_content)
    resp = mcp_client.post(
        "/mcp", json=_file_call(repo_claim, "big.sql"), headers=HDR
    )
    assert resp.status_code == 200
    assert "above the" in resp.text


def test_read_repo_file_rejects_binary(mcp_client, repo_claim, monkeypatch):
    import base64

    async def fake_content(token, owner, repo, path, ref):
        return {
            "type": "file",
            "size": 4,
            "content": base64.b64encode(b"\x00\x01\x02\x03").decode(),
        }

    monkeypatch.setattr("app.factory_mcp.github.get_content", fake_content)
    resp = mcp_client.post(
        "/mcp", json=_file_call(repo_claim, "logo.png"), headers=HDR
    )
    assert resp.status_code == 200
    assert "binary" in resp.text
    assert "not served" in resp.text


def test_repo_tools_require_the_claim(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _repo_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post("/mcp", json=_tree_call(run_id), headers=HDR)
    assert "you do not hold this run" in resp.text


def test_repo_tools_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp", json=_tree_call(str(uuid.uuid4())), headers=HDR
    )
    assert "run not found" in resp.text


def _validate_call(run_id, **arguments):
    return _rpc(
        "tools/call",
        {
            "name": "validate_submission",
            "arguments": {"run_id": run_id, **arguments},
        },
    )


def _kind_run(run_id, kind):
    run = _claimed_run(run_id)
    run["kind"] = kind
    return run


def test_validate_submission_prd_findings(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _kind_run(run_id, "prd"),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(run_id, prd="## Problem\n\nonly one section"),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "missing required section" in resp.text
    assert "## Goals" in resp.text


def test_validate_submission_plan_zero_cases(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _kind_run(run_id, "plan"),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(run_id, plan="# Plan", test_plan="prose, no fence"),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "0 cases" in resp.text


def test_validate_submission_clean_answer(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _kind_run(run_id, "plan"),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    tp = (
        '```json\n{"cases": [{"title": "t", "steps": "s", '
        '"expected_result": "e", "test_types": ["functional"]}]}\n```'
    )
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(run_id, plan="# Plan\n\nsteps", test_plan=tp),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Structurally sound" in resp.text


def test_validate_submission_requires_claim(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post(
        "/mcp", json=_validate_call(run_id, prd="x"), headers=HDR
    )
    assert "you do not hold this run" in resp.text


# ---------------------------- US-5.29: changeset dry-run validation


def _dry_run_code_run(run_id):
    run = _claimed_run(run_id)
    run["input_context"] = {
        "repo_full_name": "acme/webshop",
        "default_branch": "main",
    }
    return run


def _stub_dry_run_github(monkeypatch, head=None, commit_ok=True):
    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", fake_token
    )

    async def fake_commit(token, owner, repo, ref):
        if not commit_ok:
            from app.github import GitHubError

            raise GitHubError(f"ref '{ref}' not found in this repo")
        return {"sha": ref}

    monkeypatch.setattr("app.factory_mcp.github.get_commit", fake_commit)

    async def fake_ref(token, owner, repo, branch):
        return None if head is None else {"object": {"sha": head}}

    monkeypatch.setattr("app.factory_mcp.github.get_ref", fake_ref)


def test_validate_changeset_dry_run_findings(mcp_client, monkeypatch):
    """US-5.29: the git-free payload gets the submit gate's exact checks
    as findings — before submitting, not as a rejection after."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    _stub_dry_run_github(monkeypatch)
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            base_sha="abc123",
            message="",
            files=[{"path": "../evil.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert payload["ok"] is False
    joined = " | ".join(payload["findings"])
    assert "commit message is empty" in joined
    assert "path traversal" in joined


def test_validate_changeset_dry_run_stale_base_reports_head(
    mcp_client, monkeypatch
):
    """US-5.29: read-only base freshness — a moved branch head is a
    finding carrying current_head; no refs are created or moved."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    _stub_dry_run_github(monkeypatch, head="newhead999")
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            base_sha="abc123",
            message="fix",
            files=[{"path": "a.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["ok"] is False
    assert payload["current_head"] == "newhead999"
    assert any("stale base" in f for f in payload["findings"])


def test_validate_changeset_dry_run_bad_base_sha_is_finding(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    _stub_dry_run_github(monkeypatch, commit_ok=False)
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            base_sha="nonsense",
            message="fix",
            files=[{"path": "a.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["ok"] is False
    assert any("not a commit in this repo" in f for f in payload["findings"])
    assert "error" not in payload


def test_validate_changeset_both_transports_is_finding(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            branch_ref="factory/issue-1",
            base_sha="abc123",
            message="fix",
            files=[{"path": "a.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["ok"] is False
    assert any("one transport per call" in f for f in payload["findings"])


def test_validate_changeset_dry_run_clean(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    _stub_dry_run_github(monkeypatch, head="abc123")
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            base_sha="abc123",
            message="feat: add a",
            files=[{"path": "a.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["ok"] is True
    assert "Structurally sound" in payload["markdown"]


def test_validate_changeset_shares_submit_code_path(mcp_client, monkeypatch):
    """US-5.29 parity by identity: the dry run and submit_changeset call
    the SAME validate_changeset function — patch it once, both see it."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _dry_run_code_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    calls = []

    def sentinel(files):
        calls.append(files)
        return ["sentinel finding"]

    monkeypatch.setattr(
        "app.factory_mcp.changesets.validate_changeset", sentinel
    )
    files = [{"path": "a.py", "op": "add", "content": "x"}]
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id, base_sha="abc123", message="fix", files=files
        ),
        headers=HDR,
    )
    assert "sentinel finding" in resp.text
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_changeset",
                "arguments": {
                    "run_id": run_id,
                    "base_sha": "abc123",
                    "message": "fix",
                    "files": files,
                },
            },
        ),
        headers=HDR,
    )
    assert "sentinel finding" in resp.text
    assert len(calls) == 2


def test_validate_changeset_requires_claim_holder(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_validate_call(
            run_id,
            base_sha="abc123",
            message="fix",
            files=[{"path": "a.py", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    assert "you do not hold this run" in resp.text


# --------------------------------------------- US-5.25: get_workspace


def _workspace_call(run_id):
    return _rpc(
        "tools/call", {"name": "get_workspace", "arguments": {"run_id": run_id}}
    )


def _fake_zip(prefix="acme-webshop-abc123"):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{prefix}/README.md", "hello")
        z.writestr(f"{prefix}/src/app.py", "print('hi')")
    return buf.getvalue()


@pytest.fixture
def workspace_github(monkeypatch):
    captured = {}

    async def fake_commit(token, owner, repo, ref):
        captured["commit_ref"] = ref
        return {"sha": "abc123def456"}

    monkeypatch.setattr("app.factory_mcp.github.get_commit", fake_commit)

    async def fake_zipball(token, owner, repo, ref, max_bytes):
        captured["zip_ref"] = ref
        return _fake_zip()

    monkeypatch.setattr("app.factory_mcp.github.download_zipball", fake_zipball)
    return captured


def test_get_workspace_pins_default_branch(
    mcp_client, repo_claim, workspace_github
):
    import base64
    import io
    import zipfile

    resp = mcp_client.post("/mcp", json=_workspace_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert "abc123def456" in resp.text
    # resolved to the default branch, archived at the pinned sha
    assert workspace_github["commit_ref"] == "main"
    assert workspace_github["zip_ref"] == "abc123def456"
    payload = resp.json()["result"]["structuredContent"]
    raw = base64.b64decode(payload["zip_base64"])
    names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
    assert any(n.endswith("README.md") for n in names)
    assert not any(".git/" in n for n in names)


def test_get_workspace_prefers_existing_work_branch(
    mcp_client, repo_claim, workspace_github, monkeypatch
):
    async def branch_exists(token, owner, repo, branch):
        return {"name": branch}

    monkeypatch.setattr("app.repo_browse.github.get_branch", branch_exists)
    resp = mcp_client.post("/mcp", json=_workspace_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert workspace_github["commit_ref"].startswith("factory/issue-")


def test_get_workspace_ceiling_hands_off_to_git_remote(
    mcp_client, repo_claim, workspace_github, monkeypatch
):
    async def too_big(token, owner, repo, ref, max_bytes):
        return None

    monkeypatch.setattr("app.factory_mcp.github.download_zipball", too_big)
    resp = mcp_client.post("/mcp", json=_workspace_call(repo_claim), headers=HDR)
    assert "ceiling" in resp.text
    assert "factory git remote" in resp.text


def test_get_workspace_requires_claim(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _repo_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post("/mcp", json=_workspace_call(run_id), headers=HDR)
    assert "you do not hold this run" in resp.text


def test_get_workspace_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp", json=_workspace_call(str(uuid.uuid4())), headers=HDR
    )
    assert "run not found" in resp.text


# --------------------------------------------- US-5.26: submit_changeset


def _changeset_call(run_id, files=None, base_sha="base-1", message="feat: x"):
    return _rpc(
        "tools/call",
        {
            "name": "submit_changeset",
            "arguments": {
                "run_id": run_id,
                "base_sha": base_sha,
                "message": message,
                "files": files
                or [{"path": "src/app.py", "op": "update", "content": "hi"}],
            },
        },
    )


@pytest.fixture
def changeset_claim(monkeypatch):
    """A held code run plus a stubbed apply/submit pipeline."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _repo_run(run_id)
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", fake_token
    )
    captured: dict = {"run_id": run_id}

    async def fake_apply(
        token, repo_full, branch, base_sha, message, files, author_name
    ):
        captured["apply"] = {
            "branch": branch,
            "base_sha": base_sha,
            "message": message,
            "author": author_name,
            "files": files,
        }
        return {"commit_sha": "commit-xyz"}

    monkeypatch.setattr(
        "app.factory_mcp.changesets.apply_changeset", fake_apply
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_issue_event",
        lambda s, org, issue, kind, payload: captured.setdefault(
            "event", (kind, payload)
        ),
    )

    async def fake_submit(settings, worker, rid, body):
        captured["submitted_branch"] = body.branch_ref
        return {
            "ok": True,
            "pr_url": "https://github.com/acme/webshop/pull/9",
            "issue_status": "in-review",
        }

    monkeypatch.setattr("app.routers.worker.perform_submit", fake_submit)
    # US-5.30: no live DB in tests — the unreported count is stubbed.
    monkeypatch.setattr(
        "app.factory_mcp.db.count_unreported_test_cases",
        lambda s, r, w, tool=None: 0,
    )
    # US-27.1: a single-story run has no run_items membership — the default
    # shape, and the one whose behaviour must not change at all.
    captured["members"] = []
    monkeypatch.setattr(
        "app.factory_mcp.db.run_members", lambda s, r: captured["members"]
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_changeset_coverage",
        lambda s, r, org, ids, sha, msg, files_changed=None: captured.setdefault(
            "coverage", []
        ).append({"issue_ids": list(ids), "sha": sha}),
    )
    return captured


def test_submit_changeset_happy_path(mcp_client, changeset_claim):
    resp = mcp_client.post(
        "/mcp", json=_changeset_call(changeset_claim["run_id"]), headers=HDR
    )
    assert resp.status_code == 200
    assert "commit-xyz" in resp.text
    assert "pull/9" in resp.text
    applied = changeset_claim["apply"]
    assert applied["branch"].startswith("factory/issue-")
    assert applied["base_sha"] == "base-1"
    # run id trailer on the commit message; audit event carries the summary
    assert "Factory-Run:" in applied["message"]
    kind, payload = changeset_claim["event"]
    assert kind == "changeset-submitted"
    assert payload["commit_sha"] == "commit-xyz"
    assert payload["files"][0]["path"] == "src/app.py"
    assert "content" not in payload["files"][0]
    # then the standard submit path ran on the same branch
    assert changeset_claim["submitted_branch"] == applied["branch"]


def test_submit_changeset_rejects_findings_before_github(
    mcp_client, changeset_claim, monkeypatch
):
    async def never_apply(*a, **k):
        raise AssertionError("GitHub must not be touched on findings")

    monkeypatch.setattr(
        "app.factory_mcp.changesets.apply_changeset", never_apply
    )
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_call(
            changeset_claim["run_id"],
            files=[{"path": "../evil.txt", "op": "add", "content": "x"}],
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "nothing touched GitHub" in resp.text
    assert "traversal" in resp.text


def test_submit_changeset_stale_base_carries_current_head(
    mcp_client, changeset_claim, monkeypatch
):
    async def stale(*a, **k):
        return {"stale": True, "current_head": "head-B"}

    monkeypatch.setattr("app.factory_mcp.changesets.apply_changeset", stale)
    resp = mcp_client.post(
        "/mcp", json=_changeset_call(changeset_claim["run_id"]), headers=HDR
    )
    assert "stale base" in resp.text
    assert "head-B" in resp.text
    assert "get_workspace" in resp.text


def test_submit_changeset_wrong_kind_points_at_submit_plan(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    run = _repo_run(run_id)
    run["kind"] = "plan"
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: run
    )
    resp = mcp_client.post("/mcp", json=_changeset_call(run_id), headers=HDR)
    assert "changesets are for code runs" in resp.text
    assert "submit_plan" in resp.text


def test_submit_changeset_requires_claim(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _repo_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post("/mcp", json=_changeset_call(run_id), headers=HDR)
    assert "you do not hold this run" in resp.text


def test_submit_changeset_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp", json=_changeset_call(str(uuid.uuid4())), headers=HDR
    )
    assert "run not found" in resp.text


# ------------------------------- US-27.1: story-by-story on a feature run


def _member(pos, landed=False):
    return {
        "issue_id": f"0000000{pos}-0000-4000-8000-00000000000{pos}",
        "position": pos,
        "title": f"Story {pos}",
        "status": "queued",
        "display_id": f"US-1.1.{pos}",
        "landed": landed,
        "commit_count": 1 if landed else 0,
    }


def _multi(changeset_claim, landed=()):
    changeset_claim["members"] = [
        _member(i, landed=i in landed) for i in range(1, 7)
    ]
    return changeset_claim


def _changeset_args(run_id, **extra):
    args = {
        "run_id": run_id,
        "base_sha": "base-1",
        "message": "feat: x",
        "files": [{"path": "src/app.py", "op": "update", "content": "hi"}],
    }
    args.update(extra)
    return _rpc("tools/call", {"name": "submit_changeset", "arguments": args})


def test_multi_story_commit_must_name_the_stories_it_covers(
    mcp_client, changeset_claim, monkeypatch
):
    _multi(changeset_claim)

    async def never_apply(*a, **k):
        raise AssertionError("attribution is settled before GitHub")

    monkeypatch.setattr(
        "app.factory_mcp.changesets.apply_changeset", never_apply
    )
    resp = mcp_client.post(
        "/mcp", json=_changeset_args(changeset_claim["run_id"]), headers=HDR
    )
    assert "covers 6 stories" in resp.text
    assert "US-1.1.1" in resp.text


def test_a_commit_naming_a_story_outside_the_run_is_refused(
    mcp_client, changeset_claim, monkeypatch
):
    _multi(changeset_claim)

    async def never_apply(*a, **k):
        raise AssertionError("nothing may touch GitHub")

    monkeypatch.setattr(
        "app.factory_mcp.changesets.apply_changeset", never_apply
    )
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(
            changeset_claim["run_id"], issue_ids=["US-9.9.9"], final=False
        ),
        headers=HDR,
    )
    assert "not in this run" in resp.text
    assert "US-9.9.9" in resp.text


def test_issue_ids_is_meaningless_on_a_single_story_run(
    mcp_client, changeset_claim
):
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(changeset_claim["run_id"], issue_ids=["US-1.1.1"]),
        headers=HDR,
    )
    assert "this run covers one" in resp.text


def test_multi_story_final_has_no_default(mcp_client, changeset_claim):
    """A default of true recreates the bug; a default of false strands runs."""
    _multi(changeset_claim)
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(changeset_claim["run_id"], issue_ids=["US-1.1.1"]),
        headers=HDR,
    )
    assert "final=false" in resp.text and "final=true" in resp.text


def test_a_non_final_commit_lands_without_closing_the_run(
    mcp_client, changeset_claim, monkeypatch
):
    """The call run 11c564b0 never had: commit, keep the claim, keep going."""
    _multi(changeset_claim)

    async def never_submit(*a, **k):
        raise AssertionError("a non-final commit must not finalize the run")

    monkeypatch.setattr("app.routers.worker.perform_submit", never_submit)
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(
            changeset_claim["run_id"], issue_ids=["US-1.1.1"], final=False
        ),
        headers=HDR,
    )
    assert "commit-xyz" in resp.text
    assert "The run is still yours" in resp.text
    assert changeset_claim["coverage"][0]["issue_ids"] == [_member(1)["issue_id"]]
    assert "submitted_branch" not in changeset_claim


def test_closing_with_an_uncommitted_story_is_refused_by_name(
    mcp_client, changeset_claim
):
    _multi(changeset_claim, landed=(1, 2, 3, 4))
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(
            changeset_claim["run_id"], issue_ids=["US-1.1.4"], final=True
        ),
        headers=HDR,
    )
    assert "cannot close the run" in resp.text
    assert "US-1.1.5" in resp.text and "US-1.1.6" in resp.text
    assert "allow_partial" in resp.text
    assert "submitted_branch" not in changeset_claim


def test_allow_partial_closes_and_records_what_was_left(
    mcp_client, changeset_claim
):
    _multi(changeset_claim, landed=(1, 2, 3, 4))
    resp = mcp_client.post(
        "/mcp",
        json=_changeset_args(
            changeset_claim["run_id"],
            issue_ids=["US-1.1.4"],
            final=True,
            allow_partial=True,
        ),
        headers=HDR,
    )
    assert "pull/9" in resp.text
    assert "4 of 6 stories" in resp.text
    assert "partial" in resp.text
    assert changeset_claim["submitted_branch"].startswith("factory/issue-")


# --------------------------------------------- US-5.22: get_pr_status


def _pr_run(run_id, kind="code", pr_url="https://github.com/acme/webshop/pull/7"):
    run = _claimed_run(run_id)
    run["kind"] = kind
    run["pr_url"] = pr_url
    run["input_context"] = {
        "repo_full_name": "acme/webshop",
        "default_branch": "main",
    }
    return run


def _pr_call(run_id):
    return _rpc(
        "tools/call", {"name": "get_pr_status", "arguments": {"run_id": run_id}}
    )


@pytest.fixture
def pr_github(monkeypatch):
    """A live PR: behind main, one failed check, unresolved comments."""

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", fake_token
    )

    async def fake_pull(token, owner, repo, number):
        return {
            "state": "open",
            "merged": False,
            "mergeable_state": "behind",
            "head": {"sha": "abc1234"},
        }

    monkeypatch.setattr("app.factory_mcp.github.get_pull", fake_pull)

    async def fake_checks(token, owner, repo, ref):
        return [
            {"name": "pytest", "status": "completed", "conclusion": "failure"},
            {"name": "lint", "status": "completed", "conclusion": "success"},
        ]

    monkeypatch.setattr("app.factory_mcp.github.list_check_runs", fake_checks)

    def comment(i):
        return {
            "author": {"login": "alice"},
            "path": "src/app.py",
            "line": 10 + i,
            "body": f"nit {i}",
        }

    async def fake_threads(token, owner, repo, number):
        return [
            {"isResolved": False, "comments": {"nodes": [comment(i)]}}
            for i in range(7)
        ] + [{"isResolved": True, "comments": {"nodes": [comment(99)]}}]

    monkeypatch.setattr(
        "app.factory_mcp.github.list_review_threads", fake_threads
    )


def test_get_pr_status_maps_checks_and_mergeability(
    mcp_client, monkeypatch, pr_github
):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _pr_run(run_id)
    )
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert resp.status_code == 200
    assert "rebase or merge main" in resp.text
    assert "pytest" in resp.text and "failure" in resp.text
    assert "abc1234" in resp.text


def test_get_pr_status_caps_comments_with_more_note(
    mcp_client, monkeypatch, pr_github
):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _pr_run(run_id)
    )
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert "more on GitHub" in resp.text
    # 7 unresolved, capped at 5 shown; the resolved thread's comment is out.
    assert "nit 99" not in resp.text


def test_get_pr_status_simulated_pr_is_honest(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _pr_run(run_id, pr_url="simulated://run/1"),
    )
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert "Simulated PR" in resp.text


def test_get_pr_status_no_pr_points_at_submit(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _pr_run(run_id, pr_url=None),
    )
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert "no PR yet" in resp.text
    assert "submit_code_work" in resp.text


def test_get_pr_status_non_code_run(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _pr_run(run_id, kind="plan"),
    )
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert "no PR exists" in resp.text


def test_get_pr_status_cross_org_is_not_found(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: None
    )
    resp = mcp_client.post(
        "/mcp", json=_pr_call(str(uuid.uuid4())), headers=HDR
    )
    assert "run not found" in resp.text


def test_get_pr_status_degrades_when_checks_forbidden(
    mcp_client, monkeypatch, pr_github
):
    """US-5.22/US-5.24: a 403 on the checks listing (App lacks Checks:
    read) degrades to PR-state-without-checks with a manager-fix note —
    the tool no longer hard-fails with a worker-pointed hint."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _pr_run(run_id)
    )

    async def forbidden(token, owner, repo, ref):
        from app.github import permission_error

        raise permission_error("list checks", "Checks: read")

    monkeypatch.setattr("app.factory_mcp.github.list_check_runs", forbidden)
    resp = mcp_client.post("/mcp", json=_pr_call(run_id), headers=HDR)
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert "error" not in payload
    assert payload["state"] == "open"
    assert payload["head_sha"] == "abc1234"
    assert payload["checks"] == []
    assert "Checks: read" in payload["checks_unavailable"]
    assert "unavailable" in payload["markdown"]
    # The rest of the answer is intact: mergeability and comments.
    assert "rebase or merge main" in payload["markdown"]
    assert payload["unresolved_comment_count"] == 7


def test_github_err_permission_hint_names_manager():
    """US-5.24: the permission case never gets the worker-pointed
    "check the path/ref and retry" hint."""
    from app.factory_mcp import _github_err
    from app.github import permission_error

    out = _github_err(permission_error("list checks", "Checks: read"))
    assert "Checks: read" in out["error"]
    assert "manager" in out["hint"]
    assert "path/ref" not in out["hint"]


def test_repo_tools_credential_failure_names_manager(
    mcp_client, repo_claim, monkeypatch
):
    async def cred_boom(settings, org_id, repo_full_name=None):
        from app.github import mint_error

        raise mint_error(404)

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", cred_boom
    )
    resp = mcp_client.post("/mcp", json=_tree_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert "reconnect GitHub" in resp.text
    assert "manager" in resp.text


# ------------------------- US-5.30: next-step guidance in responses


def _sc(resp):
    assert resp.status_code == 200
    return resp.json()["result"]["structuredContent"]


def test_next_guidance_consistency_happy_path(mcp_client, monkeypatch):
    """US-5.30 consistency: walk claim → context → workspace → submit →
    report → status; each response's `next` names the tool the loop
    actually calls next, and the markdown mirrors it."""
    run_id = str(uuid.uuid4())

    def call(name, args):
        return _sc(
            mcp_client.post(
                "/mcp",
                json=_rpc("tools/call", {"name": name, "arguments": args}),
                headers=HDR,
            )
        )

    # 1. claim_work → get_work_context
    monkeypatch.setattr(
        "app.factory_mcp.db.worker_allowed_for_run", lambda s, w, r: True
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.claim_run",
        lambda s, r, w, tool=None: {
            "id": r,
            "kind": "code",
            "claim_expires_at": "2026-07-17 15:00",
        },
    )
    out = call("claim_work", {"run_id": run_id})
    assert out["next"][0]["tool"] == "get_work_context"
    assert "get_work_context" in out["markdown"]

    # 2. get_work_context (code) → get_workspace
    _stub_work_context(monkeypatch, run_id)
    out = call("get_work_context", {"run_id": run_id})
    assert out["next"][0]["tool"] == "get_workspace"

    # 3. get_workspace → submit_changeset
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _repo_run(run_id)
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr(
        "app.factory_mcp.github_tokens.token_for_org", fake_token
    )

    async def no_branch(token, owner, repo, branch):
        from app.github import GitHubError

        raise GitHubError(f"branch '{branch}' not found")

    monkeypatch.setattr("app.repo_browse.github.get_branch", no_branch)

    async def fake_commit(token, owner, repo, ref):
        return {"sha": "base-1"}

    monkeypatch.setattr("app.factory_mcp.github.get_commit", fake_commit)

    async def fake_zip_dl(token, owner, repo, ref, cap):
        return _fake_zip()

    monkeypatch.setattr(
        "app.factory_mcp.github.download_zipball", fake_zip_dl
    )
    out = call("get_workspace", {"run_id": run_id})
    assert out["next"][0]["tool"] == "submit_changeset"
    assert out["base_sha"] in out["next"][0]["reason"]

    # 4. submit_changeset → report_test_results, then get_pr_status —
    #    with the unreported-case count called out.
    async def fake_apply(
        token, repo_full, branch, base_sha, message, files, author_name
    ):
        return {"commit_sha": "commit-xyz"}

    monkeypatch.setattr(
        "app.factory_mcp.changesets.apply_changeset", fake_apply
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_issue_event", lambda *a, **k: None
    )

    async def fake_submit(settings, worker, rid, body):
        return {
            "ok": True,
            "pr_url": "https://github.com/acme/webshop/pull/9",
            "issue_status": "in-review",
        }

    monkeypatch.setattr("app.routers.worker.perform_submit", fake_submit)
    monkeypatch.setattr(
        "app.factory_mcp.db.count_unreported_test_cases", lambda s, r, w, tool=None: 3
    )
    out = call(
        "submit_changeset",
        {
            "run_id": run_id,
            "base_sha": "base-1",
            "message": "feat: x",
            "files": [{"path": "a.py", "op": "add", "content": "x"}],
        },
    )
    assert [n["tool"] for n in out["next"]] == [
        "report_test_results",
        "get_pr_status",
    ]
    assert "3 test case(s) have no reported result yet" in out["next"][0]["reason"]
    assert "3 test case(s) have no reported result yet" in out["markdown"]

    # 5. report_test_results → get_pr_status / get_run_status
    monkeypatch.setattr(
        "app.factory_mcp.db.report_test_results",
        lambda s, r, w, res: {"ok": True, "test_run_id": "tr1", "recorded": 1},
    )
    out = call(
        "report_test_results",
        {
            "run_id": run_id,
            "results": [
                {"test_case_id": "tc1", "status": "passed", "evidence": "ok"}
            ],
        },
    )
    tools = [n["tool"] for n in out["next"]]
    assert "get_pr_status" in tools and "get_run_status" in tools

    # 6. get_run_status in review — nothing to do but wait: empty next.
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_status_view",
        lambda s, r, org: _status_view(),
    )
    out = call("get_run_status", {"run_id": run_id})
    assert out["next"] == []


def test_next_guidance_rejected_run_points_at_retry(mcp_client, monkeypatch):
    retry_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_status_view",
        lambda s, r, org: _status_view(
            review={"decision": "rejected", "comment": "needs tests"},
            retry={"id": retry_id, "status": "queued"},
        ),
    )
    out = _sc(
        mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {
                    "name": "get_run_status",
                    "arguments": {"run_id": str(uuid.uuid4())},
                },
            ),
            headers=HDR,
        )
    )
    assert out["next"][0]["tool"] == "claim_work"
    assert retry_id in out["next"][0]["reason"]


def test_next_guidance_lifecycle_writes_carry_next(mcp_client, monkeypatch):
    """release_work → list_available_work. request_clarification carries no
    `next` step any more (US-59.5) — it parks the run instead of suggesting
    a poll."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.release_claim",
        lambda s, r, w, note=None: True,
    )
    out = _sc(
        mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {"name": "release_work", "arguments": {"run_id": run_id}},
            ),
            headers=HDR,
        )
    )
    assert out["next"][0]["tool"] == "list_available_work"

    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    monkeypatch.setattr(
        "app.factory_mcp.db.add_clarification",
        lambda s, run, w, q, options=None, multi_select=False: {
            "id": "cl1",
            "asked_at": "2026-07-17 13:00",
        },
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.clarification_round_count", lambda s, r: (0, 3)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_clarification_round", lambda s, r: None
    )
    out = _sc(
        mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {
                    "name": "request_clarification",
                    "arguments": {"run_id": run_id, "question": "Which env?"},
                },
            ),
            headers=HDR,
        )
    )
    # US-59.5: request_clarification no longer suggests polling
    # get_clarifications as a next step — the run parks instead, and the
    # agent is told to end its turn rather than loop.
    assert out["next"] == []


def test_error_responses_use_hint_never_next(mcp_client, monkeypatch):
    """US-5.30: an error's next step is its hint — never both channels."""
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: None
    )
    out = _sc(
        mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {
                    "name": "get_work_context",
                    "arguments": {"run_id": str(uuid.uuid4())},
                },
            ),
            headers=HDR,
        )
    )
    assert "error" in out and "hint" in out
    assert "next" not in out


# ---------------- US-5.32: recommend_guideline_change


def _recommend_call(**arguments):
    return _rpc(
        "tools/call",
        {
            "name": "recommend_guideline_change",
            "arguments": {
                "project_id": PROJECT_ID,
                "proposed_text": "Use pnpm, not npm — the lockfile is pnpm's.",
                "rationale": "The guidelines say npm; the repo uses pnpm.",
                "severity": "major",
                **arguments,
            },
        },
    )


def _stub_recommendation(monkeypatch, captured, *, section=None, duplicate=False):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md",
        lambda s, p, o: {"name": "Webshop", "guidelines": ""},
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_guideline_section",
        lambda s, p, key: section,
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.list_guideline_section_keys",
        lambda s, p: ["tech-stack", "buildmill-workflow"],
    )

    def fake_record(s, worker, org, p, sec, key, title, severity, text, why):
        captured.update(
            worker=worker["name"],
            org=org,
            project=p,
            section=sec,
            key=key,
            title=title,
            severity=severity,
            text=text,
            rationale=why,
        )
        return {"id": "rec-1", "duplicate": duplicate}

    monkeypatch.setattr(
        "app.factory_mcp.db.record_guideline_recommendation", fake_record
    )


def test_recommend_guideline_change_queues_pending(mcp_client, monkeypatch):
    captured: dict = {}
    _stub_recommendation(
        monkeypatch,
        captured,
        section={"id": "sec-1", "section_key": "tech-stack", "title": "Tech stack", "content": "npm"},
    )
    resp = mcp_client.post(
        "/mcp", json=_recommend_call(section_key="tech-stack"), headers=HDR
    )
    assert resp.status_code == 200
    payload = resp.json()["result"]["structuredContent"]
    assert payload["status"] == "pending"
    assert payload["recommendation_id"] == "rec-1"
    assert payload["new_section"] is False
    assert captured["severity"] == "major"
    assert captured["section"]["id"] == "sec-1"
    # the response echoes the severity definitions so agents self-calibrate
    assert "severe" in payload["markdown"]
    assert "actively harmful" in payload["markdown"]


def test_recommend_guideline_change_new_section_path(mcp_client, monkeypatch):
    captured: dict = {}
    _stub_recommendation(monkeypatch, captured)
    resp = mcp_client.post(
        "/mcp",
        json=_recommend_call(
            proposed_text="## Package manager\n\nUse pnpm.", section_key=""
        ),
        headers=HDR,
    )
    payload = resp.json()["result"]["structuredContent"]
    assert payload["new_section"] is True
    # title derived from the proposed text's first heading
    assert payload["section_title"] == "Package manager"
    assert captured["section"] is None


def test_recommend_guideline_change_unknown_section_lists_keys(
    mcp_client, monkeypatch
):
    captured: dict = {}
    _stub_recommendation(monkeypatch, captured, section=None)
    resp = mcp_client.post(
        "/mcp", json=_recommend_call(section_key="nope"), headers=HDR
    )
    payload = resp.json()["result"]["structuredContent"]
    assert "no guideline section 'nope'" in payload["error"]
    assert "tech-stack" in payload["hint"]
    assert "new section" in payload["hint"]


def test_recommend_guideline_change_invalid_severity(mcp_client, monkeypatch):
    resp = mcp_client.post(
        "/mcp", json=_recommend_call(severity="urgent"), headers=HDR
    )
    payload = resp.json()["result"]["structuredContent"]
    assert "unknown severity" in payload["error"]
    # the hint teaches the four levels
    for level in ("trivial", "minor", "major", "severe"):
        assert level in payload["hint"]


def test_recommend_guideline_change_duplicate_damping(mcp_client, monkeypatch):
    captured: dict = {}
    _stub_recommendation(monkeypatch, captured, duplicate=True)
    resp = mcp_client.post("/mcp", json=_recommend_call(), headers=HDR)
    payload = resp.json()["result"]["structuredContent"]
    assert payload["duplicate"] is True
    assert payload["recommendation_id"] == "rec-1"
    assert "already have an identical pending recommendation" in payload["markdown"]


def test_recommend_guideline_change_cross_org_project_not_found(
    mcp_client, monkeypatch
):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md", lambda s, p, o: None
    )
    resp = mcp_client.post("/mcp", json=_recommend_call(), headers=HDR)
    assert "project not found" in resp.text


# --------------------------------------- US-13.2: prd/breakdown repo context


def _project_repo_run(run_id, kind="breakdown"):
    """A held run whose input_context carries NO repo keys (the prd and
    breakdown dispatch RPCs never included them) but whose project row
    does — the us-13.2 shape. Repo resolution must come from the project
    row at tool-call time."""
    run = _claimed_run(run_id)
    run["kind"] = kind
    run["input_context"] = {"story": "Split me"}
    run["project_repo_full_name"] = "acme/webshop"
    run["default_branch"] = "main"
    return run


@pytest.fixture
def breakdown_repo_claim(monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _project_repo_run(run_id),
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr("app.factory_mcp.github_tokens.token_for_org", fake_token)

    async def no_branch(token, owner, repo, branch):
        from app.github import GitHubError

        raise GitHubError(f"branch '{branch}' not found")

    monkeypatch.setattr("app.repo_browse.github.get_branch", no_branch)
    return run_id


def test_repo_tree_resolves_repo_from_project_row(
    mcp_client, breakdown_repo_claim, monkeypatch
):
    """US-13.2: a breakdown run dispatched before repo keys existed can
    still read the repository — resolved from the project row, not the
    frozen input_context."""
    captured = {}

    async def fake_tree(token, owner, repo, ref):
        captured["repo"] = f"{owner}/{repo}"
        captured["ref"] = ref
        return dict(TREE)

    monkeypatch.setattr("app.factory_mcp.github.get_tree", fake_tree)
    resp = mcp_client.post(
        "/mcp", json=_tree_call(breakdown_repo_claim), headers=HDR
    )
    assert resp.status_code == 200
    assert "src/app.py" in resp.text
    assert captured["repo"] == "acme/webshop"
    assert captured["ref"] == "main"


def test_read_repo_file_resolves_repo_from_project_row(
    mcp_client, breakdown_repo_claim, monkeypatch
):
    import base64

    async def fake_content(token, owner, repo, path, ref):
        assert (owner, repo) == ("acme", "webshop")
        return {
            "type": "file",
            "size": 5,
            "encoding": "base64",
            "content": base64.b64encode(b"hello").decode(),
        }

    monkeypatch.setattr("app.factory_mcp.github.get_content", fake_content)
    resp = mcp_client.post(
        "/mcp", json=_file_call(breakdown_repo_claim, "README.md"), headers=HDR
    )
    assert resp.status_code == 200
    assert "hello" in resp.text


def test_repo_tools_without_any_repo_still_explain(mcp_client, monkeypatch):
    """No repo in the frozen context AND none on the project row — the
    original no-linked-repo message stays."""
    run_id = str(uuid.uuid4())
    run = _claimed_run(run_id)
    run["kind"] = "breakdown"
    run["input_context"] = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: run
    )
    monkeypatch.setattr("app.factory_mcp.db.extend_claim", lambda s, r, w, tool=None: True)
    resp = mcp_client.post("/mcp", json=_tree_call(run_id), headers=HDR)
    assert resp.status_code == 200
    assert "no linked GitHub repository" in resp.text


def test_repo_tree_empty_repo_is_a_normal_state(
    mcp_client, repo_claim, monkeypatch
):
    """US-13.2: a reachable repository with no commits answers 'no files
    yet' — success-shaped, never 'ref not found'."""
    from app.github import GitHubError

    async def tree_404(token, owner, repo, ref):
        raise GitHubError(f"ref '{ref}' not found (404)")

    async def repo_ok(token, owner, repo):
        return {"full_name": f"{owner}/{repo}"}

    async def no_branches(token, owner, repo):
        return []

    monkeypatch.setattr("app.factory_mcp.github.get_tree", tree_404)
    monkeypatch.setattr("app.factory_mcp.github.get_repo", repo_ok)
    monkeypatch.setattr("app.factory_mcp.github.list_branches", no_branches)
    resp = mcp_client.post("/mcp", json=_tree_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    body = resp.json()["result"]["structuredContent"]
    assert body.get("empty_repo") is True
    assert body.get("entries") == []
    assert "no files yet" in resp.text
    assert "error" not in body


def test_repo_tree_bad_ref_names_the_ref(mcp_client, repo_claim, monkeypatch):
    """A ref that doesn't exist on a non-empty repo is named as such —
    distinguishable from an empty repo and from an unreachable one."""
    from app.github import GitHubError

    async def tree_404(token, owner, repo, ref):
        raise GitHubError(f"ref '{ref}' not found (404)")

    async def repo_ok(token, owner, repo):
        return {"full_name": f"{owner}/{repo}"}

    async def branches(token, owner, repo):
        return [{"name": "main"}]

    monkeypatch.setattr("app.factory_mcp.github.get_tree", tree_404)
    monkeypatch.setattr("app.factory_mcp.github.get_repo", repo_ok)
    monkeypatch.setattr("app.factory_mcp.github.list_branches", branches)
    resp = mcp_client.post(
        "/mcp", json=_tree_call(repo_claim, ref="nope"), headers=HDR
    )
    assert resp.status_code == 200
    assert "ref 'nope' not found" in resp.text
    assert "no files yet" not in resp.text


def test_repo_tree_unreachable_repo_says_so(mcp_client, repo_claim, monkeypatch):
    from app.github import GitHubError

    async def tree_404(token, owner, repo, ref):
        raise GitHubError("not found (404)")

    async def repo_gone(token, owner, repo):
        raise GitHubError("not found (404)")

    monkeypatch.setattr("app.factory_mcp.github.get_tree", tree_404)
    monkeypatch.setattr("app.factory_mcp.github.get_repo", repo_gone)
    resp = mcp_client.post("/mcp", json=_tree_call(repo_claim), headers=HDR)
    assert resp.status_code == 200
    assert "not reachable" in resp.text


def test_prd_and_breakdown_context_name_the_repo(mcp_client, monkeypatch):
    """US-13.2: prd and breakdown briefs state the repo is readable, the
    way plan runs already do — an agent that never tries is as blind as
    one that fails."""
    for kind in ("prd", "breakdown"):
        run_id = str(uuid.uuid4())
        _stub_work_context(monkeypatch, run_id, kind=kind)
        resp = mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {"name": "get_work_context", "arguments": {"run_id": run_id}},
            ),
            headers=HDR,
        )
        assert resp.status_code == 200
        payload = resp.json()["result"]["structuredContent"]
        md = payload["markdown"]
        assert "## Repository" in md, kind
        assert "acme/webshop" in md, kind
        assert "get_repo_tree" in md, kind
        assert payload["repo_full_name"] == "acme/webshop", kind
        assert payload["default_branch"] == "main", kind


# --------------------------------------- US-13.3: notes_for_manager


def test_submit_tools_carry_notes_for_manager(mcp_client, monkeypatch):
    """US-13.3: the plan/prd/stories submit tools accept notes_for_manager
    and pass it through the shared submit contract as Submit.notes."""
    captured = {}

    async def fake_perform(settings, worker, run_id, body, trigger="submit"):
        captured[body.plan and "plan" or body.prd and "prd" or "stories"] = (
            body.notes
        )
        return {"ok": True, "issue_status": "plan-review"}

    monkeypatch.setattr("app.routers.worker.perform_submit", fake_perform)
    for name, args in (
        ("submit_plan", {"plan": "# P", "notes_for_manager": "risk A"}),
        ("submit_prd", {"prd": "## Problem", "notes_for_manager": "risk B"}),
        (
            "submit_stories",
            {"stories": [{"title": "S"}], "notes_for_manager": "risk C"},
        ),
    ):
        resp = mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {
                    "name": name,
                    "arguments": {"run_id": str(uuid.uuid4()), **args},
                },
            ),
            headers=HDR,
        )
        assert resp.status_code == 200
    assert captured == {"plan": "risk A", "prd": "risk B", "stories": "risk C"}


def test_get_instructions_names_the_notes_channel(mcp_client, monkeypatch):
    """US-13.3: get_instructions tells every worker the hand-back channel
    exists and that flagging concerns is part of finishing the work."""
    monkeypatch.setattr(
        "app.factory_mcp.db.get_run_instructions",
        lambda s, r, org: {
            "id": r,
            "issue_id": str(uuid.uuid4()),
            "kind": "code",
            "issue_title": "Add CSV export",
            "instruction_set": "Do the thing.",
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_instructions",
                "arguments": {"run_id": str(uuid.uuid4())},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "notes_for_manager" in resp.text
    assert "part of finishing the work" in resp.text


# --------------------------------------- US-13.5: compact brief + pull


def _big_context():
    return {
        "prd": "P" * 30000,
        "guidelines": "G" * 20000,
        "learnings": "L" * 8000,
        "plan": "PLAN-LINE\n" * 1500,
        "test_plan": "T" * 3000,
        "test_cases": [
            {
                "id": str(uuid.uuid4()),
                "title": f"Case {i}",
                "steps": "step\n" * 40,
                "expected_result": "E" * 200,
            }
            for i in range(40)
        ],
    }


def _context_call(run_id):
    return _rpc(
        "tools/call",
        {"name": "get_work_context", "arguments": {"run_id": run_id}},
    )


def test_code_context_is_a_compact_brief(mcp_client, monkeypatch):
    """US-13.5: the brief is the contract (story, AC, plan, mechanics,
    case ids) plus pointers — not everything the factory knows. The size
    is asserted so it cannot drift back."""
    ctx = _big_context()
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id, kind="code", extra_context=ctx)
    resp = mcp_client.post("/mcp", json=_context_call(run_id), headers=HDR)
    assert resp.status_code == 200
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    md = payload["markdown"]
    # The plan is the contract a code run is judged against — inline.
    assert "PLAN-LINE" in md and ctx["test_plan"] in md
    # Everything else is pulled, not pushed.
    assert ctx["prd"] not in md
    assert ctx["guidelines"] not in md
    assert ctx["learnings"] not in md
    # The full input_context echo is gone from the structured payload.
    assert "context" not in payload
    dumped = json.dumps(payload)
    assert ctx["prd"] not in dumped and ctx["guidelines"] not in dumped
    # Order-of-magnitude bound: everything beyond the inlined plan and
    # test plan fits a small fixed budget.
    overhead = len(md) - len(ctx["plan"]) - len(ctx["test_plan"])
    assert overhead < 6000, f"brief overhead {overhead} chars"
    # The brief names what it omitted and how to get it.
    sections = {o["section"] for o in payload["omitted"]}
    assert {"prd", "guidelines", "learnings", "release_reference"} <= sections
    assert "Not inlined" in md


def test_plan_context_stays_small_and_points_at_prd(mcp_client, monkeypatch):
    ctx = _big_context()
    del ctx["plan"], ctx["test_plan"]
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id, kind="plan", extra_context=ctx)
    resp = mcp_client.post("/mcp", json=_context_call(run_id), headers=HDR)
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    md = payload["markdown"]
    assert len(md) < 9000, f"plan brief {len(md)} chars"
    assert ctx["prd"] not in md
    assert {"prd", "guidelines", "learnings"} <= {
        o["section"] for o in payload["omitted"]
    }


def test_get_context_detail_serves_the_omitted_sections(
    mcp_client, monkeypatch
):
    ctx = _big_context()
    run_id = str(uuid.uuid4())
    _stub_work_context(monkeypatch, run_id, kind="code", extra_context=ctx)

    def call(section):
        return mcp_client.post(
            "/mcp",
            json=_rpc(
                "tools/call",
                {
                    "name": "get_context_detail",
                    "arguments": {"run_id": run_id, "section": section},
                },
            ),
            headers=HDR,
        )

    resp = call("prd")
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert payload["content"] == ctx["prd"]

    resp = call("test_cases")
    payload = json.loads(resp.json()["result"]["content"][0]["text"])
    assert len(payload["test_cases"]) == 40
    assert payload["test_cases"][0]["steps"].startswith("step")

    resp = call("guidelines")
    assert "get_project_guidelines" in resp.text

    resp = call("nonsense")
    assert "unknown section" in resp.text


def test_get_context_detail_requires_the_claim(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _claimed_run(run_id, worker_id=str(uuid.uuid4())),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "get_context_detail",
                "arguments": {"run_id": run_id, "section": "prd"},
            },
        ),
        headers=HDR,
    )
    assert "you do not hold this run" in resp.text


# --------------------------------------- US-13.11: submit_test_run


def _test_run(run_id, kind="test"):
    run = _claimed_run(run_id)
    run["kind"] = kind
    return run


def test_submit_test_run_rejects_zero_results(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _test_run(run_id)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.count_run_test_results", lambda s, r: 0
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_test_run",
                "arguments": {"run_id": run_id, "summary": "ran nothing"},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "nothing to hand back" in resp.text
    assert "release_work" in resp.text


def test_submit_test_run_completes_with_results(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _test_run(run_id)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.count_run_test_results", lambda s, r: 3
    )

    def fake_complete(settings, rid, outcome, *a, **kw):
        captured["outcome"] = outcome
        captured["trigger"] = kw.get("trigger")
        return True

    monkeypatch.setattr("app.factory_mcp.db.complete_run", fake_complete)
    monkeypatch.setattr(
        "app.routers.worker.db.set_run_handback_notes",
        lambda s, r, n: captured.update(notes=n),
    )
    monkeypatch.setattr(
        "app.routers.worker.db.add_worker_comment",
        lambda s, run, w, body: {"id": "c1"},
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_test_run",
                "arguments": {
                    "run_id": run_id,
                    "summary": "pytest -q on factory/us-1: 12 passed",
                },
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Verification complete" in resp.text
    assert captured["outcome"] == "succeeded"
    assert "12 passed" in captured["notes"]


def test_submit_test_run_wrong_kind_refused(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _test_run(run_id, kind="code"),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_test_run",
                "arguments": {"run_id": run_id, "summary": "x"},
            },
        ),
        headers=HDR,
    )
    assert "test runs only" in resp.text


# --------------------------------------- US-63.1: submit_release_notes


def _release_prep_row(prep_id, release_id, worker_id=None, status="running"):
    return {
        "id": prep_id,
        "org_id": WORKER["org_id"],
        "project_id": str(uuid.uuid4()),
        "release_id": release_id,
        "status": status,
        "worker_id": worker_id or WORKER["id"],
        "repo_full_name": "acme/webshop",
        "default_branch": "main",
    }


def _wire_release_prep_submit(
    monkeypatch, prep_id, *, version="2026.07.25.1", deploy_available=True
):
    release_id = str(uuid.uuid4())
    captured = {"patches": []}
    monkeypatch.setattr(
        "app.db.get_release_prep",
        lambda s, pid, org: _release_prep_row(prep_id, release_id),
    )
    monkeypatch.setattr(
        "app.db.get_release",
        lambda s, rid: {
            "id": rid, "version": version, "commit_sha": "f" * 40,
            "project_id": str(uuid.uuid4()),
        },
    )
    monkeypatch.setattr(
        "app.db.attach_release_inherited_cases",
        lambda s, rid: captured.update(inherited_for=rid) or 2,
    )
    monkeypatch.setattr(
        "app.db.attach_release_test_cases",
        lambda s, **kw: captured.update(cases=kw["cases"]) or len(kw["cases"]),
    )
    monkeypatch.setattr(
        "app.db.update_release",
        lambda s, rid, patch: captured["patches"].append(patch),
    )
    monkeypatch.setattr(
        "app.db.stamp_release_milestones",
        lambda s, rid, **kw: captured.update(milestones=kw),
    )
    monkeypatch.setattr(
        "app.db.complete_release_prep",
        lambda s, pid, outcome: captured.update(outcome=outcome) or True,
    )
    monkeypatch.setattr(
        "app.db.get_release_uat_deployment_id",
        lambda s, pid: str(uuid.uuid4()) if deploy_available else None,
    )
    monkeypatch.setattr(
        "app.db.get_deployment_for_agent",
        lambda s, did, org: (
            {
                "deployment": {"id": did, "org_id": org, "name": "uat-app"},
                "server": {"id": str(uuid.uuid4())},
                "project": {"name": "Webshop", "repo_full_name": "acme/webshop"},
            }
            if deploy_available
            else None
        ),
    )
    monkeypatch.setattr(
        "app.deploy.launch_release_uat_deploy",
        lambda *a, **kw: captured.update(launched=True) or str(uuid.uuid4()),
    )

    async def fake_doc(settings, **kw):
        captured["doc_name"] = kw.get("name")
        return {"id": str(uuid.uuid4())}

    monkeypatch.setattr("app.release_prep.documents.create_or_replace", fake_doc)
    return captured


def _submit_release(mcp_client, prep_id, **arguments):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {
                "name": "submit_release_notes",
                "arguments": {"prep_id": prep_id, **arguments},
            },
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    return resp.text


def test_submit_release_rejects_a_mismatched_version(mcp_client, monkeypatch):
    """US-7.14's rule: the manager fixed the version when they cut the
    release; the agent only ever reads it."""
    prep_id = str(uuid.uuid4())
    _wire_release_prep_submit(monkeypatch, prep_id)
    text = _submit_release(
        mcp_client,
        prep_id,
        notes_summary="# Release 9.9.9.9\n\nstuff",
        notes_detail="detail",
    )
    assert "must carry the version 2026.07.25.1" in text


def test_submit_release_requires_both_sets_of_notes(mcp_client, monkeypatch):
    prep_id = str(uuid.uuid4())
    _wire_release_prep_submit(monkeypatch, prep_id)
    text = _submit_release(
        mcp_client,
        prep_id,
        notes_summary="# Release 2026.07.25.1\n\nstuff",
        notes_detail="   ",
    )
    assert "notes_detail is required" in text


def test_submit_release_stores_notes_cases_and_triggers_the_uat_deploy(
    mcp_client, monkeypatch
):
    """US-63.1/63.2: the agent's job is notes only — no deployment_run_id to
    hand in, no health check to observe. Submitting fires the UAT deploy
    itself, with no further action from the agent."""
    prep_id = str(uuid.uuid4())
    captured = _wire_release_prep_submit(monkeypatch, prep_id)
    text = _submit_release(
        mcp_client,
        prep_id,
        notes_summary="# Release 2026.07.25.1\n\nshipped things",
        notes_detail="## Migrations\n\n- 131_release_run_contract",
        test_cases=[
            {
                "title": "Cut a release end to end",
                "steps": "1. Cut\n2. Watch",
                "expected_result": "It lands on UAT",
            }
        ],
    )
    assert "UAT deploy is firing now" in text
    assert captured["outcome"] == "succeeded"
    assert captured["launched"] is True
    notes_patch, deploying_patch = captured["patches"]
    assert notes_patch["status"] == "notes-ready"
    assert notes_patch["notes_summary"].startswith("# Release 2026.07.25.1")
    assert notes_patch["notes_detail"].startswith("## Migrations")
    assert deploying_patch["status"] == "deploying"
    assert captured["milestones"] == {
        "notes_written": True,
        "cases_attached": True,
    }
    assert captured["cases"][0]["title"] == "Cut a release end to end"
    # The included work items' own cases come across too — the release's set
    # is assembled, not invented.
    assert captured["inherited_for"]
    assert captured["doc_name"] == "release-notes-2026.07.25.1.md"


def test_submit_release_reports_a_deploy_trigger_failure_without_failing_submit(
    mcp_client, monkeypatch
):
    """Notes and test cases are already committed by the time the deploy
    trigger is attempted — a missing UAT deployment must not undo that."""
    prep_id = str(uuid.uuid4())
    captured = _wire_release_prep_submit(prep_id=prep_id, monkeypatch=monkeypatch, deploy_available=False)
    text = _submit_release(
        mcp_client,
        prep_id,
        notes_summary="# Release 2026.07.25.1\n\nstuff",
        notes_detail="detail",
    )
    assert "failed to start" in text
    assert captured["outcome"] == "succeeded"
    assert captured["patches"][-1]["status"] == "uat-deploy-failed"


# --------------------------------------- US-13.13: deploy-run tools


DEPLOY_DEP_ID = str(uuid.uuid4())


def _deploy_run(run_id, ic_extra=None):
    run = _claimed_run(run_id)
    run["kind"] = "deploy"
    run["issue_id"] = None
    run["project_id"] = str(uuid.uuid4())
    run["input_context"] = {
        "run_kind": "deploy",
        "deployment_id": DEPLOY_DEP_ID,
        "deployment": {
            "name": "uat-app",
            "environment": "uat",
            "server_name": "dr-server",
            "branch": "main",
            "strategy": "releases",
            "website_url": None,
            "health_check_url": "http://localhost/health",
        },
        "project_name": "Webshop",
        "repo_full_name": "acme/webshop",
        "ref": None,
        "auto_rollback": False,
        **(ic_extra or {}),
    }
    return run


def _deploy_bundle(protected=False, environment="uat", flag=False):
    return {
        "deployment": {
            "id": DEPLOY_DEP_ID,
            "org_id": WORKER["org_id"],
            "project_id": str(uuid.uuid4()),
            "name": "uat-app",
            "environment": environment,
            "protected": protected,
            "agent_dispatch_allowed": flag,
            "branch": "main",
            "strategy": "releases",
            "health_check_url": "http://localhost/health",
            "health_check_expected_status": 200,
        },
        "server": {"id": str(uuid.uuid4()), "name": "dr-server"},
        "project": {"id": str(uuid.uuid4()), "name": "Webshop",
                    "repo_full_name": "acme/webshop",
                    "uat_branch": "", "production_branch": ""},
    }


def _deploy_call(name, run_id, **arguments):
    return _rpc(
        "tools/call",
        {"name": name, "arguments": {"run_id": run_id, **arguments}},
    )


def test_trigger_deployment_recheck_refuses_protected(mcp_client, monkeypatch):
    """Defense in depth: even with a dispatched run in hand, the trigger
    tool re-checks the rails and refuses a protected deployment."""
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: _deploy_run(run_id)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent",
        lambda s, d, org: _deploy_bundle(protected=True),
    )
    resp = mcp_client.post(
        "/mcp", json=_deploy_call("trigger_deployment", run_id), headers=HDR
    )
    assert resp.status_code == 200
    assert "human-only" in resp.text


def test_rollback_refuses_without_preauthorization(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _deploy_run(
            run_id, {"deployment_run_id": str(uuid.uuid4())}
        ),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent",
        lambda s, d, org: _deploy_bundle(),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_deploy_call("trigger_deployment_rollback", run_id),
        headers=HDR,
    )
    assert "not pre-authorized" in resp.text
    assert "deployed-but-unhealthy" in resp.text


def test_rollback_is_exactly_once(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _deploy_run(
            run_id,
            {
                "auto_rollback": True,
                "deployment_run_id": str(uuid.uuid4()),
                "rollback_run_id": str(uuid.uuid4()),
            },
        ),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent",
        lambda s, d, org: _deploy_bundle(),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_deploy_call("trigger_deployment_rollback", run_id),
        headers=HDR,
    )
    assert "exactly once" in resp.text


def test_submit_deploy_run_verdict_must_match_reality(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    dr_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _deploy_run(run_id, {"deployment_run_id": dr_id}),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent",
        lambda s, d, org: _deploy_bundle(),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_run_view",
        lambda s, d, org: {
            "id": dr_id,
            "status": "failed",
            "finished_at": "2026-07-20 20:00",
            "log_tail": "boom",
        },
    )
    # 'deployed' over a failed pipeline is a lie — refused.
    resp = mcp_client.post(
        "/mcp",
        json=_deploy_call(
            "submit_deploy_run", run_id, verdict="deployed", summary="looks fine"
        ),
        headers=HDR,
    )
    assert "would misreport" in resp.text
    # 'rolled-back' without a rollback is a lie — refused.
    resp = mcp_client.post(
        "/mcp",
        json=_deploy_call(
            "submit_deploy_run", run_id, verdict="rolled-back", summary="x"
        ),
        headers=HDR,
    )
    assert "no rollback was triggered" in resp.text


def test_submit_deploy_run_unhealthy_notifies_managers(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    dr_id = str(uuid.uuid4())
    captured = {}
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _deploy_run(run_id, {"deployment_run_id": dr_id}),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent",
        lambda s, d, org: _deploy_bundle(),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_run_view",
        lambda s, d, org: {
            "id": dr_id,
            "status": "succeeded",
            "finished_at": "2026-07-20 20:00",
            "log_tail": "",
        },
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.update_run_input_context",
        lambda s, r, patch: captured.update(patch),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.complete_run", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "app.routers.worker.db.set_run_handback_notes",
        lambda s, r, n: captured.update(notes=n),
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.notify_org_managers",
        lambda s, org, kind, payload: captured.update(notified=kind),
    )
    resp = mcp_client.post(
        "/mcp",
        json=_deploy_call(
            "submit_deploy_run",
            run_id,
            verdict="deployed-unhealthy",
            summary="health check failed with HTTP 502",
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    assert "Verdict recorded" in resp.text
    assert captured["verdict"] == "deployed-unhealthy"
    assert captured["notified"] == "deploy_unhealthy"
    assert "HTTP 502" in captured["notes"]


# ----------------------------------------- US-63.1: get_release_changes


def _release_row(**over):
    return {
        "id": str(uuid.uuid4()),
        "version": "2026.07.25.1",
        "commit_sha": "f" * 40,
        "previous_release_id": None,
        "included_items": [{"display_id": "US-1.2.3", "title": "Export CSV"}],
        **over,
    }


@pytest.fixture
def release_claim(monkeypatch):
    """A held release-prep job whose release has a previous release to diff
    from."""
    prep_id = str(uuid.uuid4())
    prev = _release_row(version="2026.07.24.1", commit_sha="a" * 40)
    current = _release_row(previous_release_id=prev["id"])
    releases = {prev["id"]: prev, current["id"]: current}

    monkeypatch.setattr(
        "app.db.get_release_prep",
        lambda s, pid, org: _release_prep_row(prep_id, current["id"]),
    )
    monkeypatch.setattr("app.db.heartbeat_release_prep", lambda s, pid, wid: True)
    monkeypatch.setattr(
        "app.factory_mcp.db.get_release", lambda s, rid: releases.get(rid)
    )
    # us-101.1: the requirement behind each included item. Stubbed here so
    # these stay tests of the RANGE; the enrichment has its own coverage.
    monkeypatch.setattr(
        "app.factory_mcp.db.release_source_material",
        lambda s, org, proj, ids: {
            "items": {},
            "always_on_uat": [],
            "caps": {"body_chars": 1500, "acceptance_criteria": 12, "case_titles": 25},
        },
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    monkeypatch.setattr("app.factory_mcp.github_tokens.token_for_org", fake_token)
    return {"prep_id": prep_id, "current": current, "prev": prev}


def _changes_call(prep_id, **arguments):
    return _rpc(
        "tools/call",
        {
            "name": "get_release_changes",
            "arguments": {"prep_id": prep_id, **arguments},
        },
    )


def _changes(mcp_client, prep_id, **arguments):
    resp = mcp_client.post("/mcp", json=_changes_call(prep_id, **arguments), headers=HDR)
    assert resp.status_code == 200
    return json.loads(resp.json()["result"]["content"][0]["text"])


def _compare(files, commits=("c1",)):
    async def fake_compare(token, owner, repo, base, head):
        return {
            "commits": [
                {
                    "sha": s,
                    "commit": {
                        "message": f"msg {s}",
                        "author": {"name": "Dev", "date": "2026-07-25T00:00:00Z"},
                    },
                }
                for s in commits
            ],
            "files": files,
        }

    return fake_compare


def test_release_changes_returns_the_range(mcp_client, release_claim, monkeypatch):
    monkeypatch.setattr(
        "app.factory_mcp.github.compare_commits",
        _compare(
            [
                {
                    "filename": "infra/supabase/migrations/131_x.sql",
                    "status": "added",
                    "additions": 20,
                    "deletions": 0,
                },
                {
                    "filename": "apps/web/src/page.tsx",
                    "status": "modified",
                    "additions": 3,
                    "deletions": 1,
                },
            ]
        ),
    )
    body = _changes(mcp_client, release_claim["prep_id"])
    assert body["version"] == "2026.07.25.1"
    assert body["previous_version"] == "2026.07.24.1"
    assert body["first_release"] is False
    assert body["commit_count"] == 1
    assert body["file_count"] == 2
    assert body["work_items"][0]["display_id"] == "US-1.2.3"
    assert body["truncated"] is False
    # us-101.1: a subject line is a string, and the migrations in the range
    # are worked out here rather than left to the agent to go looking for.
    assert body["commits"][0]["message"] == "msg c1"
    assert body["migrations"] == ["infra/supabase/migrations/131_x.sql"]


def test_release_changes_filters_by_path_prefix(
    mcp_client, release_claim, monkeypatch
):
    """The cheapest possible answer to 'which migrations are in this release'."""
    monkeypatch.setattr(
        "app.factory_mcp.github.compare_commits",
        _compare(
            [
                {"filename": "infra/supabase/migrations/131_x.sql", "status": "added"},
                {"filename": "apps/web/src/page.tsx", "status": "modified"},
            ]
        ),
    )
    body = _changes(
        mcp_client,
        release_claim["prep_id"],
        path_prefix="infra/supabase/migrations/",
    )
    assert [f["path"] for f in body["files"]] == [
        "infra/supabase/migrations/131_x.sql"
    ]


def test_release_changes_pages_and_says_so(mcp_client, release_claim, monkeypatch):
    from app.factory_mcp import RELEASE_FILES_PAGE

    files = [
        {"filename": f"src/f{i}.ts", "status": "modified"}
        for i in range(RELEASE_FILES_PAGE + 20)
    ]
    monkeypatch.setattr(
        "app.factory_mcp.github.compare_commits", _compare(files)
    )
    body = _changes(mcp_client, release_claim["prep_id"])
    assert body["truncated"] is True
    assert body["cursor"] == RELEASE_FILES_PAGE
    assert len(body["files"]) == RELEASE_FILES_PAGE
    rest = _changes(mcp_client, release_claim["prep_id"], cursor=body["cursor"])
    assert len(rest["files"]) == 20


def test_release_changes_refuses_a_prep_job_held_by_another_worker(
    mcp_client, monkeypatch
):
    prep_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.db.get_release_prep",
        lambda s, pid, org: _release_prep_row(
            prep_id, str(uuid.uuid4()), worker_id=str(uuid.uuid4())
        ),
    )
    body = _changes(mcp_client, prep_id)
    assert "you do not hold this release prep" in json.dumps(body)


def test_release_changes_refuses_an_unknown_prep_job(mcp_client, monkeypatch):
    monkeypatch.setattr(
        "app.db.get_release_prep", lambda s, pid, org: None
    )
    body = _changes(mcp_client, str(uuid.uuid4()))
    assert "release prep not found" in json.dumps(body)


# ---------------- US-43.1: submit_guidelines_refresh
#
# These live here rather than in test_guidelines_refresh.py beside the
# rest of US-43 because the MCP session manager can only start once per
# process — a second module-scoped client errors every test in it.


def _gr_run(run_id, kind="guidelines", worker_id=None):
    return {
        "id": run_id,
        "org_id": WORKER["org_id"],
        "project_id": PROJECT_ID,
        "issue_id": str(uuid.uuid4()),
        "worker_id": worker_id or WORKER["id"],
        "status": "running",
        "kind": kind,
    }


def _gr_section(**over):
    base = {
        "section_key": "tech-stack",
        "title": "Tech stack",
        "proposed_text": "Next.js 16, FastAPI, Supabase.",
        "rationale": "The stored section says Next 14; package.json says 16.",
        "severity": "major",
    }
    base.update(over)
    return base


def _gr_submit(mcp_client, run_id, **args):
    payload = {"run_id": run_id, "summary": "Read the repo.", "sections": []}
    payload.update(args)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "submit_guidelines_refresh", "arguments": payload},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    return resp.json()["result"]["structuredContent"]


def _stub_gr_submit(monkeypatch, run, captured):
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, r, org: run
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.list_guideline_section_keys",
        lambda s, p: ["tech-stack"],
    )

    def fake_record(s, worker, r, summary, sections):
        captured["sections"] = sections
        captured["summary"] = summary
        return {"ok": True, "refresh_id": "ref-1", "sections": len(sections)}

    monkeypatch.setattr("app.factory_mcp.db.record_guidelines_refresh", fake_record)
    monkeypatch.setattr(
        "app.factory_mcp.db.complete_run", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "app.routers.worker._store_handback_notes", lambda *a, **k: None
    )


def test_submit_records_the_whole_pass(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _stub_gr_submit(monkeypatch, _gr_run(run_id), captured)
    out = _gr_submit(
        mcp_client,
        run_id,
        sections=[_gr_section(), _gr_section(section_key="commands", title="Commands")],
    )
    assert out["ok"] is True
    assert out["sections_proposed"] == 2
    assert out["refresh_id"] == "ref-1"
    assert len(captured["sections"]) == 2


def test_an_empty_pass_is_a_legal_answer(mcp_client, monkeypatch):
    """"I read the repository and have nothing to propose" is worth saying,
    and it must not be refused into an agent inventing sections."""
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _stub_gr_submit(monkeypatch, _gr_run(run_id), captured)
    out = _gr_submit(mcp_client, run_id, sections=[])
    assert out["ok"] is True
    assert out["sections_proposed"] == 0
    assert "nothing to propose" in out["markdown"]


def test_a_catalog_key_the_project_has_not_filled_in_is_accepted(
    mcp_client, monkeypatch
):
    # Proposing a section that does not exist yet is most of the point — only
    # `tech-stack` exists on this project, and `deployment` must still pass.
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _stub_gr_submit(monkeypatch, _gr_run(run_id), captured)
    out = _gr_submit(
        mcp_client,
        run_id,
        sections=[_gr_section(section_key="deployment", title="Deployment and Release")],
    )
    assert out["ok"] is True


def test_an_unknown_section_key_is_refused_not_coerced(mcp_client, monkeypatch):
    # Silently turning it into a new section hides the mistake behind a
    # plausible-looking proposal.
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _stub_gr_submit(monkeypatch, _gr_run(run_id), captured)
    out = _gr_submit(
        mcp_client, run_id, sections=[_gr_section(section_key="deploymnet")]
    )
    assert "error" in out
    assert "deploymnet" in out["error"]
    assert "captured" not in captured or "sections" not in captured


def test_a_new_section_needs_a_title(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_gr_submit(monkeypatch, _gr_run(run_id), {})
    out = _gr_submit(
        mcp_client, run_id, sections=[_gr_section(section_key="", title="")]
    )
    assert "error" in out
    assert "title" in out["error"] or "title" in out["hint"]


def test_a_section_without_rationale_is_refused(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_gr_submit(monkeypatch, _gr_run(run_id), {})
    out = _gr_submit(mcp_client, run_id, sections=[_gr_section(rationale="")])
    assert "error" in out and "rationale" in out["error"]


def test_an_unknown_severity_is_refused(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_gr_submit(monkeypatch, _gr_run(run_id), {})
    out = _gr_submit(mcp_client, run_id, sections=[_gr_section(severity="urgent")])
    assert "error" in out and "severity" in out["error"]


def test_a_run_you_do_not_hold_is_refused(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _gr_run(run_id, worker_id=str(uuid.uuid4())),
    )
    out = _gr_submit(mcp_client, run_id)
    assert "error" in out and "do not hold" in out["error"]


def test_the_wrong_run_kind_is_refused(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, org: _gr_run(run_id, kind="code"),
    )
    out = _gr_submit(mcp_client, run_id)
    assert "error" in out and "code run" in out["error"]


def test_a_summary_is_required(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _stub_gr_submit(monkeypatch, _gr_run(run_id), {})
    out = _gr_submit(mcp_client, run_id, summary="   ")
    assert "error" in out and "summary" in out["error"]


def test_an_already_decided_refresh_does_not_mint_a_second_bundle(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr("app.factory_mcp.db.get_worker_run", lambda s, r, o: _gr_run(run_id))
    monkeypatch.setattr(
        "app.factory_mcp.db.list_guideline_section_keys", lambda s, p: []
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.record_guidelines_refresh",
        lambda *a, **k: {"ok": False, "reason": "no open refresh for this run"},
    )
    completed: list = []
    monkeypatch.setattr(
        "app.factory_mcp.db.complete_run",
        lambda *a, **k: completed.append(1) or True,
    )
    out = _gr_submit(mcp_client, run_id)
    assert "error" in out
    # And the run is NOT completed on the way out.
    assert not completed


# ---------------- US-44.1: submit_elaboration


def _el_run(run_id, kind="elaborate", worker_id=None):
    return {
        "id": run_id,
        "org_id": WORKER["org_id"],
        "project_id": PROJECT_ID,
        "issue_id": str(uuid.uuid4()),
        "worker_id": worker_id or WORKER["id"],
        "status": "running",
        "kind": kind,
    }


def _el_stub(monkeypatch, run, captured):
    monkeypatch.setattr("app.factory_mcp.db.get_worker_run", lambda s, r, o: run)

    def fake_record(s, r, story, criteria, questions, proposes):
        captured.update(
            story=story,
            criteria=criteria,
            questions=questions,
            proposes=proposes,
        )
        return {"id": "art-1", "version": 1}

    monkeypatch.setattr("app.factory_mcp.db.record_elaboration", fake_record)
    monkeypatch.setattr("app.factory_mcp.db.complete_run", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.routers.worker._store_handback_notes", lambda *a, **k: None
    )


def _el_submit(mcp_client, run_id, **args):
    payload = {"run_id": run_id}
    payload.update(args)
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "submit_elaboration", "arguments": payload},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    return resp.json()["result"]["structuredContent"]


def test_submit_elaboration_records_a_proposal(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _el_stub(monkeypatch, _el_run(run_id), captured)
    out = _el_submit(
        mcp_client,
        run_id,
        story="Rewritten against the actual codebase.",
        acceptance_criteria=["a happens", "b happens"],
        open_questions=["which table owns this?"],
    )
    assert out["ok"] is True
    assert out["proposes_change"] is True
    assert out["acceptance_criteria_count"] == 2
    assert captured["questions"] == ["which table owns this?"]


def test_criteria_given_as_one_string_are_coerced_not_refused(
    mcp_client, monkeypatch
):
    """US-42.1: fifteen runs each paid a full re-submit because a body
    validation error discards the WHOLE payload. A field's shape must never
    cost the hand-back."""
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _el_stub(monkeypatch, _el_run(run_id), captured)
    out = _el_submit(
        mcp_client,
        run_id,
        story="Rewritten.",
        acceptance_criteria="the only criterion",
    )
    assert out["ok"] is True
    assert captured["criteria"] == ["the only criterion"]


def test_criteria_given_as_a_newline_block_become_a_list(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _el_stub(monkeypatch, _el_run(run_id), captured)
    out = _el_submit(
        mcp_client,
        run_id,
        story="Rewritten.",
        acceptance_criteria="- first thing\n- second thing",
    )
    assert out["ok"] is True
    assert captured["criteria"] == ["first thing", "second thing"]


def test_an_empty_proposal_is_a_legal_answer(mcp_client, monkeypatch):
    # "this story is fine as written" costs the manager one glance and must
    # not push the agent into inventing a rewrite.
    run_id = str(uuid.uuid4())
    captured: dict = {}
    _el_stub(monkeypatch, _el_run(run_id), captured)
    out = _el_submit(mcp_client, run_id)
    assert out["ok"] is True
    assert out["proposes_change"] is False
    assert captured["proposes"] is False


def test_submit_elaboration_refuses_the_wrong_kind(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, o: _el_run(run_id, kind="plan"),
    )
    out = _el_submit(mcp_client, run_id, story="x")
    assert "error" in out and "plan run" in out["error"]


def test_submit_elaboration_refuses_a_run_you_do_not_hold(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run",
        lambda s, r, o: _el_run(run_id, worker_id=str(uuid.uuid4())),
    )
    out = _el_submit(mcp_client, run_id, story="x")
    assert "error" in out and "do not hold" in out["error"]


# --- external deployments: what the tools can and cannot do (US-50.3) --------
#
# An external deployment has no machine. The tools that would reach one must
# say so plainly rather than failing repeatedly, so the agent stops instead of
# retrying — and rollback must be refused outright, because there is nothing
# to put back.


def _ext_deploy_run(run_id, kind="deploy", **ic):
    return {
        "id": run_id,
        "org_id": WORKER["org_id"],
        "project_id": PROJECT_ID,
        "issue_id": None,
        "worker_id": WORKER["id"],
        "status": "running",
        "kind": kind,
        "input_context": {"deployment_id": str(uuid.uuid4()), **ic},
    }


def _ext_bundle(kind="external"):
    return {
        "deployment": {
            "id": str(uuid.uuid4()),
            "org_id": WORKER["org_id"],
            "name": "production",
            "kind": kind,
            "branch": "main",
            "target_branch": "prod",
            "environment": "production",
            "agent_dispatch_allowed": True,
            "server_id": None,
            "health_check_url": "",
            "strategy": "in-place",
        },
        "server": None,
        "project": {"id": PROJECT_ID, "name": "Webshop",
                    "repo_full_name": "acme/webshop"},
    }


def _ext_stub(monkeypatch, run, bundle):
    monkeypatch.setattr("app.factory_mcp.db.get_worker_run", lambda s, r, o: run)
    monkeypatch.setattr(
        "app.factory_mcp.db.extend_claim",
        lambda s, r, w, tool=None: {"id": r, "claim_expires_at": "later"},
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_deployment_for_agent", lambda s, d, o: bundle
    )


def _ext_call(mcp_client, tool, run_id):
    resp = mcp_client.post(
        "/mcp",
        json=_rpc("tools/call", {"name": tool, "arguments": {"run_id": run_id}}),
        headers=HDR,
    )
    assert resp.status_code == 200
    return resp.json()["result"]["structuredContent"]


def test_health_on_an_external_deployment_is_not_applicable_not_an_error(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    _ext_stub(monkeypatch, _ext_deploy_run(run_id), _ext_bundle())

    def never_connect(*a, **k):
        raise AssertionError("an external deployment has no server to reach")

    monkeypatch.setattr("app.factory_mcp.deploy.connect_to_server", never_connect)

    out = _ext_call(mcp_client, "get_deployment_health", run_id)
    assert "error" not in out
    assert out["applicable"] is False
    assert out["healthy"] is None
    assert "external" in out["markdown"].lower()


def test_rollback_is_refused_on_an_external_deployment(mcp_client, monkeypatch):
    run_id = str(uuid.uuid4())
    _ext_stub(
        monkeypatch,
        _ext_deploy_run(run_id, auto_rollback=True, deployment_run_id="dr-1"),
        _ext_bundle(),
    )

    def never(*a, **k):
        raise AssertionError("nothing may be rolled back")

    monkeypatch.setattr("app.factory_mcp.deploy.create_rollback_run", never)

    out = _ext_call(mcp_client, "trigger_deployment_rollback", run_id)
    assert "error" in out
    assert "not supported" in out["error"]
    assert "prod" in out["error"]


def test_a_factory_deployment_still_rolls_back(mcp_client, monkeypatch):
    """The refusal must be by kind and nothing else — a factory deployment
    still hits its own, older rails."""
    run_id = str(uuid.uuid4())
    _ext_stub(monkeypatch, _ext_deploy_run(run_id), _ext_bundle(kind="factory"))
    out = _ext_call(mcp_client, "trigger_deployment_rollback", run_id)
    assert "error" in out
    assert "pre-authorized" in out["error"]  # not the external refusal


def test_an_external_deploy_run_is_told_there_is_no_health_step(
    mcp_client, monkeypatch
):
    run_id = str(uuid.uuid4())
    _stub_work_context(
        monkeypatch,
        run_id,
        kind="deploy",
        extra_context={
            "deployment_id": str(uuid.uuid4()),
            "deployment": {
                "name": "production",
                "kind": "external",
                "branch": "main",
                "target_branch": "prod",
                "environment": "production",
            },
            # A pre-authorization cannot survive onto a kind with no rollback.
            "auto_rollback": True,
        },
    )
    resp = mcp_client.post(
        "/mcp",
        json=_rpc(
            "tools/call",
            {"name": "get_work_context", "arguments": {"run_id": run_id}},
        ),
        headers=HDR,
    )
    assert resp.status_code == 200
    out = resp.json()["result"]["structuredContent"]
    md = out["markdown"]
    assert "get_deployment_health" not in md
    assert "trigger_deployment_rollback" not in md
    assert "prod" in md
    # a pre-authorization cannot survive onto a kind that has no rollback
    assert out["auto_rollback"] is False
