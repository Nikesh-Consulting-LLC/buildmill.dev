"""GET /api/v1/projects/{id}/guidelines.md (US-1.18)."""

import uuid

from app.supabase import RpcError

PROJECT_ID = str(uuid.uuid4())


def _patch_rpc(monkeypatch, behavior):
    async def fake_rpc(settings, token, fn, args):
        assert fn == "assemble_project_guidelines"
        assert args == {"p_project": PROJECT_ID}
        return behavior()

    monkeypatch.setattr("app.routers.projects.rpc", fake_rpc)


def test_guidelines_md_happy_path(client, make_token, monkeypatch):
    _patch_rpc(monkeypatch, lambda: "## Tech stack\n\nPython + FastAPI.")

    resp = client.get(
        f"/api/v1/projects/{PROJECT_ID}/guidelines.md",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert resp.text == "## Tech stack\n\nPython + FastAPI."


def test_guidelines_md_empty_is_empty_string(client, make_token, monkeypatch):
    _patch_rpc(monkeypatch, lambda: "")

    resp = client.get(
        f"/api/v1/projects/{PROJECT_ID}/guidelines.md",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200
    assert resp.text == ""


def test_guidelines_md_cross_org_is_404(client, make_token, monkeypatch):
    # RLS hides other orgs' projects: the RPC sees no row and errors the
    # same way dispatch_issue does for issues not in the caller's org.
    def raise_not_found():
        raise RpcError("project not found")

    _patch_rpc(monkeypatch, raise_not_found)

    resp = client.get(
        f"/api/v1/projects/{PROJECT_ID}/guidelines.md",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 404


def test_guidelines_md_without_token_is_401(client):
    resp = client.get(f"/api/v1/projects/{PROJECT_ID}/guidelines.md")
    assert resp.status_code == 401
