"""US-7.9: write-only build config endpoints — values go to Storage and are
never echoed; names are validated; non-members get 404; cross-org access is
blocked by RLS (the postgrest membership check returns nothing)."""

import uuid


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


PROJECT_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())


def _patch_member(monkeypatch, org_id=ORG_ID):
    async def fake_projects(settings, token, table, params):
        assert table == "projects"
        return [{"org_id": org_id}] if org_id else []

    monkeypatch.setattr("app.routers.projects.postgrest_get", fake_projects)


def test_set_build_config_stores_value_never_echoes(client, make_token, monkeypatch):
    _patch_member(monkeypatch)
    stored = {}

    async def fake_put(settings, path, content, *a, **k):
        stored["path"] = path
        stored["content"] = content

    def fake_upsert(settings, org_id, project_id, name, actor=None):
        stored["name"] = name

    monkeypatch.setattr("app.routers.projects.storage.put_object", fake_put)
    monkeypatch.setattr("app.routers.projects.db.upsert_build_config_name", fake_upsert)

    resp = client.put(
        f"/api/v1/projects/{PROJECT_ID}/build-config/TEST_DB_URL",
        json={"value": "postgres://secret"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True}
    # the value is never in the response
    assert "secret" not in resp.text
    assert stored["content"] == b"postgres://secret"
    assert stored["path"].endswith("/build-config/TEST_DB_URL")
    assert f"/projects/{PROJECT_ID}/build-config" in stored["path"]
    assert stored["name"] == "TEST_DB_URL"


def test_set_build_config_rejects_bad_name(client, make_token, monkeypatch):
    _patch_member(monkeypatch)
    resp = client.put(
        f"/api/v1/projects/{PROJECT_ID}/build-config/1bad-name",
        json={"value": "x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 400


def test_build_config_404_for_non_member(client, make_token, monkeypatch):
    # RLS returns no project row → not a member → 404, no cross-org leak.
    _patch_member(monkeypatch, org_id=None)
    resp = client.put(
        f"/api/v1/projects/{PROJECT_ID}/build-config/X",
        json={"value": "x"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 404


def test_remove_build_config(client, make_token, monkeypatch):
    _patch_member(monkeypatch)
    deleted = {}

    async def fake_delete(settings, path, *a, **k):
        deleted["path"] = path

    def fake_db_delete(settings, project_id, name):
        deleted["name"] = name

    monkeypatch.setattr("app.routers.projects.storage.delete_object", fake_delete)
    monkeypatch.setattr("app.routers.projects.db.delete_build_config_name", fake_db_delete)

    resp = client.delete(
        f"/api/v1/projects/{PROJECT_ID}/build-config/TEST_DB_URL",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200
    assert deleted["name"] == "TEST_DB_URL"
    assert deleted["path"].endswith("/build-config/TEST_DB_URL")
