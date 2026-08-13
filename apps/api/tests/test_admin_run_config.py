"""US-57.6: the platform's one run configuration, and the module catalog —
both platform-admin only, both cascading with no per-agent write."""

from __future__ import annotations

import pytest

PLATFORM_CONFIG_ROW = {
    "id": True,
    "autonomy_policy": {},
    "model_routes": {},
    "run_routes": {},
    "max_run_minutes": None,
    "max_total_run_minutes": None,
    "max_item_attempts": 3,
    "updated_at": "2026-07-30T00:00:00Z",
}


def _grant_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


def _deny_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return False

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


@pytest.mark.parametrize(
    "method,path,json",
    [
        ("GET", "/api/v1/admin/run-config", None),
        ("PUT", "/api/v1/admin/run-config", {"max_item_attempts": 5}),
        ("GET", "/api/v1/admin/modules", None),
        ("PATCH", "/api/v1/admin/modules/claude", {"available": False}),
    ],
)
def test_non_admin_gets_403(client, make_token, monkeypatch, method, path, json):
    _deny_admin(monkeypatch)
    resp = client.request(
        method, path, json=json, headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 403


def test_get_run_config_returns_the_singleton_row(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        assert table == "platform_run_config"
        return [dict(PLATFORM_CONFIG_ROW)]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    resp = client.get(
        "/api/v1/admin/run-config", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    assert resp.json()["max_item_attempts"] == 3


def test_set_run_config_updates_only_provided_fields(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    async def fake_admin_patch(settings, table, params, body):
        assert table == "platform_run_config"
        assert params == {"id": "eq.true"}
        captured.update(body)
        return [{**PLATFORM_CONFIG_ROW, **body}]

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.put(
        "/api/v1/admin/run-config",
        json={"max_item_attempts": 7},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {"max_item_attempts": 7}


def test_set_run_config_rejects_out_of_range_limits(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        raise AssertionError("must not write an out-of-range limit")

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.put(
        "/api/v1/admin/run-config",
        json={"max_run_minutes": 5000},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422


def test_set_run_config_rejects_a_malformed_run_route(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        raise AssertionError("must not write an invalid run route")

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.put(
        "/api/v1/admin/run-config",
        json={"run_routes": {"not-a-kind": {"preset_id": "x"}}},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 422


def test_set_run_config_can_explicitly_clear_a_limit(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    async def fake_admin_patch(settings, table, params, body):
        captured.update(body)
        return [{**PLATFORM_CONFIG_ROW, **body}]

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.put(
        "/api/v1/admin/run-config",
        json={"max_run_minutes": None},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {"max_run_minutes": None}


def test_list_modules_returns_the_catalog(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_get(settings, table, params):
        assert table == "agent_modules"
        return [
            {"key": "claude", "label": "Claude Code", "available": True},
            {"key": "grok", "label": "Grok Build", "available": True},
        ]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_admin_get)
    resp = client.get(
        "/api/v1/admin/modules", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200
    assert [m["key"] for m in resp.json()] == ["claude", "grok"]


def test_hiding_a_module_only_flips_availability(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    async def fake_admin_patch(settings, table, params, body):
        assert table == "agent_modules"
        assert params == {"key": "eq.grok"}
        captured.update(body)
        return [{"key": "grok", "label": "Grok Build", "available": False}]

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.patch(
        "/api/v1/admin/modules/grok",
        json={"available": False},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured == {"available": False}


def test_hiding_an_unknown_module_is_404(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)

    async def fake_admin_patch(settings, table, params, body):
        return []

    monkeypatch.setattr("app.routers.admin.admin_patch", fake_admin_patch)
    resp = client.patch(
        "/api/v1/admin/modules/nonexistent",
        json={"available": False},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404
