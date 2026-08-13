# apps/api/tests/test_admin_orgs.py
"""Platform admin org endpoints (US-1.27)."""

import pytest

from tests.conftest import TEST_USER_ID

NON_ADMIN_EMAIL = "not-admin@example.com"


def _non_admin_auth(make_token):
    return {"Authorization": f"Bearer {make_token(email=NON_ADMIN_EMAIL)}"}


@pytest.fixture(autouse=True)
def deny_platform_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        assert fn == "is_platform_admin"
        return False

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("GET", "/api/v1/admin/orgs", None),
        ("POST", "/api/v1/admin/orgs", {"name": "x"}),
        ("PATCH", "/api/v1/admin/orgs/org-1", {"name": "x"}),
        ("GET", "/api/v1/admin/orgs/org-1/members", None),
        ("POST", "/api/v1/admin/orgs/org-1/archive", {"archived": True}),
        ("DELETE", "/api/v1/admin/orgs/org-1", None),
        ("DELETE", "/api/v1/admin/users/user-1", None),
    ],
)
def test_non_admin_gets_403(client, make_token, method, path, json):
    resp = client.request(method, path, json=json, headers=_non_admin_auth(make_token))
    assert resp.status_code == 403


def test_list_orgs_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    async def fake_admin_get(settings, path, params):
        if path == "organizations":
            return [
                {
                    "id": "org-1",
                    "name": "Acme",
                    "shortname": "acme",
                    "archived_at": None,
                    "is_platform_admin": False,
                    "max_agents": 3,
                }
            ]
        assert path == "organization_members"
        if params.get("role") == "eq.owner":
            return [
                {"org_id": "org-1", "principals": {"email": "boss@acme.io", "display_name": "Boss"}}
            ]
        # US-57.2: the agent-count read, filtered by the embedded principal's kind.
        assert params.get("principals.kind") == "eq.agent"
        return [{"org_id": "org-1"}, {"org_id": "org-1"}]

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)

    resp = client.get(
        "/api/v1/admin/orgs", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": "org-1",
            "name": "Acme",
            "shortname": "acme",
            "archived_at": None,
            "is_platform_admin": False,
            "max_agents": 3,
            "owner": {"email": "boss@acme.io", "display_name": "Boss"},
            "agent_count": 2,
        }
    ]


def test_list_org_members_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    async def fake_admin_get(settings, path, params):
        assert path == "organization_members"
        assert params["org_id"] == "eq.org-1"
        return [
            {
                "role": "owner",
                "created_at": "2026-01-01T00:00:00Z",
                "principals": {
                    "id": "p-1",
                    "kind": "human",
                    "email": "boss@acme.io",
                    "display_name": "Boss",
                },
            },
            {
                "role": "agent",
                "created_at": "2026-01-02T00:00:00Z",
                "principals": {
                    "id": "p-2",
                    "kind": "agent",
                    "email": None,
                    "display_name": "Worker 1",
                },
            },
        ]

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)

    resp = client.get(
        "/api/v1/admin/orgs/org-1/members",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "principal_id": "p-1",
            "kind": "human",
            "email": "boss@acme.io",
            "display_name": "Boss",
            "role": "owner",
            "joined_at": "2026-01-01T00:00:00Z",
        },
        {
            "principal_id": "p-2",
            "kind": "agent",
            "email": None,
            "display_name": "Worker 1",
            "role": "agent",
            "joined_at": "2026-01-02T00:00:00Z",
        },
    ]


def test_update_org_slug_happy_path(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    async def fake_admin_patch(settings, table, params, body):
        captured.update(body)
        return [{"id": "org-1", "shortname": body["shortname"]}]

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/orgs/org-1",
        json={"shortname": "New-Slug"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert captured == {"shortname": "new-slug"}  # normalized to lowercase


def test_update_org_max_agents_happy_path(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    async def fake_admin_patch(settings, table, params, body):
        captured.update(body)
        return [{"id": "org-1", "max_agents": body["max_agents"]}]

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/orgs/org-1",
        json={"max_agents": 10},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert captured == {"max_agents": 10}


def test_update_org_max_agents_rejects_out_of_range(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        raise AssertionError("patch must not run for an out-of-range quota")

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/orgs/org-1",
        json={"max_agents": 0},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 400


def test_update_org_slug_rejects_bad_format(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        raise AssertionError("patch must not run for an invalid slug")

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/orgs/org-1",
        json={"shortname": "bad slug!"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 400


def test_update_org_slug_conflict(client, make_token, monkeypatch):
    from app.supabase import PostgrestError

    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        raise PostgrestError("duplicate key value violates unique constraint")

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/orgs/org-1",
        json={"shortname": "taken"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 409


def _grant_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("POST", "/api/v1/admin/orgs/org-1/archive", {"archived": True}),
        ("DELETE", "/api/v1/admin/orgs/org-1", None),
    ],
)
def test_archive_and_delete_reject_platform_admin_org(
    client, make_token, monkeypatch, method, path, json
):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        assert table == "organizations"
        assert params["id"] == "eq.org-1"
        return [{"is_platform_admin": True}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)

    resp = client.request(method, path, json=json, headers={"Authorization": f"Bearer {make_token()}"})
    assert resp.status_code == 400


def test_archive_org_happy_path(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        return [{"is_platform_admin": False}]

    async def fake_admin_patch(settings, table, params, body):
        assert table == "organizations"
        assert body["archived_at"] is not None
        return [{"id": "org-1", "archived_at": body["archived_at"]}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.post(
        "/api/v1/admin/orgs/org-1/archive",
        json={"archived": True},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200


def test_delete_org_happy_path(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        if table == "issues":
            return []  # US-2.16: no work in progress blocks the delete
        return [{"is_platform_admin": False}]

    async def fake_admin_delete(settings, table, params):
        assert table == "organizations"

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_delete", fake_admin_delete)

    resp = client.delete(
        "/api/v1/admin/orgs/org-1", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200


def test_delete_org_blocked_by_active_issue(client, make_token, monkeypatch):
    """US-2.16: a clear message naming the blocking work item, not the
    raw trigger error."""
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        if table == "issues":
            return [{"title": "Ship checkout", "status": "running"}]
        return [{"is_platform_admin": False}]

    async def fake_admin_delete(settings, table, params):
        raise AssertionError("delete must not be attempted while work is active")

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_delete", fake_admin_delete)

    resp = client.delete(
        "/api/v1/admin/orgs/org-1", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 409
    assert "Ship checkout" in resp.json()["detail"]


def test_delete_org_force_skips_active_check(client, make_token, monkeypatch):
    """force must skip the API-level precheck AND route through the
    admin_force_delete_org RPC (migration 222), not a plain table delete —
    a plain delete still trips the queued/running guard trigger."""
    _grant_admin(monkeypatch)
    called = {}

    async def fake_admin_get(settings, table, params):
        if table == "issues":
            raise AssertionError("force must skip the active-work check entirely")
        return [{"is_platform_admin": False}]

    async def fake_admin_delete(settings, table, params):
        raise AssertionError("force must use the RPC, not a plain table delete")

    async def fake_admin_rpc(settings, fn, args):
        called["fn"] = fn
        called["args"] = args

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_delete", fake_admin_delete)
    monkeypatch.setattr("app.routers.admin.admin_rpc", fake_admin_rpc)

    resp = client.delete(
        "/api/v1/admin/orgs/org-1?force=true",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert called == {"fn": "admin_force_delete_org", "args": {"p_org_id": "org-1"}}


def test_delete_user_happy_path(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    deleted = {}

    async def fake_admin_get(settings, table, params):
        if table == "principals":
            return [{"id": "principal-1"}]
        assert table == "issues"
        return []  # no work in progress

    async def fake_delete_user(settings, user_id):
        deleted["user_id"] = user_id

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr(
        "app.routers.admin.admin_auth.delete_user", fake_delete_user
    )

    resp = client.delete(
        "/api/v1/admin/users/user-1", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    assert deleted["user_id"] == "user-1"


def test_delete_user_blocked_by_active_issue(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        if table == "principals":
            return [{"id": "principal-1"}]
        assert table == "issues"
        return [{"title": "Ship checkout", "status": "running"}]

    async def fake_delete_user(settings, user_id):
        raise AssertionError("delete must not be attempted while work is active")

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr(
        "app.routers.admin.admin_auth.delete_user", fake_delete_user
    )

    resp = client.delete(
        "/api/v1/admin/users/user-1", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 409
    assert "Ship checkout" in resp.json()["detail"]


def test_delete_user_force_skips_active_check(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    deleted = {}

    async def fake_admin_get(settings, table, params):
        raise AssertionError("force must skip the active-work check entirely")

    async def fake_delete_user(settings, user_id):
        deleted["user_id"] = user_id

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr(
        "app.routers.admin.admin_auth.delete_user", fake_delete_user
    )

    resp = client.delete(
        "/api/v1/admin/users/user-1?force=true",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert deleted["user_id"] == "user-1"


def test_delete_user_rejects_self_delete(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    resp = client.delete(
        f"/api/v1/admin/users/{TEST_USER_ID}",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 400
