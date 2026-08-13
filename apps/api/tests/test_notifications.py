"""Notification endpoints (US-1.44): webhook URLs are write-only secrets."""


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


def _member(monkeypatch, endpoint_row=None):
    async def fake_get(settings, token, table, params):
        if table == "organization_members":
            return [{"org_id": "org-1"}]
        assert table == "notification_endpoints"
        return [endpoint_row] if endpoint_row else []

    monkeypatch.setattr("app.routers.notifications.postgrest_get", fake_get)


def test_create_endpoint_requires_auth(client):
    resp = client.post("/api/v1/notifications/endpoints", json={})
    assert resp.status_code == 401


def test_create_endpoint_validates_url(client, make_token, monkeypatch):
    _member(monkeypatch)
    resp = client.post(
        "/api/v1/notifications/endpoints",
        json={"org_id": "org-1", "name": "slack", "url": "not-a-url"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


def test_create_endpoint_stores_url_write_only(client, make_token, monkeypatch):
    written = {}

    def fake_create(settings, org_id, name, url_host, fmt):
        return {
            "id": "ep-1",
            "org_id": org_id,
            "name": name,
            "url_host": url_host,
            "format": fmt,
        }

    async def fake_put(settings, path, content, content_type="application/octet-stream"):
        written[path] = content

    _member(monkeypatch)
    monkeypatch.setattr("app.notify.create_endpoint", fake_create)
    monkeypatch.setattr("app.routers.notifications.storage.put_object", fake_put)

    secret_url = "https://hooks.slack.com/services/T000/B000/tokentokentoken"
    resp = client.post(
        "/api/v1/notifications/endpoints",
        json={"org_id": "org-1", "name": "slack", "url": secret_url, "format": "slack"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 201
    assert written["org-1/notifications/ep-1/url"] == secret_url.encode()
    body = resp.json()
    assert body["url_host"] == "hooks.slack.com"
    # the full URL must never come back
    assert secret_url not in resp.text


def test_delete_endpoint_removes_row_and_object(client, make_token, monkeypatch):
    removed = {}

    def fake_delete_row(settings, endpoint_id):
        removed["row"] = endpoint_id

    async def fake_delete_object(settings, path):
        removed["object"] = path

    _member(
        monkeypatch,
        endpoint_row={"id": "ep-1", "org_id": "org-1", "name": "slack", "format": "slack"},
    )
    monkeypatch.setattr("app.notify.delete_endpoint", fake_delete_row)
    monkeypatch.setattr(
        "app.routers.notifications.storage.delete_object", fake_delete_object
    )

    resp = client.delete(
        "/api/v1/notifications/endpoints/ep-1", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert removed["row"] == "ep-1"
    assert removed["object"] == "org-1/notifications/ep-1/url"


def test_foreign_endpoint_is_404(client, make_token, monkeypatch):
    _member(monkeypatch, endpoint_row=None)
    resp = client.delete(
        "/api/v1/notifications/endpoints/other-org-ep", headers=_auth(make_token)
    )
    assert resp.status_code == 404


def test_test_endpoint_returns_outcome(client, make_token, monkeypatch):
    async def fake_send_test(settings, ep):
        return {"ok": False, "error": "HTTP 404"}

    _member(
        monkeypatch,
        endpoint_row={"id": "ep-1", "org_id": "org-1", "name": "slack", "format": "slack"},
    )
    monkeypatch.setattr("app.routers.notifications.notify.send_test", fake_send_test)

    resp = client.post(
        "/api/v1/notifications/endpoints/ep-1/test", headers=_auth(make_token)
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": False, "error": "HTTP 404"}
