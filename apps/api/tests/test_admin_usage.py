"""US-60.2: the superadmin's cross-org usage view — the same grain
`/llm/orgs/{org_id}/spend` offers one org, minus the org filter, gated by
`require_platform_admin` instead of `is_org_member`."""

from __future__ import annotations


def _grant_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


def _deny_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        return False

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


def test_non_admin_gets_403(client, make_token, monkeypatch):
    _deny_admin(monkeypatch)
    resp = client.get(
        "/api/v1/admin/usage", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 403


def test_admin_usage_calls_spend_breakdown_with_no_org_filter_by_default(
    client, make_token, monkeypatch
):
    _grant_admin(monkeypatch)
    captured = {}

    def fake_breakdown(settings, org_id, **kw):
        captured["org_id"] = org_id
        captured.update(kw)
        return {"group_by": kw.get("group_by", "org"), "days": 30, "rows": [], "totals": {}}

    monkeypatch.setattr("app.db.spend_breakdown", fake_breakdown)
    resp = client.get(
        "/api/v1/admin/usage", headers={"Authorization": f"Bearer {make_token()}"}
    )
    assert resp.status_code == 200, resp.text
    assert captured["org_id"] is None
    assert captured["group_by"] == "org"


def test_admin_usage_can_drill_into_one_org(client, make_token, monkeypatch):
    _grant_admin(monkeypatch)
    captured = {}

    def fake_breakdown(settings, org_id, **kw):
        captured["org_id"] = org_id
        return {"group_by": kw.get("group_by"), "days": 30, "rows": [], "totals": {}}

    monkeypatch.setattr("app.db.spend_breakdown", fake_breakdown)
    resp = client.get(
        "/api/v1/admin/usage",
        params={"org_id": "11111111-1111-1111-1111-111111111111", "group_by": "project"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["org_id"] == "11111111-1111-1111-1111-111111111111"
