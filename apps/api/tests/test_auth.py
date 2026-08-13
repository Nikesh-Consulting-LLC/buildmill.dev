"""US-1.8: /api/v1/auth/me — valid token 200, missing/invalid 401."""

from tests.conftest import TEST_ORG_ID, TEST_USER_ID


def test_me_returns_user_and_org(client, make_token, monkeypatch):
    async def fake_get(settings, token, path, params):
        assert path == "organization_members"
        assert params["user_id"] == f"eq.{TEST_USER_ID}"
        return [{"org_id": TEST_ORG_ID}]

    monkeypatch.setattr("app.routers.auth.postgrest_get", fake_get)

    resp = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == TEST_USER_ID
    assert body["email"] == "kaushlesh@nikesh.llc"
    assert body["org_id"] == TEST_ORG_ID


def test_me_without_token_is_401(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code == 401


def test_me_with_garbage_token_is_401(client):
    resp = client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"}
    )
    assert resp.status_code == 401


def test_me_with_expired_token_is_401(client, make_token):
    import time

    expired = make_token(exp=int(time.time()) - 60)
    resp = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"}
    )
    assert resp.status_code == 401


def test_me_with_wrong_audience_is_401(client, make_token):
    resp = client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {make_token(aud='anon')}"},
    )
    assert resp.status_code == 401
