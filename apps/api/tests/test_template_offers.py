"""US-99.7: a changed template offers itself; it never pushes.

A template seeds a project once and is never read again, so a superadmin
fixing a genuinely wrong instruction fixes it for projects that do not exist
yet and nobody else. Pushing the fix automatically is worse — a project that
deliberately rewrote that instruction would have its work reverted from
somewhere its manager cannot see.

`safe_to_accept` is the whole design: it is true only when
`worker_instructions.updated_by` is null, which the seeding trigger leaves
and any manager edit stamps. Safe means accepting reverts nothing.
"""

from __future__ import annotations

import uuid

PROJECT_ID = str(uuid.uuid4())


def _wire(monkeypatch, *, template_id="tpl-1", offers=None):
    async def fake_get(settings, token, path, params):
        assert path == "projects"
        return [{"id": PROJECT_ID, "org_template_id": template_id}]

    monkeypatch.setattr("app.routers.projects.postgrest_get", fake_get)
    monkeypatch.setattr(
        "app.routers.projects.db.get_template_instruction_offers",
        lambda s, p: list(offers or []),
    )


def _get(client, make_token):
    return client.get(
        f"/api/v1/projects/{PROJECT_ID}/instructions/template-offers",
        headers={"Authorization": f"Bearer {make_token()}"},
    )


def _offer(kind, safe):
    return {
        "kind": kind,
        "template_content": "new text",
        "project_content": "old text",
        "safe_to_accept": safe,
    }


def test_a_project_bound_to_no_template_has_nothing_to_offer(
    client, make_token, monkeypatch
):
    _wire(monkeypatch, template_id=None)
    body = _get(client, make_token).json()
    assert body["bound_to_template"] is False
    assert body["offers"] == []


def test_an_unedited_instruction_is_safe_to_accept(client, make_token, monkeypatch):
    _wire(monkeypatch, offers=[_offer("code", True)])
    body = _get(client, make_token).json()
    assert body["offers"][0]["safe_to_accept"] is True
    assert body["safe_count"] == 1
    assert body["conflicting_count"] == 0


def test_an_edited_instruction_is_flagged_as_conflicting(
    client, make_token, monkeypatch
):
    """The case the whole story exists for: accepting here would revert a
    manager's deliberate local rewrite."""
    _wire(monkeypatch, offers=[_offer("code", False)])
    body = _get(client, make_token).json()
    assert body["offers"][0]["safe_to_accept"] is False
    assert body["conflicting_count"] == 1
    # Both texts travel, so the surface can show what would be lost rather
    # than asking the manager to take it on trust.
    assert body["offers"][0]["project_content"] == "old text"
    assert body["offers"][0]["template_content"] == "new text"


def test_the_counts_split_a_mixed_set(client, make_token, monkeypatch):
    _wire(
        monkeypatch,
        offers=[
            _offer("code", True),
            _offer("plan", False),
            _offer("bug_rca", True),
        ],
    )
    body = _get(client, make_token).json()
    assert body["safe_count"] == 2
    assert body["conflicting_count"] == 1


def test_no_differences_is_an_empty_offer_not_an_error(
    client, make_token, monkeypatch
):
    _wire(monkeypatch, offers=[])
    body = _get(client, make_token).json()
    assert body["bound_to_template"] is True
    assert body["offers"] == []
    assert body["safe_count"] == 0


def test_nothing_is_applied_by_reading_the_offers(client, make_token, monkeypatch):
    """An offer is a read. If this endpoint ever writes, a superadmin's edit
    would reach every project the moment anyone opened a page."""

    def must_not_write(*a, **k):
        raise AssertionError("reading offers must not modify anything")

    _wire(monkeypatch, offers=[_offer("code", True)])
    monkeypatch.setattr(
        "app.routers.projects.db.record_instructions_sync", must_not_write
    )
    assert _get(client, make_token).status_code == 200


def test_unknown_project_is_404(client, make_token, monkeypatch):
    async def empty(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.projects.postgrest_get", empty)
    assert _get(client, make_token).status_code == 404


def test_requires_authentication(client):
    resp = client.get(
        f"/api/v1/projects/{PROJECT_ID}/instructions/template-offers"
    )
    assert resp.status_code in (401, 403)
