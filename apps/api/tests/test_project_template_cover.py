"""US-118.1: a catalog template carries a cover, and the API keeps its shape.

The browser puts the object in the public `template-images` bucket under
Storage RLS; the API only ever records *where* it is. So the one thing these
routes must get right is the shape of `image_path` — three legal forms, and
a 422 for anything else, so a typo never reaches the DB CHECK as a 409 —
plus the two side effects: a duplicate does not share an uploaded object,
and a delete takes the object with it without ever failing on its absence.
"""

from __future__ import annotations

import pytest

from app import storage
from app.routers.admin import (
    TEMPLATE_IMAGE_BUCKET,
    template_cover_object,
    validate_template_image_path,
)

TEMPLATE_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def as_platform_admin(monkeypatch):
    async def fake_rpc(settings, user_token, fn, args):
        assert fn == "is_platform_admin"
        return True

    monkeypatch.setattr("app.routers.admin.rpc", fake_rpc)


def _auth(make_token):
    return {"Authorization": f"Bearer {make_token()}"}


def _patch_fixtures(monkeypatch):
    patched = {}

    async def fake_get(settings, path, params):
        return [{"id": TEMPLATE_ID, "is_default": False, "is_disabled": False}]

    async def fake_patch(settings, path, params, body):
        patched["body"] = body
        return [{"id": TEMPLATE_ID, **body}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_patch", fake_patch)
    return patched


# --- AC3: the three shapes, and 422 for the rest ---------------------------


@pytest.mark.parametrize(
    "value",
    [None, "builtin/web-app", "builtin/full-stack", template_cover_object(TEMPLATE_ID)],
)
def test_patch_accepts_each_legal_image_path(client, make_token, monkeypatch, value):
    patched = _patch_fixtures(monkeypatch)
    resp = client.patch(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}",
        json={"image_path": value},
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    # `null` must reach PostgREST as an explicit null, not be dropped — it is
    # how Remove clears the cover.
    assert patched["body"] == {"image_path": value}


@pytest.mark.parametrize(
    "value",
    [
        template_cover_object(OTHER_ID),  # another template's object
        f"catalog/{TEMPLATE_ID}/cover.png",  # an extension
        f"00000000-0000-0000-0000-000000000000/{TEMPLATE_ID}/cover",  # an org path
        "https://example.com/cover.png",  # a URL
        "builtin/Web App",  # not a slug
        "builtin/",
        "",
    ],
)
def test_patch_refuses_any_other_image_path_naming_the_field(
    client, make_token, monkeypatch, value
):
    async def must_not_patch(*a, **k):
        raise AssertionError("an illegal image_path must never reach PostgREST")

    async def fake_get(settings, path, params):
        return [{"id": TEMPLATE_ID, "is_default": False, "is_disabled": False}]

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_patch", must_not_patch)
    resp = client.patch(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}",
        json={"image_path": value},
        headers=_auth(make_token),
    )
    assert resp.status_code == 422, resp.text
    assert "image_path" in resp.json()["detail"]


def test_the_validator_is_the_same_rule_the_route_uses():
    validate_template_image_path(TEMPLATE_ID, None)
    validate_template_image_path(TEMPLATE_ID, "builtin/site")
    validate_template_image_path(TEMPLATE_ID, f"catalog/{TEMPLATE_ID}/cover")
    with pytest.raises(Exception):
        validate_template_image_path(TEMPLATE_ID, f"catalog/{OTHER_ID}/cover")


# --- AC3: delete takes the object with it, best-effort ---------------------


def test_delete_removes_the_cover_object_and_ignores_a_missing_one(
    client, make_token, monkeypatch
):
    deleted = {}

    async def fake_get(settings, path, params):
        return [{"id": TEMPLATE_ID, "is_default": False}]

    async def fake_delete(settings, path, params):
        deleted["row"] = (path, params)

    async def fake_delete_object(settings, path, bucket=storage.DATA_BUCKET):
        deleted["object"] = (bucket, path)
        # storage.delete_object already treats a missing object as success;
        # returning normally here models that.

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_delete", fake_delete)
    monkeypatch.setattr("app.storage.delete_object", fake_delete_object)
    resp = client.delete(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}", headers=_auth(make_token)
    )
    assert resp.status_code == 200, resp.text
    assert deleted["row"] == ("project_templates", {"id": f"eq.{TEMPLATE_ID}"})
    assert deleted["object"] == (TEMPLATE_IMAGE_BUCKET, f"catalog/{TEMPLATE_ID}/cover")


def test_a_storage_fault_does_not_fail_the_delete(client, make_token, monkeypatch):
    async def fake_get(settings, path, params):
        return [{"id": TEMPLATE_ID, "is_default": False}]

    async def fake_delete(settings, path, params):
        return None

    async def failing_delete_object(settings, path, bucket=storage.DATA_BUCKET):
        raise storage.StorageError("Storage delete failed (503): down")

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_delete", fake_delete)
    monkeypatch.setattr("app.storage.delete_object", failing_delete_object)
    resp = client.delete(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}", headers=_auth(make_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


# --- AC3: a duplicate never shares an uploaded object -----------------------


@pytest.mark.parametrize(
    "source_image, expected",
    [
        (f"catalog/{TEMPLATE_ID}/cover", None),  # an upload is one row's object
        ("builtin/web-app", "builtin/web-app"),  # a built-in is a name; it travels
        (None, None),
    ],
)
def test_duplicate_carries_a_builtin_cover_but_not_an_upload(
    client, make_token, monkeypatch, source_image, expected
):
    posted = {}

    async def fake_get(settings, path, params):
        if path == "project_templates" and params.get("select") == "*":
            return [
                {
                    "id": TEMPLATE_ID,
                    "key": "web-app",
                    "name": "Web app",
                    "description": "**Bold** words.",
                    "category": "General",
                    "agent_instructions": "# Doc",
                    "image_path": source_image,
                }
            ]
        if path == "project_templates":
            return [{"key": "web-app"}]
        return []  # no sections

    async def fake_post(settings, path, body):
        if path == "project_templates":
            posted.update(body)
            return [{"id": OTHER_ID, **body}]
        return body

    monkeypatch.setattr("app.routers.admin.admin_get", fake_get)
    monkeypatch.setattr("app.routers.admin.admin_post", fake_post)
    resp = client.post(
        f"/api/v1/admin/project-templates/{TEMPLATE_ID}/duplicate",
        headers=_auth(make_token),
    )
    assert resp.status_code == 200, resp.text
    assert posted["description"] == "**Bold** words."
    assert posted["category"] == "General"
    assert posted["image_path"] == expected
