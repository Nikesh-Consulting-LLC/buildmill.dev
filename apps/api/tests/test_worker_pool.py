"""US-3.2: worker pool API — auth, pool, claim, context, submit, release.

Endpoint-level: db and GitHub helpers are monkeypatched; the SQL layer
is covered in test_worker_pool_sql.py.
"""

import uuid

import pytest

RUN_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())

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
    def fake_lookup(settings, token):
        return dict(WORKER) if token == "sfw_testtoken" else None

    monkeypatch.setattr("app.routers.worker.db.get_worker_by_token", fake_lookup)
    # US-31.3: granted by default in these tests; the capability test
    # overrides this with a named refusal.
    monkeypatch.setattr(
        "app.routers.worker.db.worker_run_refusal", lambda s, w, r: None
    )
    return WORKER


@pytest.fixture(autouse=True)
def stub_instructions(monkeypatch):
    """US-5.14/US-5.12: context serving reads the project's instruction
    template and the comment thread live; stub the db reads so endpoint
    tests stay database-free."""
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_instruction",
        lambda s, project_id, kind, issue_id=None: f"Stub {kind} template.",
    )
    monkeypatch.setattr(
        "app.routers.worker.db.list_issue_comments_for_run",
        lambda s, r, org: [],
    )
    # US-7.3: the branch resolver + branch_ref writer hit the DB; stub to the
    # deterministic legacy name and a no-op so context serving stays DB-free.
    monkeypatch.setattr(
        "app.routers.worker.db.resolve_working_branch",
        lambda s, run: (
            f"factory/issue-{run.get('issue_id')}",
            (run.get("dev_branch_strategy") or "story"),
            "direct" if run.get("dev_branch_strategy") == "main" else "pr",
        ),
    )
    monkeypatch.setattr(
        "app.routers.worker.db.set_run_branch_ref", lambda s, rid, b: None
    )
    # US-31.2: the bundle carries the claim lease; stub the config read.
    monkeypatch.setattr(
        "app.routers.worker.db.worker_lease_seconds", lambda s, w, t, run_id=None: 900
    )
    # US-17.4: submit consults the project's auto-approve switches; all-off
    # here so these tests keep exercising the review path (the switches
    # themselves are covered in test_auto_approve_sql.py).
    monkeypatch.setattr(
        "app.routers.worker.db.get_project_auto_flags", lambda s, project_id: {}
    )
    # US-49.7: a code submit asks which stories share the run; single-story
    # here, so coverage attribution never engages.
    monkeypatch.setattr(
        "app.routers.worker.db.run_members", lambda s, run_id: []
    )
    # US-59.5: a non-success submit checks for a pending clarification before
    # deciding failed/paused/stopped; no open question in these tests unless
    # a specific test overrides it.
    monkeypatch.setattr(
        "app.routers.worker.db.has_pending_clarification", lambda s, run: False
    )


def test_pool_requires_token(client, worker_auth):
    assert client.get("/api/v1/worker/pool").status_code == 401
    assert (
        client.get(
            "/api/v1/worker/pool", headers={"X-Worker-Token": "wrong"}
        ).status_code
        == 401
    )


def test_pool_lists_claimable_runs(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.list_worker_pool",
        lambda settings, org_id: [
            {
                "id": RUN_ID,
                "kind": "code",
                "issue_id": ISSUE_ID,
                "issue_title": "Add CSV export",
                "issue_type": "story",
                "project_name": "Webshop",
                "repo_full_name": "acme/webshop",
            }
        ],
    )
    resp = client.get("/api/v1/worker/pool", headers=HDR)
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert runs[0]["id"] == RUN_ID
    assert runs[0]["issue_title"] == "Add CSV export"
    assert runs[0]["repo_full_name"] == "acme/webshop"
    # us-59.9: no pinned resumable work for this worker in this test.
    assert resp.json()["resumable"] == []


def test_pool_lists_own_resumable_runs(client, worker_auth, monkeypatch):
    """US-59.9: a worker's own parked runs ride the same pool response, so
    it can resume them before claiming fresh work."""
    monkeypatch.setattr(
        "app.routers.worker.db.list_worker_pool", lambda settings, org_id: []
    )
    monkeypatch.setattr(
        "app.routers.worker.db.list_worker_resumable",
        lambda settings, worker: [
            {
                "id": RUN_ID,
                "kind": "code",
                "status": "paused",
                "issue_id": ISSUE_ID,
                "resume_reason": "turn_limit",
                "issue_title": "Add CSV export",
            }
        ],
    )
    resp = client.get("/api/v1/worker/pool", headers=HDR)
    assert resp.status_code == 200
    resumable = resp.json()["resumable"]
    assert resumable[0]["id"] == RUN_ID
    assert resumable[0]["status"] == "paused"


def test_pool_resumable_listing_failure_never_blocks_the_pool(
    client, worker_auth, monkeypatch
):
    """A resumable-list hiccup must not take down the ordinary pool."""
    monkeypatch.setattr(
        "app.routers.worker.db.list_worker_pool", lambda settings, org_id: []
    )

    def exploding(settings, worker):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.routers.worker.db.list_worker_resumable", exploding)
    resp = client.get("/api/v1/worker/pool", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["resumable"] == []


def test_resume_claim_continues_a_paused_run(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.resume_claim",
        lambda settings, run_id, worker: {
            "id": run_id,
            "kind": "code",
            "claim_expires_at": None,
        },
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/resume-claim", headers=HDR
    )
    assert resp.status_code == 200
    assert resp.json()["run"]["id"] == RUN_ID


def test_resume_claim_refuses_a_run_it_does_not_own(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.resume_claim",
        lambda settings, run_id, worker: None,
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/resume-claim", headers=HDR
    )
    assert resp.status_code == 409


def test_claim_success(client, worker_auth, monkeypatch):
    captured = {}

    def fake_claim(settings, run_id, worker):
        captured.update(run_id=run_id, worker=worker["id"])
        return {"id": run_id, "kind": "code", "claim_expires_at": "2026-07-15T12:00:00Z"}

    monkeypatch.setattr("app.routers.worker.db.claim_run", fake_claim)
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/claim", headers=HDR)
    assert resp.status_code == 200
    assert resp.json()["run"]["id"] == RUN_ID
    assert captured["run_id"] == RUN_ID


def test_claim_race_lost_is_409(client, worker_auth, monkeypatch):
    monkeypatch.setattr("app.routers.worker.db.claim_run", lambda s, r, w: None)
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {"id": r, "status": "running"},
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/claim", headers=HDR)
    assert resp.status_code == 409


def test_claim_unknown_run_is_404(client, worker_auth, monkeypatch):
    monkeypatch.setattr("app.routers.worker.db.claim_run", lambda s, r, w: None)
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: None
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/claim", headers=HDR)
    assert resp.status_code == 404


def test_claim_outside_capabilities_is_403(client, worker_auth, monkeypatch):
    # US-3.12: a readable refusal distinct from a lost race — and the
    # claim is never attempted.
    def never_claim(s, r, w):
        raise AssertionError("claim_run must not run when capabilities deny")

    monkeypatch.setattr("app.routers.worker.db.claim_run", never_claim)
    monkeypatch.setattr(
        "app.routers.worker.db.worker_run_refusal",
        lambda s, w, r: (
            "this agent does not have access to that project — give it "
            "access on its Team page"
        ),
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/claim", headers=HDR)
    assert resp.status_code == 403
    # US-31.3/55.1: the refusal names the missing half.
    assert "does not have access to that project" in resp.json()["detail"]


def test_context_bundle(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": r,
            "org_id": ORG_ID,
            "worker_id": WORKER["id"],
            "status": "running",
            "kind": "code",
            "issue_id": ISSUE_ID,
            "project_id": PROJECT_ID,
            "org_shortname": "acme",
            "project_slug": "webshop",
            "input_context": {
                "title": "Add CSV export",
                "repo_full_name": "acme/webshop",
                "default_branch": "main",
            },
        },
    )
    monkeypatch.setattr(
        "app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True
    )
    resp = client.get(f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR)
    assert resp.status_code == 200
    ctx = resp.json()
    assert ctx["context"]["title"] == "Add CSV export"
    assert ctx["branch_name"] == f"factory/issue-{ISSUE_ID}"
    # US-3.13: readable remote — org shortname + project slug, not a uuid
    assert ctx["git_remote_url"].endswith("/git/acme/webshop.git")
    assert ctx["repo_full_name"] == "acme/webshop"


def test_context_of_foreign_claim_is_409(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": r,
            "worker_id": str(uuid.uuid4()),
            "status": "running",
            "kind": "code",
            "issue_id": ISSUE_ID,
            "project_id": PROJECT_ID,
            "input_context": {},
        },
    )
    resp = client.get(f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR)
    assert resp.status_code == 409


def test_heartbeat_extends_lease(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/heartbeat", headers=HDR)
    assert resp.status_code == 200


def test_heartbeat_without_claim_is_409(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: False
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/heartbeat", headers=HDR)
    assert resp.status_code == 409


def _own_run(kind):
    return {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "worker_id": WORKER["id"],
        "status": "running",
        "kind": kind,
        "issue_id": ISSUE_ID,
        "project_id": PROJECT_ID,
        "issue_title": "Add CSV export",
        "input_context": {
            "repo_full_name": "acme/webshop",
            "default_branch": "main",
        },
    }


def test_submit_plan_flows_to_plan_review(client, worker_auth, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("plan")
    )

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(outcome=outcome, plan=kw.get("plan"), worker=kw.get("worker_name"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"plan": "# Plan", "test_plan": "# Test plan"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["outcome"] == "succeeded"
    assert captured["plan"] == "# Plan"
    assert captured["worker"] == WORKER["name"]


def test_submit_plan_requires_plan_body(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("plan")
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit", json={}, headers=HDR
    )
    assert resp.status_code == 422


def test_submit_plan_warns_on_zero_case_test_plan(client, worker_auth, monkeypatch):
    """US-5.21: a test plan the gate's parser can't read comes back as a
    warning in the submit response AND as an issue event the plan-review
    page shows the manager."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("plan")
    )
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)

    def fake_event(settings, org_id, issue_id, event_type, payload):
        captured["event"] = (event_type, payload)

    monkeypatch.setattr("app.routers.worker.db.record_issue_event", fake_event)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"plan": "# Plan", "test_plan": "prose without a JSON fence"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert any("0 cases" in w for w in resp.json()["warnings"])
    assert captured["event"][0] == "submission-findings"
    assert captured["event"][1]["findings"]


def test_submit_breakdown_creates_stories_and_returns_ready(
    client, worker_auth, monkeypatch
):
    """US-2.33: a breakdown submit hands the split to complete_run (which
    auto-creates the draft children) and the feature returns to 'ready'."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _own_run("breakdown"),
    )

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(outcome=outcome, stories=kw.get("stories"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    monkeypatch.setattr(
        "app.routers.worker.db.child_story_ids", lambda s, iid: []
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"stories": [{"title": "Auth", "body": "b", "acceptance_criteria": []}]},
        headers=HDR,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["issue_status"] == "ready"
    assert captured["outcome"] == "succeeded"
    assert captured["stories"] == [
        {"title": "Auth", "body": "b", "acceptance_criteria": []}
    ]


def test_submit_breakdown_requires_stories(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _own_run("breakdown"),
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit", json={}, headers=HDR
    )
    assert resp.status_code == 422


def test_submit_prd_warns_on_missing_sections(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("prd")
    )
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"prd": "## Problem\n\nonly this"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert any("## Goals" in w for w in resp.json()["warnings"])


def test_submit_code_verifies_branch_and_opens_pr(client, worker_auth, monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )
    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def fake_branch(token, owner, repo, branch):
        captured["branch_checked"] = branch
        return {"name": branch}

    async def fake_open_pulls(token, owner, repo):
        return []

    async def fake_create_pull(token, owner, repo, head, base, title, body=""):
        captured["pr_created"] = (head, base)
        return {"html_url": "https://github.com/acme/webshop/pull/7"}

    async def fake_diff(token, owner, repo, base, head):
        return "diff --git a/x b/x"

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)
    monkeypatch.setattr("app.routers.worker.github.get_branch", fake_branch)
    monkeypatch.setattr("app.routers.worker.github.list_open_pulls", fake_open_pulls)
    monkeypatch.setattr("app.routers.worker.github.create_pull", fake_create_pull)
    monkeypatch.setattr("app.routers.worker.github.get_compare_diff", fake_diff)

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(pr=pr, diff=diff, branch=branch)
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": f"factory/issue-{ISSUE_ID}"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["branch_checked"] == f"factory/issue-{ISSUE_ID}"
    assert captured["pr_created"] == (f"factory/issue-{ISSUE_ID}", "main")
    assert captured["pr"] == "https://github.com/acme/webshop/pull/7"
    assert captured["diff"] == "diff --git a/x b/x"


def test_multistory_close_asks_the_record_not_the_walk(
    client, worker_auth, monkeypatch
):
    """2026-08-13 (FEAT-2.8): record_changeset_coverage is idempotent, so an
    MCP multi-story run — whose coverage rows were all written at apply time —
    walked the branch to ZERO new rows, and the close gate read zero as
    "carries no story attribution". Eleven fully attributed stories could
    never close. The verdict must ask the RECORD (run_members' landed flag),
    not count the walk's own inserts."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def fake_branch(token, owner, repo, branch):
        return {"name": branch}

    async def fake_open_pulls(token, owner, repo):
        return []

    async def fake_create_pull(token, owner, repo, head, base, title, body=""):
        captured["pr_created"] = (head, base)
        return {"html_url": "https://github.com/acme/webshop/pull/9"}

    async def fake_diff(token, owner, repo, base, head):
        return "diff --git a/x b/x"

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)
    monkeypatch.setattr("app.routers.worker.github.get_branch", fake_branch)
    monkeypatch.setattr("app.routers.worker.github.list_open_pulls", fake_open_pulls)
    monkeypatch.setattr("app.routers.worker.github.create_pull", fake_create_pull)
    monkeypatch.setattr("app.routers.worker.github.get_compare_diff", fake_diff)
    monkeypatch.setattr(
        "app.routers.worker.db.complete_run",
        lambda s, run_id, outcome, stdout, diff, branch, pr, error, **kw: True,
    )

    # The walk writes nothing new — every row already recorded at apply time.
    async def zero_walk(*a, **kw):
        return 0

    monkeypatch.setattr(
        "app.routers.worker._attribute_branch_coverage", zero_walk
    )

    def members(landed_a, landed_b):
        return [
            {"issue_id": str(uuid.uuid4()), "display_id": "US-1.1",
             "position": 1, "landed": landed_a},
            {"issue_id": str(uuid.uuid4()), "display_id": "US-1.2",
             "position": 2, "landed": landed_b},
        ]

    # Record says the work landed → the close succeeds despite the zero walk.
    monkeypatch.setattr(
        "app.routers.worker.db.run_members",
        lambda s, run_id: members(True, True),
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": f"factory/issue-{ISSUE_ID}"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["pr_created"] == (f"factory/issue-{ISSUE_ID}", "main")

    # Record empty → the honest refusal stays.
    monkeypatch.setattr(
        "app.routers.worker.db.run_members",
        lambda s, run_id: members(False, False),
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": f"factory/issue-{ISSUE_ID}"},
        headers=HDR,
    )
    assert resp.status_code == 422
    assert "no story attribution" in resp.json()["detail"]


def test_submit_code_direct_mode_no_pr_merges(client, worker_auth, monkeypatch):
    """US-7.15: a main-strategy (direct) code submit commits to the default
    branch, opens NO PR, and the item lands merged (review bypassed)."""
    captured = {}
    run = {**_own_run("code"), "dev_branch_strategy": "main", "default_branch": "main"}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: run
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def fake_branch(token, owner, repo, branch):
        captured["branch_checked"] = branch
        return {"name": branch}

    async def no_pull(*a, **k):
        raise AssertionError("direct mode must not open a PR")

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)
    monkeypatch.setattr("app.routers.worker.github.get_branch", fake_branch)
    monkeypatch.setattr("app.routers.worker.github.list_open_pulls", no_pull)
    monkeypatch.setattr("app.routers.worker.github.create_pull", no_pull)

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(pr=pr, direct=kw.get("direct"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": "main"},
        headers=HDR,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["issue_status"] == "merged"
    assert body["pr_url"] is None
    assert body.get("landed") == "direct"
    assert captured["pr"] is None
    assert captured["direct"] is True


def test_submit_code_missing_branch_is_422(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )
    async def fake_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def fake_branch(token, owner, repo, branch):
        from app.github import GitHubError

        raise GitHubError("branch not found")

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)
    monkeypatch.setattr("app.routers.worker.github.get_branch", fake_branch)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": "factory/issue-x"},
        headers=HDR,
    )
    assert resp.status_code == 422


def test_submit_code_not_configured_names_manager(client, worker_auth, monkeypatch):
    """US-5.24 (a): no connection → the detail says the manager must
    connect one, and never tells the worker to push."""
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        from app.github import GitHubNotConfigured

        raise GitHubNotConfigured(
            "the org has no GitHub connection — the manager must connect "
            "one in Settings → GitHub"
        )

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": "factory/issue-x"},
        headers=HDR,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "manager must connect" in detail
    assert "push" not in detail


def test_submit_code_credential_failure_names_manager(client, worker_auth, monkeypatch):
    """US-5.24 (b): App/installation mismatch → reconnect message carrying
    the upstream status, never "push the branch first"."""
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )

    async def fake_token(settings, org_id, repo_full_name=None):
        from app.github import mint_error

        raise mint_error(404)

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", fake_token)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": "factory/issue-x"},
        headers=HDR,
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "404" in detail
    assert "reconnect GitHub" in detail
    assert "push" not in detail


def test_submit_code_taxonomy_hints_name_fix_owner(monkeypatch):
    """US-5.24: the hint rides the HTTPException for the MCP layer —
    manager-owned for credential failures, worker-owned for a missing
    branch."""
    import asyncio

    from fastapi import HTTPException

    from app.config import Settings
    from app.github import GitHubError, mint_error
    from app.routers.worker import Submit, perform_submit

    settings = Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
    )
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )

    async def cred_boom(settings, org_id, repo_full_name=None):
        raise mint_error(401)

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", cred_boom)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            perform_submit(settings, WORKER, RUN_ID, Submit(branch_ref="b"))
        )
    assert "manager" in ei.value.hint
    assert "push" not in ei.value.hint

    async def ok_token(settings, org_id, repo_full_name=None):
        return "ghs_test"

    async def missing_branch(token, owner, repo, branch):
        raise GitHubError(f"branch '{branch}' not found")

    monkeypatch.setattr("app.routers.worker.github_tokens.token_for_org", ok_token)
    monkeypatch.setattr("app.routers.worker.github.get_branch", missing_branch)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            perform_submit(settings, WORKER, RUN_ID, Submit(branch_ref="b"))
        )
    assert "push the branch" in ei.value.hint


def test_submit_reclaimed_by_other_worker_is_409(client, worker_auth, monkeypatch):
    run = _own_run("code")
    run["worker_id"] = str(uuid.uuid4())
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: run
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"branch_ref": "factory/issue-x"},
        headers=HDR,
    )
    assert resp.status_code == 409


def test_submit_idempotent_prd_reports_prd_review(client, worker_auth, monkeypatch):
    """US-3.21: a duplicate/lease-expiry-reconciler resubmit of an
    already-succeeded prd run must report issue_status "prd-review",
    not the code/plan defaults."""
    run = _own_run("prd")
    run["status"] = "succeeded"
    run["pr_url"] = None
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: run
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"prd": "# PRD"},
        headers=HDR,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["idempotent"] is True
    assert body["issue_status"] == "prd-review"


def test_submit_failure_marks_run_failed(client, worker_auth, monkeypatch):
    """US-3.5: a worker can report a failed run — the old callback's
    failure path survives the move onto the worker surface."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )
    monkeypatch.setattr(
        "app.issue_sync.db.get_issue_sync_context", lambda s, issue_id: None
    )

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(outcome=outcome, error=error, worker=kw.get("worker_name"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"error": "provider crashed", "stdout": "log tail"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["outcome"] == "failed"
    assert captured["error"] == "provider crashed"
    assert captured["worker"] == WORKER["name"]


def test_submit_simulated_run_carries_posted_diff(client, worker_auth, monkeypatch):
    """US-3.5 parity: the simulator posts simulated:// PRs and a diff —
    accepted only for simulated URLs, never for real runs."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )

    def fake_complete(settings, run_id, outcome, stdout, diff, branch, pr, error, **kw):
        captured.update(diff=diff, pr=pr)
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={
            "pr_url": "simulated://pr/x",
            "diff": "diff --git a/sim b/sim",
            "branch_ref": "factory/sim",
        },
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["pr"] == "simulated://pr/x"
    assert captured["diff"] == "diff --git a/sim b/sim"


def test_submit_rejects_posted_diff_for_real_runs(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("code")
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"pr_url": "https://github.com/acme/webshop/pull/9", "diff": "x"},
        headers=HDR,
    )
    assert resp.status_code == 422


def test_release_returns_run_to_pool(client, worker_auth, monkeypatch):
    captured = {}

    def fake_release(settings, run_id, worker, note=None):
        captured.update(run_id=run_id, note=note)
        return True

    monkeypatch.setattr("app.routers.worker.db.release_claim", fake_release)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/release",
        json={"note": "out of my depth"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["note"] == "out of my depth"


def test_release_without_claim_is_409(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.release_claim", lambda s, r, w, note=None: False
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/release", json={}, headers=HDR
    )
    assert resp.status_code == 409


def test_prd_context_has_no_git_fields(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "input_context": {"title": "A feature"},
        },
    )
    monkeypatch.setattr("app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True)
    resp = client.get(f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR)
    assert resp.status_code == 200
    body = resp.json()
    assert "git_remote_url" not in body
    assert "branch_name" not in body
    assert body["context"]["title"] == "A feature"


def _context_run(kind):
    return {
        "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
        "worker_id": WORKER["id"], "status": "running", "kind": kind,
        "project_id": PROJECT_ID, "org_shortname": "acme",
        "project_slug": "webshop", "input_context": {"title": "A feature"},
    }


def test_context_instructions_compose_mechanics_plus_template(
    client, worker_auth, monkeypatch
):
    """US-5.14: mechanics stay code-generated; the editable template is
    appended — for git-backed kinds and prd runs alike."""
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _context_run("code"),
    )
    monkeypatch.setattr("app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True)
    body = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR
    ).json()
    assert f"factory/issue-{ISSUE_ID}" in body["instructions"]  # mechanics
    assert body["instructions"].endswith("Stub code template.")

    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _context_run("prd"),
    )
    body = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR
    ).json()
    assert "no repo, no branch" in body["instructions"]  # mechanics
    assert body["instructions"].endswith("Stub prd template.")


def test_comment_posts_to_claimed_run(client, worker_auth, monkeypatch):
    """US-5.12: a claim-holder comments on its run's work item; posting
    extends the lease like a heartbeat."""
    captured = {}

    def fake_add(settings, run, worker, body):
        captured.update(issue_id=run["issue_id"], body=body)
        return {"id": str(uuid.uuid4()), "created_at": "now"}

    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _context_run("code"),
    )
    monkeypatch.setattr("app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True)
    monkeypatch.setattr("app.routers.worker.db.add_worker_comment", fake_add)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/comment",
        json={"body": "Started on the export module."},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["body"] == "Started on the export module."
    assert captured["issue_id"] == ISSUE_ID


def test_comment_requires_claim_and_body(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {**_context_run("code"), "worker_id": str(uuid.uuid4())},
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/comment",
        json={"body": "not my run"},
        headers=HDR,
    )
    assert resp.status_code == 409

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/comment",
        json={"body": "   "},
        headers=HDR,
    )
    assert resp.status_code == 422


def test_context_instructions_survive_missing_template(
    client, worker_auth, monkeypatch
):
    """A None template (unknown project) still yields the mechanics."""
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_instruction",
        lambda s, project_id, kind, issue_id=None: None,
    )
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: _context_run("code"),
    )
    monkeypatch.setattr("app.routers.worker.db.extend_claim", lambda s, r, w, tool=None: True)
    body = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/context", headers=HDR
    ).json()
    assert f"factory/issue-{ISSUE_ID}" in body["instructions"]
    assert not body["instructions"].endswith("template.")


def test_prd_submit_requires_prd_field(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "input_context": {},
        },
    )
    resp = client.post(f"/api/v1/worker/runs/{RUN_ID}/submit", headers=HDR, json={})
    assert resp.status_code == 422


def test_prd_submit_reaches_prd_review(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, r, org: {
            "id": RUN_ID, "org_id": ORG_ID, "issue_id": ISSUE_ID,
            "worker_id": WORKER["id"], "status": "running", "kind": "prd",
            "project_id": PROJECT_ID, "input_context": {},
        },
    )
    captured = {}

    def fake_complete(settings, run_id, outcome, stdout, diff, branch_ref, pr_url, error, **kw):
        captured.update(outcome=outcome, prd=kw.get("prd"))
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"prd": "## Problem\n\nx\n"},
    )
    assert resp.status_code == 200
    assert resp.json()["issue_status"] == "prd-review"
    assert captured["prd"] == "## Problem\n\nx\n"


# --------------------------------------- US-13.3: hand-back notes


def test_submit_plan_notes_reach_the_manager(client, worker_auth, monkeypatch):
    """US-13.3: notes on a submit are stored on the run AND mirrored into
    the item's thread by the submission itself — no separate add_comment
    call (which a worker-side allow-list can deny) is required."""
    captured = {}
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("plan")
    )
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.routers.worker.db.set_run_handback_notes",
        lambda s, rid, notes: captured.update(stored=notes),
    )
    monkeypatch.setattr(
        "app.routers.worker.db.add_worker_comment",
        lambda s, run, w, body: captured.update(comment=body) or {"id": "c1"},
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={
            "plan": "# Plan",
            "test_plan": "# Test plan",
            "notes": "Cookies travel over plain HTTP on the UAT host.",
        },
        headers=HDR,
    )
    assert resp.status_code == 200
    assert captured["stored"] == "Cookies travel over plain HTTP on the UAT host."
    assert "plain HTTP" in captured["comment"]
    assert "Notes for the manager" in captured["comment"]


def test_submit_without_notes_stores_nothing(client, worker_auth, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("prd")
    )
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)

    def boom(*a, **k):
        raise AssertionError("notes storage must not run without notes")

    monkeypatch.setattr("app.routers.worker.db.set_run_handback_notes", boom)
    monkeypatch.setattr("app.routers.worker.db.add_worker_comment", boom)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"prd": "## Problem\n\nx"},
        headers=HDR,
    )
    assert resp.status_code == 200


def test_notes_storage_failure_never_fails_the_submit(
    client, worker_auth, monkeypatch
):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run", lambda s, r, org: _own_run("plan")
    )
    monkeypatch.setattr("app.routers.worker.db.complete_run", lambda *a, **k: True)

    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr("app.routers.worker.db.set_run_handback_notes", boom)
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        json={"plan": "# Plan", "notes": "flag this"},
        headers=HDR,
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
