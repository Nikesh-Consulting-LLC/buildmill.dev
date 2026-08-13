"""PAT connect / unified disconnect / merged repos (US-3.15)."""

from app.github import GitHubError
from app.supabase import RpcError

CONN_ID = "3f9f3ac0-6f7d-4c14-9d0e-2f1a75f2b111"


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def test_connect_pat_validates_and_stores(client, make_token, monkeypatch):
    rpc_calls = {}

    async def fake_user(token):
        assert token == "github_pat_abc1234"
        return {"login": "kaush", "type": "User"}, "2027-01-15 10:30:00 UTC"

    async def fake_repo(token, owner, repo):
        assert (owner, repo) == ("acme", "webshop")
        return {"full_name": "acme/webshop", "default_branch": "develop"}

    async def fake_get(settings, token, path, params):
        # US-76.3: a pasted token lands in the ACTIVE workspace, so the flow
        # reads memberships and then the stored active org.
        if path == "organization_members":
            return [{"org_id": "org-0"}, {"org_id": "org-1"}]
        assert path == "principals"
        return [{"active_org_id": "org-1"}]

    async def fake_rpc(settings, token, fn, args):
        rpc_calls["fn"] = fn
        rpc_calls["args"] = args
        return CONN_ID

    monkeypatch.setattr(
        "app.routers.github.github.get_authenticated_user", fake_user
    )
    monkeypatch.setattr("app.routers.github.github.get_repo", fake_repo)
    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.rpc", fake_rpc)

    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "github_pat_abc1234", "repos": ["acme/webshop"]},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["account_login"] == "kaush"
    assert body["pat_last4"] == "1234"
    assert body["pat_expires_at"] == "2027-01-15T10:30:00+00:00"
    assert body["repos"] == [{"full_name": "acme/webshop", "default_branch": "develop"}]
    assert rpc_calls["fn"] == "connect_github_pat"
    assert rpc_calls["args"]["p_org"] == "org-1"
    assert rpc_calls["args"]["p_token"] == "github_pat_abc1234"
    # the RPC receives validated repo entries, not raw user input
    assert rpc_calls["args"]["p_repos"] == [
        {"full_name": "acme/webshop", "default_branch": "develop"}
    ]


def test_connect_pat_rejected_token_is_400(client, make_token, monkeypatch):
    async def fake_user(token):
        raise GitHubError("token rejected by GitHub (401)")

    monkeypatch.setattr(
        "app.routers.github.github.get_authenticated_user", fake_user
    )
    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "bad", "repos": ["acme/webshop"]},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


def test_connect_pat_unreachable_repo_is_400(client, make_token, monkeypatch):
    async def fake_user(token):
        return {"login": "kaush", "type": "User"}, None

    async def fake_repo(token, owner, repo):
        raise GitHubError("repository acme/private not reachable with this token (404)")

    monkeypatch.setattr(
        "app.routers.github.github.get_authenticated_user", fake_user
    )
    monkeypatch.setattr("app.routers.github.github.get_repo", fake_repo)
    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "github_pat_abc1234", "repos": ["acme/private"]},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "acme/private" in resp.json()["detail"]


def test_connect_pat_validation_error_does_not_echo_token(client, make_token):
    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "github_pat_supersecret"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422
    assert "github_pat_supersecret" not in resp.text


def test_connect_pat_requires_repos(client, make_token):
    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "github_pat_abc1234", "repos": []},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


def test_disconnect_pat_skips_uninstall_and_calls_rpc(client, make_token, monkeypatch):
    calls = {"uninstall": 0, "rpc": None}

    async def fake_get(settings, token, path, params):
        assert path == "github_connections"
        return [{"id": CONN_ID, "method": "pat", "installation_id": None}]

    async def fake_uninstall(settings, installation_id):
        calls["uninstall"] += 1

    async def fake_rpc(settings, token, fn, args):
        calls["rpc"] = (fn, args)
        return None

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.uninstall", fake_uninstall)
    monkeypatch.setattr("app.routers.github.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/github/connections/{CONN_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert calls["uninstall"] == 0
    assert calls["rpc"] == ("delete_github_connection", {"p_id": CONN_ID})


def test_disconnect_app_uninstalls_then_deletes(client, make_token, monkeypatch):
    calls = {"uninstall": None, "rpc": None}

    async def fake_get(settings, token, path, params):
        return [{"id": CONN_ID, "method": "app", "installation_id": 987}]

    async def fake_uninstall(settings, installation_id):
        calls["uninstall"] = installation_id

    async def fake_rpc(settings, token, fn, args):
        calls["rpc"] = (fn, args)
        return None

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.github.uninstall", fake_uninstall)
    monkeypatch.setattr("app.routers.github.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/github/connections/{CONN_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert calls["uninstall"] == 987
    assert calls["rpc"] == ("delete_github_connection", {"p_id": CONN_ID})


def test_disconnect_unknown_connection_is_404(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    resp = client.post(
        f"/api/v1/github/connections/{CONN_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


def test_repos_merges_app_and_pat_connections(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        if path == "organization_members":
            return [{"org_id": "org-1"}]
        if path == "principals":
            return [{"active_org_id": "org-1"}]
        assert path == "github_connections"
        assert params["org_id"] == "eq.org-1"
        return [
            {"method": "app", "installation_id": 1, "repos": []},
            {
                "method": "pat",
                "installation_id": None,
                "repos": [
                    {"full_name": "acme/webshop", "default_branch": "main"},
                    {"full_name": "other/tool", "default_branch": "main"},
                ],
            },
        ]

    async def fake_mint(settings, installation_id):
        return "tok"

    async def fake_list(token):
        return [{"full_name": "acme/webshop", "default_branch": "main"}]

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.routers.github.github.mint_installation_token", fake_mint
    )
    monkeypatch.setattr(
        "app.routers.github.github.list_installation_repos", fake_list
    )

    resp = client.get("/api/v1/github/repos", headers=_auth(make_token))
    assert resp.status_code == 200
    names = [r["full_name"] for r in resp.json()]
    assert names == ["acme/webshop", "other/tool"]  # deduped, both sources


def test_connect_pat_rpc_error_is_400(client, make_token, monkeypatch):
    async def fake_user(token):
        return {"login": "kaush", "type": "User"}, "2027-01-15 10:30:00 UTC"

    async def fake_repo(token, owner, repo):
        return {"full_name": "acme/webshop", "default_branch": "develop"}

    async def fake_get(settings, token, path, params):
        # US-76.3: a pasted token lands in the ACTIVE workspace, so the flow
        # reads memberships and then the stored active org.
        if path == "organization_members":
            return [{"org_id": "org-0"}, {"org_id": "org-1"}]
        assert path == "principals"
        return [{"active_org_id": "org-1"}]

    async def fake_rpc(settings, token, fn, args):
        raise RpcError("not authorized")

    monkeypatch.setattr(
        "app.routers.github.github.get_authenticated_user", fake_user
    )
    monkeypatch.setattr("app.routers.github.github.get_repo", fake_repo)
    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.rpc", fake_rpc)

    resp = client.post(
        "/api/v1/github/connections/pat",
        json={"token": "github_pat_abc1234", "repos": ["acme/webshop"]},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "not authorized" in resp.json()["detail"]


def test_disconnect_connection_rpc_error_is_400(client, make_token, monkeypatch):
    calls = {"rpc": None}

    async def fake_get(settings, token, path, params):
        return [{"id": CONN_ID, "method": "pat", "installation_id": None}]

    async def fake_rpc(settings, token, fn, args):
        raise RpcError("connection not found")

    monkeypatch.setattr("app.routers.github.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.github.rpc", fake_rpc)

    resp = client.post(
        f"/api/v1/github/connections/{CONN_ID}/disconnect",
        headers=_auth(make_token),
    )
    assert resp.status_code == 400
    assert "connection not found" in resp.json()["detail"]
