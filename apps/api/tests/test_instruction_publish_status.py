"""US-99.4: an edit that has not reached the repository says so.

Publishing stopped being automatic when us-99.4 retired the dispatch-time
write — a publish now rewrites AGENTS.md, CLAUDE.md and every `.buildmill/`
file whole and deletes the ones a kind no longer needs, which is far too much
to happen as a side effect of pressing Dispatch.

The cost of that decision is a new state: edited, but not in the repository,
with agents still running on yesterday's files. These pin that the state is
visible and that it clears only on a real publish.
"""

from __future__ import annotations

import uuid

import pytest

from app import repo_docs

PROJECT_ID = str(uuid.uuid4())
GUIDELINES = "## House style\n\nUse tabs."
INSTRUCTIONS = {"code": "Build it well.", "plan": "Think first."}


def _digest(guidelines=GUIDELINES, instructions=None, tree=True):
    files, deletes = repo_docs.instruction_file_plan(
        dict(INSTRUCTIONS if instructions is None else instructions),
        guidelines,
        tree,
    )
    return repo_docs.publish_hash(files, deletes)


def _wire(monkeypatch, *, synced_hash=None, guidelines=GUIDELINES,
          instructions=None, repo="acme/demo"):
    async def fake_get(settings, token, path, params):
        assert path == "projects"
        return [
            {
                "id": PROJECT_ID,
                "repo_full_name": repo,
                "docs_tree_enabled": True,
                "instructions_synced_hash": synced_hash,
                "instructions_synced_at": None,
                "instructions_synced_sha": None,
            }
        ]

    async def fake_rpc(settings, token, fn, args):
        assert fn == "assemble_project_guidelines"
        return guidelines

    monkeypatch.setattr("app.routers.projects.postgrest_get", fake_get)
    monkeypatch.setattr("app.routers.projects.rpc", fake_rpc)
    monkeypatch.setattr(
        "app.routers.projects.db.get_project_instructions_for_publish",
        lambda s, p: dict(INSTRUCTIONS if instructions is None else instructions),
    )


def _get(client, make_token):
    return client.get(
        f"/api/v1/projects/{PROJECT_ID}/instructions/status",
        headers={"Authorization": f"Bearer {make_token()}"},
    )


def test_a_never_published_project_is_unpublished(client, make_token, monkeypatch):
    _wire(monkeypatch, synced_hash=None)
    body = _get(client, make_token).json()
    assert body["unpublished"] is True
    assert body["published_hash"] is None


def test_a_matching_hash_is_published(client, make_token, monkeypatch):
    _wire(monkeypatch, synced_hash=_digest())
    body = _get(client, make_token).json()
    assert body["unpublished"] is False


def test_editing_an_instruction_makes_it_unpublished_again(
    client, make_token, monkeypatch
):
    """The state has to survive the thing that creates it."""
    published = _digest()
    _wire(
        monkeypatch,
        synced_hash=published,
        instructions={"code": "Build it DIFFERENTLY.", "plan": "Think first."},
    )
    assert _get(client, make_token).json()["unpublished"] is True


def test_editing_the_conventions_also_counts(client, make_token, monkeypatch):
    """Guidelines live in the published set too (us-99.3), so a refresh that
    changes them must show as unpublished."""
    _wire(monkeypatch, synced_hash=_digest(), guidelines="## New\n\nUse spaces.")
    assert _get(client, make_token).json()["unpublished"] is True


def test_clearing_an_instruction_counts_as_a_change(client, make_token, monkeypatch):
    """The bug the old block_hash could not see: a kind going blank turns a
    write into a DELETE, and nothing about the remaining text changes. If
    deletions were outside the hash this would read as 'published'."""
    _wire(
        monkeypatch,
        synced_hash=_digest(),
        instructions={"code": "Build it well."},  # plan cleared
    )
    body = _get(client, make_token).json()
    assert body["unpublished"] is True
    assert ".buildmill/Plan.md" in body["deletes"]
    assert ".buildmill/Plan.md" not in body["files"]


def test_it_names_what_would_be_written_and_removed(client, make_token, monkeypatch):
    _wire(monkeypatch)
    body = _get(client, make_token).json()
    assert ".buildmill/Code.md" in body["files"]
    assert "AGENTS.md" in body["files"]
    assert "CLAUDE.md" in body["files"]
    # us-100.2: retired — written nowhere, deleted everywhere.
    assert ".buildmill/Guidelines.md" not in body["files"]
    assert ".buildmill/Guidelines.md" in body["deletes"]
    # Every kind with no content is a delete, not an empty file.
    assert ".buildmill/RCA.md" in body["deletes"]


def test_the_ownership_notice_is_always_present(client, make_token, monkeypatch):
    """us-99.2 AC6: standing copy, not a dialog dismissed once — it is true
    on every publish, including the ones that destroy something."""
    _wire(monkeypatch, synced_hash=_digest())
    body = _get(client, make_token).json()
    assert "owns AGENTS.md" in body["ownership_notice"]
    assert "whole" in body["ownership_notice"]


def test_a_project_without_a_repo_says_so(client, make_token, monkeypatch):
    _wire(monkeypatch, repo=None)
    assert _get(client, make_token).json()["has_repo"] is False


def test_unknown_project_is_404(client, make_token, monkeypatch):
    async def empty(settings, token, path, params):
        return []

    monkeypatch.setattr("app.routers.projects.postgrest_get", empty)
    assert _get(client, make_token).status_code == 404


def test_requires_authentication(client):
    resp = client.get(f"/api/v1/projects/{PROJECT_ID}/instructions/status")
    assert resp.status_code in (401, 403)


# --- us-99.4 AC5: dispatch no longer publishes -----------------------------


def test_dispatch_does_not_publish_instructions(monkeypatch):
    """The sharp edge this story exists to remove.

    Until now, dispatching a run wrote AGENTS.md as a side effect. Under
    us-99.2 that write rewrites AGENTS.md, CLAUDE.md and every `.buildmill/`
    file whole — and deletes some. Nobody should be able to trigger that by
    pressing Dispatch, least of all without knowing they did.

    The docs tree still syncs: it is additive and per-story, and that is what
    US-22.4 put there.
    """
    import asyncio

    from app.routers import issues as issues_router

    called: list[str] = []

    async def instructions(*a, **k):
        called.append("instructions")

    async def tree(*a, **k):
        called.append("tree")

    monkeypatch.setattr(
        "app.repo_docs.sync_instruction_files", instructions
    )
    monkeypatch.setattr("app.repo_docs.sync_tree", tree)

    asyncio.run(issues_router._sync_repo_before_dispatch(None, str(uuid.uuid4())))

    assert "instructions" not in called, (
        "dispatch published instructions — a whole-file overwrite must never "
        "fire as a side effect of dispatching work"
    )
    assert called == ["tree"]


def test_dispatch_with_no_project_does_nothing(monkeypatch):
    import asyncio

    from app.routers import issues as issues_router

    async def boom(*a, **k):
        raise AssertionError("nothing should be synced without a project")

    monkeypatch.setattr("app.repo_docs.sync_tree", boom)
    asyncio.run(issues_router._sync_repo_before_dispatch(None, None))
