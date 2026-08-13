"""US-31.6: the factory serves a workspace incrementally.

The correctness point these tests defend: a delta must name DELETIONS, and
when a delta cannot be trusted the answer is a full tree — never a delta that
might leave a file the upstream removed still sitting on disk, compiling.
"""

import pytest

from app import github
from app.factory_mcp import _workspace_delta

TOKEN = "gh_test"
OWNER, REPO = "org", "repo"
OLD = "a" * 40
NEW = "b" * 40


def _cmp(status="ahead", files=None):
    return {"status": status, "files": files or []}


async def _delta(monkeypatch, cmp_result, held=None):
    async def fake_compare(token, owner, repo, base, head):
        if isinstance(cmp_result, Exception):
            raise cmp_result
        return cmp_result

    monkeypatch.setattr(github, "compare_commits", fake_compare)
    return await _workspace_delta(
        TOKEN, OWNER, REPO, OLD, NEW, held or ["kept.py"]
    )


# ------------------------------------------------- deletions are the point


@pytest.mark.asyncio
async def test_removed_file_appears_in_delete(monkeypatch):
    out = await _delta(
        monkeypatch, _cmp(files=[{"filename": "gone.py", "status": "removed"}])
    )
    assert out is not None
    assert out["delete"] == ["gone.py"]
    # and it is NOT offered as content to write
    assert out["files"] == []


@pytest.mark.asyncio
async def test_rename_becomes_delete_plus_add(monkeypatch):
    """A workspace with no .git cannot apply a rename as one operation."""
    out = await _delta(
        monkeypatch,
        _cmp(
            files=[
                {
                    "filename": "new/name.py",
                    "status": "renamed",
                    "previous_filename": "old/name.py",
                }
            ]
        ),
    )
    assert out["delete"] == ["old/name.py"]
    assert out["add"] == ["new/name.py"]


@pytest.mark.asyncio
async def test_rename_without_previous_name_forces_full(monkeypatch):
    out = await _delta(
        monkeypatch,
        _cmp(files=[{"filename": "new.py", "status": "renamed"}]),
    )
    assert out is None  # do not guess which path to delete


@pytest.mark.asyncio
async def test_modified_held_file_is_update_unheld_is_add(monkeypatch):
    out = await _delta(
        monkeypatch,
        _cmp(
            files=[
                {"filename": "kept.py", "status": "modified"},
                {"filename": "fresh.py", "status": "modified"},
            ]
        ),
        held=["kept.py"],
    )
    assert out["update"] == ["kept.py"]
    assert out["add"] == ["fresh.py"]


# --------------------------------------------- when in doubt, answer full


@pytest.mark.asyncio
async def test_unrelated_history_forces_full(monkeypatch):
    out = await _delta(monkeypatch, github.GitHubError("not comparable"))
    assert out is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["behind", "diverged"])
async def test_non_ancestor_base_forces_full(monkeypatch, status):
    """A force-push means the agent holds something that is not an ancestor
    of the new base. Re-establish rather than patch."""
    out = await _delta(monkeypatch, _cmp(status=status))
    assert out is None


@pytest.mark.asyncio
async def test_unknown_status_forces_full(monkeypatch):
    out = await _delta(
        monkeypatch, _cmp(files=[{"filename": "x.py", "status": "surprise"}])
    )
    assert out is None


@pytest.mark.asyncio
async def test_missing_filename_forces_full(monkeypatch):
    out = await _delta(monkeypatch, _cmp(files=[{"status": "modified"}]))
    assert out is None


@pytest.mark.asyncio
async def test_identical_shas_yield_an_empty_delta(monkeypatch):
    """No upstream change must answer an empty delta, not a whole tree."""
    out = await _delta(monkeypatch, _cmp(status="identical"))
    assert out == {"add": [], "update": [], "delete": [], "files": []}
