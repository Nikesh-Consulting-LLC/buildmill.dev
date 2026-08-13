"""US-31.8: a project gets one workspace, not one per issue.

The properties worth pinning: the folder is the PROJECT's, repair escalates
cheapest-first (invalidate before wipe, so a stale tree does not cost minutes
of dependency install), and reclamation says what it removed.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from supervisor import workspace
from supervisor.modules.base import RunContext


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- identity


def test_two_issues_on_one_project_share_one_folder():
    a = workspace.workspace_for("11111111-2222-3333-4444-555555555555", "run-a")
    b = workspace.workspace_for("11111111-2222-3333-4444-555555555555", "run-b")
    assert a == b
    assert a.name.startswith("project-")


def test_different_projects_do_not_share():
    a = workspace.workspace_for("aaaaaaaa-0000-0000-0000-000000000000", "r")
    b = workspace.workspace_for("bbbbbbbb-0000-0000-0000-000000000000", "r")
    assert a != b


def test_no_project_falls_back_to_the_run_not_a_shared_folder():
    """Issue-less kinds (deploy, release) and older servers must not all
    collide into one directory."""
    a = workspace.workspace_for(None, "run-aaaa")
    b = workspace.workspace_for(None, "run-bbbb")
    assert a != b
    assert a.name.startswith("run-")


# ---------------------------------------------------------------- state


def test_state_survives_and_touch_updates_it(isolated_root):
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    workspace.write_state(ws, base_sha="abc123")
    assert workspace.read_state(ws)["base_sha"] == "abc123"
    workspace.touch(ws)
    assert workspace.read_state(ws)["base_sha"] == "abc123"  # touch keeps it


def test_corrupt_state_file_is_not_an_error(isolated_root):
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    (ws / workspace.STATE_FILE).write_text("{not json", encoding="utf-8")
    assert workspace.read_state(ws) == {}


# ---------------------------------------------------------------- repair


def test_invalidate_keeps_artifacts_and_forgets_the_base(isolated_root):
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("x", encoding="utf-8")
    workspace.write_state(ws, base_sha="deadbeefcafe")

    changed = workspace.invalidate(ws)
    assert changed and "invalidated" in changed
    assert (ws / "node_modules" / "dep.js").exists()  # the expensive half stays
    assert "base_sha" not in workspace.read_state(ws)


def test_invalidate_twice_reports_nothing_the_second_time(isolated_root):
    """US-27.12: a repair that changed nothing must say so, so the caller
    stops instead of repeating itself — and here it escalates to wipe."""
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    workspace.write_state(ws, base_sha="abc")
    assert workspace.invalidate(ws) is not None
    assert workspace.invalidate(ws) is None


def test_wipe_removes_everything(isolated_root):
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    (ws / "node_modules").mkdir()
    changed = workspace.wipe(ws)
    assert changed and "including installed dependencies" in changed
    assert not ws.exists()


def test_repair_escalates_invalidate_then_wipe(isolated_root, monkeypatch):
    """The ordered ladder: reclone invalidates first, and only wipes once
    there is no source record left to invalidate."""
    from supervisor.repair import apply_repair

    ctx = RunContext(
        run_id="r1", kind="code", context={"project_id": "p1"},
    )
    ws = workspace.ensure(workspace.workspace_for("p1", "r1"))
    (ws / "node_modules").mkdir()
    workspace.write_state(ws, base_sha="abc")

    class FakePrim:
        async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
            raise AssertionError("reclone must not run a shell command")

    first = asyncio.run(apply_repair("reclone", ctx, FakePrim()))
    assert first and "invalidated" in first
    assert ws.exists()  # tier one kept the folder

    second = asyncio.run(apply_repair("reclone", ctx, FakePrim()))
    assert second and "including installed dependencies" in second
    assert not ws.exists()  # tier two


# ---------------------------------------------------------------- reclaim


def test_reclaim_removes_only_stale_workspaces_and_reports(isolated_root):
    fresh = workspace.ensure(workspace.workspace_for("fresh", "r"))
    workspace.touch(fresh)
    stale = workspace.ensure(workspace.workspace_for("stale", "r"))
    (stale / "big.bin").write_text("x" * 1000, encoding="utf-8")
    (stale / workspace.STATE_FILE).write_text(
        json.dumps({"touched_at": time.time() - 60 * 60 * 24 * 30}),
        encoding="utf-8",
    )

    out = workspace.reclaim()
    names = [r["name"] for r in out["removed"]]
    assert stale.name in names
    assert fresh.name not in names
    assert out["freed_bytes"] > 0  # says what it freed
    assert fresh.exists() and not stale.exists()


def test_reclaim_uses_mtime_when_there_is_no_state_file(isolated_root):
    """Pre-us-31.8 folders have no state file; they must still be reclaimable
    rather than living forever."""
    legacy = isolated_root / "issue-abcd1234"
    legacy.mkdir()
    import os

    old = time.time() - 60 * 60 * 24 * 30
    os.utime(legacy, (old, old))
    out = workspace.reclaim()
    assert legacy.name in [r["name"] for r in out["removed"]]


def test_legacy_issue_dirs_are_reclaimed_on_sight(isolated_root):
    """They are orphaned the moment the path scheme changes — nothing will
    ever look in them again."""
    legacy = isolated_root / "issue-deadbeef"
    legacy.mkdir()
    (legacy / "f.txt").write_text("hello", encoding="utf-8")
    keep = workspace.ensure(workspace.workspace_for("p1", "r"))

    out = workspace.reclaim_legacy_issue_dirs()
    assert [r["name"] for r in out["removed"]] == ["issue-deadbeef"]
    assert not legacy.exists()
    assert keep.exists()


def test_usage_reports_per_workspace_bytes(isolated_root):
    ws = workspace.ensure(workspace.workspace_for("p1", "r"))
    (ws / "a.bin").write_text("x" * 500, encoding="utf-8")
    out = workspace.usage()
    assert out["total_bytes"] >= 500
    assert any(w["name"] == ws.name for w in out["workspaces"])
