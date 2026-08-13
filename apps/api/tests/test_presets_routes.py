"""US-57.6: presets are platform-authored — create/patch/delete/reseed all
route through `require_platform_admin` now, not the org's own `manage_work`.

No route-level test existed for these before this story (test_presets.py
only exercises the pure validation functions), so this both pins the new
gate and closes that gap.
"""

from __future__ import annotations

import pytest

ORG = "654d7ff1-ab30-4812-a1ff-c9588d91ad50"
PRESET_ID = "88888888-8888-4888-8888-888888888888"

PRESET_ROW = {
    "id": PRESET_ID,
    "org_id": ORG,
    "name": "Deep",
    "description": "",
    "model": None,
    "settings": {},
    "version": 1,
    "tool_grants": [],
}


def _auth(make_token, **claims):
    return {"Authorization": f"Bearer {make_token(**claims)}"}


def _rpc(is_platform_admin: bool):
    async def fake(settings, token, fn, args):
        assert fn == "is_platform_admin"
        return is_platform_admin

    return fake


@pytest.fixture
def db_stubs(monkeypatch):
    state = {"created": None, "updated": None, "archived": None, "reseeded": None}

    monkeypatch.setattr(
        "app.routers.presets.db.list_presets", lambda settings, org, *a: []
    )
    monkeypatch.setattr(
        "app.routers.presets.db.get_preset",
        lambda settings, pid: dict(PRESET_ROW) if pid == PRESET_ID else None,
    )

    def fake_create(settings, org_id, **kw):
        state["created"] = {"org_id": org_id, **kw}
        return {**PRESET_ROW, **kw}

    def fake_update(settings, preset_id, **kw):
        state["updated"] = {"preset_id": preset_id, **kw}
        return {**PRESET_ROW, **kw}

    def fake_archive(settings, preset_id):
        state["archived"] = preset_id
        return True

    def fake_reseed(settings, org_id, keys):
        state["reseeded"] = {"org_id": org_id, "keys": keys}
        return []

    monkeypatch.setattr("app.routers.presets.db.create_preset", fake_create)
    monkeypatch.setattr("app.routers.presets.db.update_preset", fake_update)
    monkeypatch.setattr("app.routers.presets.db.archive_preset", fake_archive)
    monkeypatch.setattr("app.routers.presets.db.apply_reseed", fake_reseed)
    monkeypatch.setattr(
        "app.routers.presets.db.list_preset_templates", lambda settings: []
    )
    monkeypatch.setattr(
        "app.routers.presets.db.org_module_support", lambda settings, org: {}
    )
    monkeypatch.setattr(
        "app.routers.presets.db.get_org_llm_config", lambda settings, org: ([], {})
    )
    monkeypatch.setattr(
        "app.routers.presets.db.list_mcp_servers", lambda settings, org: []
    )
    return state


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", f"/api/v1/orgs/{ORG}/presets", {"name": "New"}),
        ("PATCH", f"/api/v1/presets/{PRESET_ID}", {"name": "Renamed"}),
        ("DELETE", f"/api/v1/presets/{PRESET_ID}", None),
        ("POST", f"/api/v1/orgs/{ORG}/presets/reseed", {}),
    ],
)
def test_a_non_platform_admin_cannot_write_a_preset(
    client, make_token, db_stubs, monkeypatch, method, path, body
):
    monkeypatch.setattr("app.routers.admin.rpc", _rpc(is_platform_admin=False))
    resp = client.request(method, path, json=body, headers=_auth(make_token))
    assert resp.status_code == 403, resp.text
    assert not any(db_stubs.values())


def test_a_platform_admin_can_create_a_preset(client, make_token, db_stubs, monkeypatch):
    monkeypatch.setattr("app.routers.admin.rpc", _rpc(is_platform_admin=True))
    resp = client.post(
        f"/api/v1/orgs/{ORG}/presets",
        json={"name": "New"},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert db_stubs["created"]["org_id"] == ORG


def test_a_platform_admin_can_delete_a_preset(client, make_token, db_stubs, monkeypatch):
    monkeypatch.setattr("app.routers.admin.rpc", _rpc(is_platform_admin=True))
    resp = client.delete(f"/api/v1/presets/{PRESET_ID}", headers=_auth(make_token))
    assert resp.status_code == 200, resp.text
    assert db_stubs["archived"] == PRESET_ID


def test_reading_the_reseed_preview_still_only_needs_manage_work(
    client, make_token, db_stubs, monkeypatch
):
    """The GET preview is informational, unchanged by this story — only
    applying a re-seed moved to the platform admin."""

    async def fake_rpc(settings, token, fn, args):
        assert fn == "has_org_capability"
        return True

    monkeypatch.setattr("app.routers.presets.rpc", fake_rpc)
    resp = client.get(
        f"/api/v1/orgs/{ORG}/presets/reseed", headers=_auth(make_token)
    )
    assert resp.status_code == 200, resp.text
