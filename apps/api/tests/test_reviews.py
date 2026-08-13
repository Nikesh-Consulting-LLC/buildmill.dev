"""US-1.12/1.13: approve (simulated merge) and reject endpoints."""

import uuid

from app.supabase import RpcError

RUN_ID = str(uuid.uuid4())


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_approve_simulated_pr(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": "simulated://pr/health", "issue_id": "issue-1", "kind": "code", "org_id": "org-1"}]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(f"unexpected path {path}")

    called = {}

    async def fake_rpc(settings, token, fn, args):
        called["fn"] = fn
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.issue_sync.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "merge": "simulated"}
    assert called["fn"] == "approve_run"


def test_approve_unknown_run_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []  # RLS hides it

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 404


def test_approve_not_in_review_is_409_before_merge(client, make_token, monkeypatch):
    """A stale approve must be rejected BEFORE the irreversible GitHub merge.

    US-40.1: the refusal now comes from `approve_run_precheck` — the same
    predicate `approve_run` raises from — rather than from a second, narrower
    check written in the API.
    """

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/webshop/pull/9",
                    "issue_id": "issue-1",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        raise AssertionError(f"unexpected path {path}")

    async def fail_merge(token, owner, repo, number):
        raise AssertionError("merge must not run when the issue is not in review")

    async def fake_rpc(settings, token, fn, args):
        if fn == "approve_run_precheck":
            return 'issue is not in review (status "needs-fixes")'
        raise AssertionError("approve_run must not run when the precheck refuses")

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fail_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    assert 'not in review (status "needs-fixes")' in resp.json()["detail"]


def test_approve_feature_batch_with_unready_members_never_merges(
    client, make_token, monkeypatch
):
    """US-40.1 regression, 2026-07-28.

    The feature the run points at was `in-review`; its six member stories were
    `planned`. The old guard read only the feature, passed, merged PR #12 into
    `main`, and only then let `approve_run` refuse — shipping code the factory
    never recorded. The precheck reads the members, so nothing reaches GitHub.
    """
    members = (
        "Persistence layer (planned), Registration endpoint (planned), "
        "Login, logout (planned)"
    )

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/demo/pull/12",
                    "issue_id": "the-feature",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        # The feature itself IS in-review — reading it alone is what used to
        # let this through.
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(f"unexpected path {path}")

    async def fail_merge(token, owner, repo, number):
        raise AssertionError("a feature batch with unready members must not merge")

    async def fake_rpc(settings, token, fn, args):
        if fn == "approve_run_precheck":
            return f'issue is not in review (status "{members}")'
        raise AssertionError("approve_run must not run when the precheck refuses")

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fail_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    # The manager is told which stories, not just that something is wrong.
    assert "Persistence layer (planned)" in resp.json()["detail"]


def test_approve_plan_run_is_409(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": None, "issue_id": "issue-1", "kind": "plan", "org_id": "org-1"}]
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409


def test_approve_rpc_error_still_maps_to_409(client, make_token, monkeypatch):
    """Belt and braces: approve_run's own transactional check still surfaces."""

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [{"pr_url": "simulated://pr/x", "issue_id": "issue-1", "kind": "code", "org_id": "org-1"}]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        # US-40.1: the precheck agrees, and approve_run refuses anyway — the
        # race the precheck cannot close. The transaction stays the authority.
        if fn == "approve_run_precheck":
            return None
        raise RpcError('issue is not in review (status "merged")')

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409


def test_approve_records_merged_unapproved_when_rpc_fails_after_merge(
    client, make_token, monkeypatch
):
    """US-40.1: a real merge that lands and then fails to record is marked.

    The PR is on the default branch and cannot be un-merged, so the run has to
    say so — otherwise the split state exists only on GitHub.
    """
    patched = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/webshop/pull/9",
                    "issue_id": "issue-1",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        if fn == "approve_run_precheck":
            return None
        raise RpcError('issue is not in review (status "merged")')

    async def fake_merge(token, owner, repo, number):
        return "deadbeef"

    async def fake_token(settings, user_token, org_id, repo_full):
        return "gh-token", "the org's GitHub App installation (id 1)"

    async def fake_patch(settings, token, path, match, body):
        patched.update(path=path, body=body)

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fake_merge)
    monkeypatch.setattr(
        "app.routers.reviews.github_tokens.resolve_for_user", fake_token
    )
    monkeypatch.setattr("app.routers.reviews.postgrest_patch", fake_patch)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409
    assert patched["path"] == "runs"
    assert patched["body"]["merged_unapproved_at"]


def test_finish_approval_records_without_touching_github(
    client, make_token, monkeypatch
):
    """US-40.1: the way back from merged-but-unapproved.

    The normal approve path cannot repair this — it fails EARLIER, at the
    merge, because GitHub will not merge an already-merged PR.
    """
    called = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "id": RUN_ID,
                    "kind": "code", "org_id": "org-1",
                    "issue_id": "issue-1",
                    "merged_unapproved_at": "2026-07-28T01:05:09Z",
                }
            ]
        if path == "issues":
            return [{"status": "merged", "github_issue_number": None}]
        raise AssertionError(f"unexpected path {path}")

    async def fake_rpc(settings, token, fn, args):
        called["fn"] = fn
        return None

    async def fail_merge(token, owner, repo, number):
        raise AssertionError("finish-approval must never call GitHub")

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.issue_sync.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fail_merge)

    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/finish-approval", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()["merge"] == "already-merged"
    assert called["fn"] == "approve_run"


def test_finish_approval_on_a_healthy_run_is_409(client, make_token, monkeypatch):
    """It is a repair, not a second way to approve."""

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "id": RUN_ID,
                    "kind": "code", "org_id": "org-1",
                    "issue_id": "issue-1",
                    "merged_unapproved_at": None,
                }
            ]
        raise AssertionError(f"unexpected path {path}")

    async def fail_rpc(settings, token, fn, args):
        raise AssertionError("approve_run must not run for a healthy run")

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.rpc", fail_rpc)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/finish-approval", headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_reject_requires_comment(client, make_token):
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/reject",
        json={"comment": "   "},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422


def test_reject_happy_path(client, make_token, monkeypatch):
    called = {}

    async def fake_rpc(settings, token, fn, args):
        called.update(fn=fn, args=args)
        return None

    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/reject",
        json={"comment": "Version constant is hard-coded"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert called["fn"] == "reject_run"
    assert called["args"]["p_comment"] == "Version constant is hard-coded"


def test_reject_without_token_is_401(client):
    resp = client.post(f"/api/v1/runs/{RUN_ID}/reject", json={"comment": "x"})
    assert resp.status_code == 401


def test_approve_real_pr_uses_installation_token(client, make_token, monkeypatch):
    calls = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/webshop/pull/9",
                    "issue_id": "issue-1",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(path)

    async def fake_token_for_user(settings, user_token, org_id, repo_full_name=None):
        calls["repo_full_name"] = repo_full_name
        return "installation-token", "the org's GitHub App installation (id 1)"

    async def fake_merge(token, owner, repo, number):
        calls["token"] = token
        calls["pr"] = (owner, repo, number)

    async def fake_rpc(settings, token, fn, args):
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.issue_sync.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.routers.reviews.github_tokens.resolve_for_user", fake_token_for_user
    )
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fake_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["merge"] == "merged"
    assert calls["repo_full_name"] == "acme/webshop"
    assert calls["token"] == "installation-token"
    assert calls["pr"] == ("acme", "webshop", 9)


def test_approve_real_pr_falls_back_to_static_token(
    client, make_token, monkeypatch, settings_override
):
    settings_override.github_token = "static-token"
    calls = {}

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/webshop/pull/9",
                    "issue_id": "issue-1",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(path)

    async def fake_connections(settings, token, path, params):
        assert path == "github_connections"
        return []

    async def fake_merge(token, owner, repo, number):
        calls["token"] = token

    async def fake_rpc(settings, token, fn, args):
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.issue_sync.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_connections)
    monkeypatch.setattr("app.routers.reviews.github.merge_pull_request", fake_merge)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 200
    assert calls["token"] == "static-token"


def test_approve_real_pr_no_installation_no_token_is_409(
    client, make_token, monkeypatch, settings_override
):
    settings_override.github_token = ""  # hermetic: no env fallback either

    async def fake_get(settings, token, path, params):
        if path == "runs":
            return [
                {
                    "pr_url": "https://github.com/acme/webshop/pull/9",
                    "issue_id": "issue-1",
                    "kind": "code", "org_id": "org-1",
                }
            ]
        if path == "issues":
            return [{"status": "in-review", "github_issue_number": None}]
        raise AssertionError(path)

    async def fake_connections(settings, token, path, params):
        assert path == "github_connections"
        return []

    async def fake_rpc(settings, token, fn, args):
        # US-40.1: approve now consults approve_run_precheck before it merges.
        # This test is about the missing GitHub credential, so let the
        # precheck agree and keep the case hermetic.
        assert fn == "approve_run_precheck"
        return None

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_connections)
    monkeypatch.setattr("app.routers.reviews.rpc", fake_rpc)

    resp = client.post(f"/api/v1/runs/{RUN_ID}/approve", headers=_auth(make_token))
    assert resp.status_code == 409


# --------------------------------------- US-13.6: force-requeue


def test_force_requeue_running_run(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        assert path == "runs"
        return [{"id": RUN_ID, "status": "running", "worker_id": "w-1"}]

    captured = {}

    def fake_requeue(settings, run_id, note=None):
        captured["run_id"] = run_id
        captured["note"] = note
        return True

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.db.force_requeue_run", fake_requeue)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/force-requeue", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "queued"}
    assert captured["run_id"] == RUN_ID
    assert "silent" in captured["note"]


def test_force_requeue_refuses_non_running(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"id": RUN_ID, "status": "queued", "worker_id": None}]

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/force-requeue", headers=_auth(make_token)
    )
    assert resp.status_code == 409


def test_force_requeue_unknown_run_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/force-requeue", headers=_auth(make_token)
    )
    assert resp.status_code == 404


# ------------------------------------------------- US-59.7: abandon


def test_abandon_a_paused_run(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"id": RUN_ID, "org_id": "org-1", "status": "paused"}]

    captured = {}

    def fake_abandon(settings, run_id, org_id, *, reason, member=None):
        captured.update(run_id=run_id, org_id=org_id, reason=reason, member=member)
        return True

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.db.abandon_run", fake_abandon)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/abandon",
        json={"reason": "not coming back"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "abandoned"}
    assert captured["run_id"] == RUN_ID
    assert captured["org_id"] == "org-1"
    assert captured["reason"] == "not coming back"


def test_abandon_requires_a_reason(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"id": RUN_ID, "org_id": "org-1", "status": "paused"}]

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/abandon", json={"reason": "  "},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422


def test_abandon_refuses_a_run_that_is_not_parked(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [{"id": RUN_ID, "org_id": "org-1", "status": "succeeded"}]

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/abandon", json={"reason": "stale"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 409


def test_abandon_unknown_run_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/runs/{RUN_ID}/abandon", json={"reason": "stale"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


# ------------------------------------------------- US-59.3: manual resume


def test_resume_a_stopped_run_with_a_session(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [
            {
                "id": RUN_ID,
                "org_id": "org-1",
                "status": "stopped",
                "claude_session_id": "sess-1",
            }
        ]

    captured = {}

    def fake_mark(settings, run_id, org_id, member):
        captured.update(run_id=run_id, org_id=org_id, member=member)
        return True

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.reviews.db.mark_stopped_resumable", fake_mark)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/resume", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "status": "paused"}
    assert captured["run_id"] == RUN_ID


def test_resume_refuses_a_stopped_run_with_no_session(client, make_token, monkeypatch):
    """A `stopped` run from before Phase 59 has nothing to resume into."""

    async def fake_get(settings, token, path, params):
        return [
            {
                "id": RUN_ID,
                "org_id": "org-1",
                "status": "stopped",
                "claude_session_id": None,
            }
        ]

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/resume", headers=_auth(make_token))
    assert resp.status_code == 409


def test_resume_refuses_a_run_that_is_not_stopped(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return [
            {
                "id": RUN_ID,
                "org_id": "org-1",
                "status": "running",
                "claude_session_id": "sess-1",
            }
        ]

    monkeypatch.setattr("app.routers.reviews.postgrest_get", fake_get)
    resp = client.post(f"/api/v1/runs/{RUN_ID}/resume", headers=_auth(make_token))
    assert resp.status_code == 409
