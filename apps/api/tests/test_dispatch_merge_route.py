"""US-98.2: /api/v1/issues/{id}/dispatch-merge.

The endpoint exists to do one thing SQL cannot: resolve every named branch's
head from GitHub before the run is created, so that

  * the shas frozen onto the run are current at dispatch rather than at the
    moment the manager picked the branches, and
  * a branch deleted since it was listed fails the dispatch BY NAME, instead
    of sixty seconds into an agent's checkout.

These pin both, plus the refusals on the way through.
"""

import uuid

import pytest

from app import github
from app.supabase import RpcError

ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
RUN_ID = str(uuid.uuid4())


def _wire(
    monkeypatch,
    *,
    branches=("feat/a", "feat/b"),
    heads=None,
    rpc_behavior=None,
    issue_rows=None,
):
    """Fake every outside edge: the issue read, the GitHub token, the branch
    resolution, the RPC, and the background docs sync."""
    calls: dict = {}

    heads = (
        heads
        if heads is not None
        else {"main": "base000", "feat/a": "aaa1111", "feat/b": "bbb2222"}
    )

    async def fake_get(settings, token, path, params):
        assert path == "issues"
        if issue_rows is not None:
            return issue_rows
        return [
            {
                "id": ISSUE_ID,
                "org_id": ORG_ID,
                "project_id": PROJECT_ID,
                "type": "chore",
                "merge_branches": list(branches),
                "projects": {
                    "repo_full_name": "acme/widgets",
                    "default_branch": "main",
                },
            }
        ]

    async def fake_token(settings, user_token, org_id, repo_full):
        calls["token_org"] = org_id
        return "gh_token"

    async def fake_get_branch(token, owner, repo, branch):
        if branch not in heads:
            raise github.GitHubError(f"branch '{branch}' not found")
        return {"name": branch, "commit": {"sha": heads[branch]}}

    async def fake_rpc(settings, token, fn, args):
        calls["rpc_fn"] = fn
        calls["rpc_args"] = args
        if rpc_behavior:
            return rpc_behavior()
        return RUN_ID

    def fake_spawn(coro):
        coro.close()  # never actually sync the repo in a test

    monkeypatch.setattr("app.routers.issues.postgrest_get", fake_get)
    monkeypatch.setattr("app.github_tokens.token_for_user", fake_token)
    monkeypatch.setattr("app.github.get_branch", fake_get_branch)
    monkeypatch.setattr("app.routers.issues.rpc", fake_rpc)
    monkeypatch.setattr("app.repo_docs.spawn_background", fake_spawn)
    return calls


def _post(client, make_token):
    return client.post(
        f"/api/v1/issues/{ISSUE_ID}/dispatch-merge",
        headers={"Authorization": f"Bearer {make_token()}"},
    )


def test_resolves_every_head_and_freezes_them_onto_the_run(
    client, make_token, monkeypatch
):
    calls = _wire(monkeypatch)

    resp = _post(client, make_token)

    assert resp.status_code == 202
    assert calls["rpc_fn"] == "dispatch_merge"
    sent = calls["rpc_args"]["p_branch_heads"]
    assert sent == [
        {"branch": "feat/a", "head_sha": "aaa1111", "base_head": "base000"},
        {"branch": "feat/b", "head_sha": "bbb2222", "base_head": "base000"},
    ]
    body = resp.json()
    assert body["kind"] == "merge"
    assert body["base"] == {"branch": "main", "head_sha": "base000"}


def test_the_branch_order_the_manager_chose_is_preserved(
    client, make_token, monkeypatch
):
    """The list is ordered; a merge applied in a different order can resolve
    differently, so the order must survive the round trip."""
    calls = _wire(monkeypatch, branches=("feat/b", "feat/a"))

    assert _post(client, make_token).status_code == 202
    assert [h["branch"] for h in calls["rpc_args"]["p_branch_heads"]] == [
        "feat/b",
        "feat/a",
    ]


def test_no_branches_is_409_and_never_touches_github(
    client, make_token, monkeypatch
):
    calls = _wire(monkeypatch, branches=())

    resp = _post(client, make_token)

    assert resp.status_code == 409
    assert "names no branches" in resp.json()["detail"]
    assert "rpc_fn" not in calls  # refused before the RPC
    assert "token_org" not in calls  # and before GitHub


def test_a_deleted_branch_is_refused_by_name(client, make_token, monkeypatch):
    calls = _wire(
        monkeypatch,
        branches=("feat/a", "feat/gone"),
        heads={"main": "base000", "feat/a": "aaa1111"},
    )

    resp = _post(client, make_token)

    assert resp.status_code == 409
    assert "feat/gone" in resp.json()["detail"]
    assert "rpc_fn" not in calls  # nothing was dispatched


def test_every_missing_branch_is_named_at_once(client, make_token, monkeypatch):
    """A manager repairing a stale list should not have to rediscover it one
    branch per dispatch attempt."""
    _wire(
        monkeypatch,
        branches=("feat/a", "feat/gone", "feat/alsogone"),
        heads={"main": "base000", "feat/a": "aaa1111"},
    )

    detail = _post(client, make_token).json()["detail"]

    assert "feat/gone" in detail and "feat/alsogone" in detail


def test_an_unresolvable_base_is_409(client, make_token, monkeypatch):
    calls = _wire(monkeypatch, heads={"feat/a": "aaa", "feat/b": "bbb"})

    resp = _post(client, make_token)

    assert resp.status_code == 409
    assert "base branch" in resp.json()["detail"]
    assert "rpc_fn" not in calls


def test_an_rpc_refusal_reaches_the_manager_as_409(
    client, make_token, monkeypatch
):
    def refuse():
        raise RpcError("only a chore is dispatched as a merge — this is a story")

    _wire(monkeypatch, rpc_behavior=refuse)

    resp = _post(client, make_token)

    assert resp.status_code == 409
    assert "only a chore" in resp.json()["detail"]


def test_unknown_issue_is_404(client, make_token, monkeypatch):
    _wire(monkeypatch, issue_rows=[])

    assert _post(client, make_token).status_code == 404


def test_a_project_without_a_repo_is_409(client, make_token, monkeypatch):
    _wire(
        monkeypatch,
        issue_rows=[
            {
                "id": ISSUE_ID,
                "org_id": ORG_ID,
                "project_id": PROJECT_ID,
                "type": "chore",
                "merge_branches": ["feat/a"],
                "projects": {"repo_full_name": None, "default_branch": "main"},
            }
        ],
    )

    resp = _post(client, make_token)

    assert resp.status_code == 409
    assert "GitHub repository" in resp.json()["detail"]


def test_the_token_is_scoped_to_the_issues_org(client, make_token, monkeypatch):
    """US-76.4: minting another workspace's installation token is exactly the
    bug `org_id` is required to prevent."""
    calls = _wire(monkeypatch)

    _post(client, make_token)

    assert calls["token_org"] == ORG_ID


def test_requires_authentication(client):
    assert client.post(f"/api/v1/issues/{ISSUE_ID}/dispatch-merge").status_code in (
        401,
        403,
    )
