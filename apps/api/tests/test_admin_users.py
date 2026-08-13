# apps/api/tests/test_admin_users.py
"""Platform admin user + membership endpoints (US-1.27)."""

import pytest

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
        ("GET", "/api/v1/admin/users", None),
        ("PATCH", "/api/v1/admin/users/user-1", {"display_name": "x"}),
        ("POST", "/api/v1/admin/users/user-1/deactivate", {"deactivated": True}),
        ("POST", "/api/v1/admin/users/user-1/reset-password", {"new_password": "x" * 10}),
        ("POST", "/api/v1/admin/memberships", {"org_id": "o", "user_id": "u", "role": "member"}),
        ("PATCH", "/api/v1/admin/memberships", {"org_id": "o", "user_id": "u", "role": "owner"}),
        ("DELETE", "/api/v1/admin/memberships?org_id=o&user_id=u", None),
    ],
)
def test_non_admin_gets_403(client, make_token, method, path, json):
    resp = client.request(method, path, json=json, headers=_non_admin_auth(make_token))
    assert resp.status_code == 403


def test_deactivate_user_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    called = {}

    async def fake_set_ban(settings, user_id, banned):
        called["user_id"] = user_id
        called["banned"] = banned

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_auth.set_ban", fake_set_ban)

    resp = client.post(
        "/api/v1/admin/users/user-1/deactivate",
        json={"deactivated": True},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert called == {"user_id": "user-1", "banned": True}


def test_edit_user_email_happy_path(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    async def fake_admin_get(settings, table, params):
        assert table == "profiles"
        return [{"email": "old@example.com"}]

    called = {}

    async def fake_update_user(settings, user_id, **fields):
        called.setdefault("update_user_calls", []).append(fields)

    async def fake_admin_patch(settings, table, params, body):
        assert table == "profiles"
        return [{"id": "user-1", "email": body["email"]}]

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_auth.update_user", fake_update_user)
    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/users/user-1",
        json={"email": "new@example.com"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert called["update_user_calls"] == [{"email": "new@example.com"}]


def test_edit_user_email_rolls_back_auth_on_profile_patch_failure(client, make_token, monkeypatch):
    from app.supabase import PostgrestError

    async def fake_rpc(settings, user_token, fn, args):
        return True

    async def fake_admin_get(settings, table, params):
        return [{"email": "old@example.com"}]

    called = {}

    async def fake_update_user(settings, user_id, **fields):
        called.setdefault("update_user_calls", []).append(fields)

    async def fake_admin_patch(settings, table, params, body):
        raise PostgrestError("boom")

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)
    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    monkeypatch.setattr("app.routers.admin.admin_auth.update_user", fake_update_user)
    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)

    resp = client.patch(
        "/api/v1/admin/users/user-1",
        json={"email": "new@example.com"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 502
    assert called["update_user_calls"] == [
        {"email": "new@example.com"},
        {"email": "old@example.com"},
    ]


def test_reset_password_rejects_short_password(client, make_token, monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)

    resp = client.post(
        "/api/v1/admin/users/user-1/reset-password",
        json={"new_password": "short"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 400
