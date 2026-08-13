"""US-7.3: the create-branch endpoint — bases off the default branch head
and maps GitHub errors to actionable 422s."""

from app.github import GitHubError


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_create_branch_bases_off_default(client, make_token, monkeypatch):
    calls = {}

    async def fake_token(settings, user_token, repo_full_name=None):
        return "tok"

    async def fake_get_repo(token, owner, repo):
        return {"default_branch": "main"}

    async def fake_get_branch(token, owner, repo, branch):
        calls["base"] = branch
        return {"commit": {"sha": "abc123"}}

    async def fake_create_ref(token, owner, repo, branch, sha):
        calls["created"] = (branch, sha)

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    monkeypatch.setattr("app.routers.github.github.get_repo", fake_get_repo)
    monkeypatch.setattr("app.routers.github.github.get_branch", fake_get_branch)
    monkeypatch.setattr("app.routers.github.github.create_ref", fake_create_ref)

    resp = client.post(
        "/api/v1/github/repos/acme/webshop/branches",
        json={"name": "release/uat"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"name": "release/uat", "commit_sha": "abc123"}
    assert calls["base"] == "main"
    assert calls["created"] == ("release/uat", "abc123")


def test_create_branch_uses_explicit_base(client, make_token, monkeypatch):
    seen = {}

    async def fake_token(settings, user_token, repo_full_name=None):
        return "tok"

    async def fake_get_branch(token, owner, repo, branch):
        seen["base"] = branch
        return {"commit": {"sha": "def456"}}

    async def fake_create_ref(token, owner, repo, branch, sha):
        pass

    # get_repo must NOT be needed when a base is given.
    async def boom(*a, **k):
        raise AssertionError("get_repo should not be called with explicit base")

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    monkeypatch.setattr("app.routers.github.github.get_repo", boom)
    monkeypatch.setattr("app.routers.github.github.get_branch", fake_get_branch)
    monkeypatch.setattr("app.routers.github.github.create_ref", fake_create_ref)

    resp = client.post(
        "/api/v1/github/repos/acme/webshop/branches",
        json={"name": "release/prod", "base_branch": "develop"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert seen["base"] == "develop"


def test_create_branch_rejects_invalid_name(client, make_token, monkeypatch):
    async def fake_token(settings, user_token, repo_full_name=None):
        return "tok"

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    resp = client.post(
        "/api/v1/github/repos/acme/webshop/branches",
        json={"name": "bad name with spaces"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422


def test_create_branch_maps_github_error(client, make_token, monkeypatch):
    async def fake_token(settings, user_token, repo_full_name=None):
        return "tok"

    async def fake_get_repo(token, owner, repo):
        return {"default_branch": "main"}

    async def fake_get_branch(token, owner, repo, branch):
        return {"commit": {"sha": "abc123"}}

    async def fake_create_ref(token, owner, repo, branch, sha):
        raise GitHubError("could not create branch: Reference already exists")

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    monkeypatch.setattr("app.routers.github.github.get_repo", fake_get_repo)
    monkeypatch.setattr("app.routers.github.github.get_branch", fake_get_branch)
    monkeypatch.setattr("app.routers.github.github.create_ref", fake_create_ref)

    resp = client.post(
        "/api/v1/github/repos/acme/webshop/branches",
        json={"name": "release/uat"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "already exists" in resp.json()["detail"]
