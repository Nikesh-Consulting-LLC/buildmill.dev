"""US-98.6: approving a merge merges — it does not squash.

A work item's PR wants a squash: one item, one commit. A merge run's PR is
the opposite case. Squashing it would collapse every source branch's history
into a single new commit and destroy the only record of where those changes
came from — the same reason CLAUDE.md forbids squashing release PRs to
`prod`, and the same way the drift check there stops being meaningful.

The negative case matters as much as the positive one: this must not
accidentally change the method for every other run kind.
"""

from __future__ import annotations

import pytest

from app.routers import reviews


@pytest.fixture()
def captured(monkeypatch):
    seen: dict = {}

    async def fake_resolve(settings, user_token, org_id, repo_full):
        return "gh_token", "the test credential"

    async def fake_merge(token, owner, repo, number, merge_method="squash"):
        seen["merge_method"] = merge_method
        seen["pr"] = (owner, repo, number)
        return "merged000"

    monkeypatch.setattr(
        "app.github_tokens.resolve_for_user", fake_resolve
    )
    monkeypatch.setattr("app.github.merge_pull_request", fake_merge)
    return seen


PR = "https://github.com/acme/widgets/pull/7"


@pytest.mark.anyio
async def test_a_merge_run_uses_a_merge_commit(captured):
    await reviews._merge_pr(None, "tok", "org", PR, "merge")
    assert captured["merge_method"] == "merge"


@pytest.mark.anyio
async def test_the_default_is_still_squash(captured):
    """Every existing caller passes no method and must keep squashing."""
    await reviews._merge_pr(None, "tok", "org", PR)
    assert captured["merge_method"] == "squash"


@pytest.mark.anyio
async def test_a_code_run_still_squashes(captured):
    await reviews._merge_pr(None, "tok", "org", PR, "squash")
    assert captured["merge_method"] == "squash"


@pytest.mark.anyio
async def test_a_simulated_pr_never_reaches_github(captured):
    result = await reviews._merge_pr(
        None, "tok", "org", f"{reviews.SIMULATED_PREFIX}whatever", "merge"
    )
    assert result == "simulated"
    assert "merge_method" not in captured
