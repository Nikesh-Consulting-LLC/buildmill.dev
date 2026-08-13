"""US-1.9/US-2.1: /api/v1/issues/{id}/dispatch — happy path, wrong status, cross-org."""

import uuid

from app.supabase import RpcError

ISSUE_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


def _patch_rpc(monkeypatch, behavior, prior_status="draft"):
    async def fake_rpc(settings, token, fn, args):
        assert fn == "dispatch_issue"
        assert args == {"p_issue": ISSUE_ID}
        return behavior()

    async def fake_get(settings, token, path, params):
        assert path == "issues"
        return [{"status": prior_status}]

    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)


def test_dispatch_happy_path(client, make_token, monkeypatch):
    _patch_rpc(monkeypatch, lambda: RUN_ID)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 202
    assert resp.json() == {"run_id": RUN_ID, "status": "queued"}


def test_dispatch_wrong_status_is_409(client, make_token, monkeypatch):
    def raise_not_dispatchable():
        raise RpcError('issue is not dispatchable from status "running"')

    _patch_rpc(monkeypatch, raise_not_dispatchable)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
    assert "not dispatchable" in resp.json()["detail"]


def test_dispatch_feature_owns_the_build_is_409(client, make_token, monkeypatch):
    """US-22.10: in feature/epic mode dispatch_issue refuses a story-level
    code run. The refusal must reach the manager as a 409 naming the feature
    to dispatch instead — not a bare 400."""

    def raise_owned():
        raise RpcError(
            "FEAT-1.4 owns the build — dispatch the feature to build all 5 stories"
        )

    _patch_rpc(monkeypatch, raise_owned, prior_status="planned")

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "owns the build" in detail
    assert "FEAT-1.4" in detail


def test_dispatch_passes_the_named_kind_through(client, make_token, monkeypatch):
    """Migration 166: a named phase reaches the RPC as `p_kind`.

    The whole point of the per-story Plan it / Code it actions is that the
    label the manager clicked is the run that happens. If the kind were dropped
    on the way through, the RPC would fall back to its inference and the two
    would silently disagree — the 2026-07-26 failure mode, one story at a time.
    """
    seen: dict = {}

    async def fake_rpc(settings, token, fn, args):
        seen.update(args)
        return RUN_ID

    async def fake_get(settings, token, path, params):
        return [{"status": "planned"}]

    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"kind": "plan"},
    )
    assert resp.status_code == 202
    assert seen == {"p_issue": ISSUE_ID, "p_kind": "plan"}


def test_dispatch_without_a_kind_leaves_the_rpc_to_infer(client, make_token, monkeypatch):
    """No kind means no `p_kind` argument at all — not an explicit null.

    Every existing caller goes through this path; it must reach PostgREST
    byte-identical to how it did before migration 166.
    """
    seen: dict = {}

    async def fake_rpc(settings, token, fn, args):
        seen.update(args)
        return RUN_ID

    async def fake_get(settings, token, path, params):
        return [{"status": "draft"}]

    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"preset_id": None},
    )
    assert resp.status_code == 202
    assert seen == {"p_issue": ISSUE_ID}


def test_dispatch_rejects_an_unknown_kind(client, make_token, monkeypatch):
    """The kind is a closed set, refused before it can reach the database."""
    called = False

    async def fake_rpc(settings, token, fn, args):
        nonlocal called
        called = True
        return RUN_ID

    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
        json={"kind": "review"},
    )
    assert resp.status_code == 422
    assert not called


def test_dispatch_illegal_named_kind_is_409(client, make_token, monkeypatch):
    """"Code it" on a story with no approved plan is a refusal the manager can
    act on, not a 400 that reads as a bug in the page."""

    def raise_needs_plan():
        raise RpcError("code run requires an approved plan")

    _patch_rpc(monkeypatch, raise_needs_plan, prior_status="planned")

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
    assert "approved plan" in resp.json()["detail"]


def test_dispatch_syncs_the_repo_after_the_run_exists(client, make_token, monkeypatch):
    """US-22.4/22.7: the write happens, and it happens AFTER the run is
    created — a GitHub outage must never stop work being dispatched."""
    project_id = str(uuid.uuid4())
    calls: list[str] = []

    async def fake_rpc(settings, token, fn, args):
        calls.append("dispatch")
        return RUN_ID

    async def fake_get(settings, token, path, params):
        return [{"status": "draft", "project_id": project_id}]

    async def fake_sync(settings, pid):
        calls.append(f"sync:{pid}")

    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.issues._sync_repo_before_dispatch", fake_sync)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 202
    assert calls == ["dispatch", f"sync:{project_id}"]


def test_dispatch_cross_org_is_404(client, make_token, monkeypatch):
    # RLS hides other orgs' issues entirely: the RPC sees no row.
    def raise_not_found():
        raise RpcError("issue not found")

    _patch_rpc(monkeypatch, raise_not_found)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_dispatch_without_token_is_401(client):
    resp = client.post(f"/api/v1/issues/{ISSUE_ID}/dispatch")
    assert resp.status_code == 401


def _revert_get(rows_by_path):
    async def fake_get(settings, token, path, params):
        if path in rows_by_path:
            return rows_by_path[path]
        raise AssertionError(f"unexpected path {path}")

    return fake_get


def test_revert_happy_path(client, make_token, monkeypatch):
    posted = {}

    fake_get = _revert_get(
        {
            "issues": [{"id": ISSUE_ID, "status": "merged", "title": "Add CSV export"}],
            "issue_events": [
                {"org_id": "org-1", "payload": {"pr_url": "https://github.com/acme/webshop/pull/9"}}
            ],
            "github_connections": [
                {"id": "c1", "method": "app", "installation_id": 42,
                 "vault_secret_id": None, "repos": []}
            ],
        }
    )

    async def fake_mint(settings, installation_id):
        return "installation-token"

    async def fake_get_pull(token, owner, repo, number):
        return {"node_id": "PR_kwDOabc"}

    async def fake_revert(token, node_id, title):
        return "https://github.com/acme/webshop/pull/10"

    async def fake_post(settings, token, path, body):
        posted.update(path=path, body=body)
        return [body]

    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.issues.postgrest_post", fake_post)
    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.issues.github.get_pull", fake_get_pull)
    monkeypatch.setattr("app.routers.issues.github.revert_pull_request", fake_revert)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"revert_pr_url": "https://github.com/acme/webshop/pull/10"}
    assert posted["path"] == "issue_events"
    assert posted["body"]["type"] == "reverted"


def test_revert_not_merged_is_409(client, make_token, monkeypatch):
    fake_get = _revert_get({"issues": [{"id": ISSUE_ID, "status": "in-review", "title": "x"}]})
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_revert_unknown_issue_is_404(client, make_token, monkeypatch):
    fake_get = _revert_get({"issues": []})
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_revert_simulated_pr_is_409(client, make_token, monkeypatch):
    fake_get = _revert_get(
        {
            "issues": [{"id": ISSUE_ID, "status": "merged", "title": "x"}],
            "issue_events": [{"org_id": "org-1", "payload": {"pr_url": "simulated://pr/health"}}],
        }
    )
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def test_revert_no_installation_is_409(
    client, make_token, monkeypatch, settings_override
):
    settings_override.github_token = ""  # hermetic: no env fallback either
    fake_get = _revert_get(
        {
            "issues": [{"id": ISSUE_ID, "status": "merged", "title": "x"}],
            "issue_events": [
                {"org_id": "org-1", "payload": {"pr_url": "https://github.com/acme/webshop/pull/9"}}
            ],
            "github_connections": [],
        }
    )
    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.postgrest_get", fake_get)

    resp = client.post(
        f"/api/v1/issues/{ISSUE_ID}/revert",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409
