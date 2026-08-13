"""GitHub App connect/disconnect + repo/PR/Projects-v2 reads (US-1.19)."""

from fastapi import HTTPException

from app.supabase import PostgrestError

INSTALLATION_ID = 987654

# US-76.3: the callback now re-checks that the state's user is still an active
# member of the state's org before recording anything. Tests that exercise the
# happy path have to say so.
def _member_ok(monkeypatch):
    async def fake_admin_get(settings, path, params):
        assert path == "organization_members"
        return [{"org_id": params["org_id"].removeprefix("eq.")}]

    monkeypatch.setattr("app.routers.github.admin_get", fake_admin_get)


# US-76.3: connect-url and the repo endpoints resolve the ACTIVE org, which
# means a memberships read plus a principals read.
def _active_org(monkeypatch, org_id="org-1", extra=None):
    async def fake_get(settings, token, path, params):
        if path == "organization_members":
            return [{"org_id": org_id}]
        if path == "principals":
            return [{"active_org_id": org_id}]
        if extra is not None:
            return extra(path, params)
        raise AssertionError(path)

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)



def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_install_callback_valid_state_upserts_and_redirects(
    client, make_token, monkeypatch
):
    called = {}

    def fake_verify_state(settings, state):
        assert state == "good-state"
        return ("org-1", "user-1")

    async def fake_get_installation(settings, installation_id):
        assert installation_id == INSTALLATION_ID
        return {"account": {"login": "acme", "type": "Organization"}}

    async def fake_admin_rpc(settings, fn, args):
        assert fn == "record_github_app_installation"
        called.update(args)

    monkeypatch.setattr("app.routers.github.github.verify_state", fake_verify_state)
    monkeypatch.setattr(
        "app.routers.github.github.get_installation", fake_get_installation
    )
    monkeypatch.setattr("app.routers.github.admin_rpc", fake_admin_rpc)
    _member_ok(monkeypatch)

    resp = client.get(
        f"/api/v1/github/install/callback"
        f"?installation_id={INSTALLATION_ID}&setup_action=install&state=good-state",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/settings/github?github=connected")
    assert called["p_org"] == "org-1"
    assert called["p_connected_by"] == "user-1"
    assert called["p_account_login"] == "acme"


def test_install_callback_invalid_state_redirects_to_error(client, monkeypatch):
    from app.github import GitHubError

    def fake_verify_state(settings, state):
        raise GitHubError("bad")

    monkeypatch.setattr("app.routers.github.github.verify_state", fake_verify_state)

    resp = client.get(
        f"/api/v1/github/install/callback"
        f"?installation_id={INSTALLATION_ID}&setup_action=install&state=bad",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/settings/github?github=error")


def test_install_callback_rpc_failure_redirects_to_error(client, monkeypatch):
    """US-3.19: a service-role RPC failure (e.g. bad service role key) is
    caught like any other GitHub-side failure — no more bare 500s from a
    misconfigured direct-Postgres connection."""

    def fake_verify_state(settings, state):
        return ("org-1", "user-1")

    async def fake_get_installation(settings, installation_id):
        return {"account": {"login": "acme", "type": "Organization"}}

    async def fake_admin_rpc(settings, fn, args):
        raise PostgrestError("service role rejected")

    monkeypatch.setattr("app.routers.github.github.verify_state", fake_verify_state)
    monkeypatch.setattr(
        "app.routers.github.github.get_installation", fake_get_installation
    )
    monkeypatch.setattr("app.routers.github.admin_rpc", fake_admin_rpc)
    _member_ok(monkeypatch)

    resp = client.get(
        f"/api/v1/github/install/callback"
        f"?installation_id={INSTALLATION_ID}&setup_action=install&state=good-state",
        follow_redirects=False,
    )
    assert resp.status_code in (302, 307)
    assert resp.headers["location"].endswith("/settings/github?github=error")


def test_connect_url_returns_install_link(client, make_token, monkeypatch):
    def fake_make_state(settings, org_id, user_id):
        assert org_id == "org-1"
        return "signed-state"

    _active_org(monkeypatch, "org-1")
    monkeypatch.setattr("app.routers.github.github.make_state", fake_make_state)
    monkeypatch.setattr("app.routers.github.settings_slug", lambda s: "my-app")

    resp = client.get("/api/v1/github/connect-url", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json()["url"].endswith("/installations/new?state=signed-state")


def test_repos_merges_across_installations(client, make_token, monkeypatch):
    def conns(path, params):
        assert path == "github_connections"
        # US-76.4: scoped to the active org, not to every org RLS allows
        assert params["org_id"] == "eq.org-1"
        return [{"method": "app", "installation_id": 1, "repos": []}]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_list(token):
        return [{"full_name": "acme/webshop", "default_branch": "main"}]

    _active_org(monkeypatch, "org-1", conns)
    monkeypatch.setattr("app.routers.github.github.mint_installation_token", fake_mint)
    monkeypatch.setattr("app.routers.github.github.list_installation_repos", fake_list)

    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == [{"full_name": "acme/webshop", "default_branch": "main"}]


def test_repos_credential_failure_is_502_with_cause(client, make_token, monkeypatch):
    """US-5.24: a mint failure on the repos listing becomes a real error
    response carrying the credential message — the browser no longer sees
    a CORS-stripped 500 as "Failed to fetch"."""

    _active_org(
        monkeypatch,
        "org-1",
        lambda path, params: [{"method": "app", "installation_id": 1, "repos": []}],
    )

    async def fake_mint(settings, installation_id):
        from app.github import mint_error

        raise mint_error(404)

    monkeypatch.setattr("app.routers.github.github.mint_installation_token", fake_mint)

    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 502
    assert "reconnect GitHub" in resp.json()["detail"]


def test_repos_cross_org_is_empty(client, make_token, monkeypatch):
    # RLS hides other orgs' installations, and US-76.4's org filter hides the
    # caller's *other* orgs on top of that.
    _active_org(monkeypatch, "org-1", lambda path, params: [])
    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 200
    assert resp.json() == []


def test_repo_pulls_happy_path(client, make_token, monkeypatch):
    async def fake_token(settings, user, repo_full_name=None):
        assert repo_full_name == "acme/webshop"
        return "tok"

    async def fake_pulls(token, owner, repo):
        assert (owner, repo) == ("acme", "webshop")
        return [
            {
                "number": 7,
                "title": "Fix bug",
                "user": {"login": "alice"},
                "html_url": "https://github.com/acme/webshop/pull/7",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    monkeypatch.setattr("app.routers.github.github.list_open_pulls", fake_pulls)

    resp = client.get(
        "/api/v1/github/repos/acme/webshop/pulls", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()[0]["author"] == "alice"


def test_repo_pulls_no_installation_is_404(client, make_token, monkeypatch):
    async def fake_token(settings, user, repo_full_name=None):
        raise HTTPException(status_code=404, detail="No GitHub connection for this org")

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    resp = client.get(
        "/api/v1/github/repos/acme/webshop/pulls", headers=_auth(make_token)
    )
    assert resp.status_code == 404


def test_repo_projects_happy_path(client, make_token, monkeypatch):
    async def fake_token(settings, user, repo_full_name=None):
        assert repo_full_name == "acme/webshop"
        return "tok"

    async def fake_projects(token, owner, repo):
        return [{"id": "PVT_1", "title": "Roadmap", "url": "https://github.com/orgs/acme/projects/1"}]

    monkeypatch.setattr("app.routers.github._org_github_token", fake_token)
    monkeypatch.setattr("app.routers.github.github.list_projects_v2", fake_projects)

    resp = client.get(
        "/api/v1/github/repos/acme/webshop/projects", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json()[0]["title"] == "Roadmap"


def test_repos_without_token_is_401(client):
    resp = client.get("/api/v1/github/repos")
    assert resp.status_code == 401


# ------------------------------- US-5.24 (b'): permission failures (403)


def test_list_check_runs_403_is_permission_error(monkeypatch):
    """US-5.24: a checks listing refused with 403 means the credential
    lacks Checks: read — a manager-only fix, never "retry"."""
    import asyncio

    import pytest

    from app import github

    class FakeResp:
        status_code = 403

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr("app.github.httpx.AsyncClient", FakeClient)
    with pytest.raises(github.GitHubPermissionError) as ei:
        asyncio.run(github.list_check_runs("tok", "acme", "webshop", "abc123"))
    assert "Checks: read" in ei.value.message
    assert ei.value.upstream_status == 403
    # Subclass of the credential taxonomy: every manager-must-fix path
    # (git proxy, repo browse, settings) already treats it correctly.
    assert isinstance(ei.value, github.GitHubCredentialError)


def test_permission_error_names_operation_and_permission():
    from app import github

    e = github.permission_error("list checks", "Checks: read")
    assert "list checks" in e.message
    assert "Checks: read" in e.message
    assert "manager" in e.message
    assert e.permission == "Checks: read"
