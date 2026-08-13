"""US-1.15: real Claude Code provider — git flow, artifacts, timeout.

Runs against a local bare repo and a fake CLI; requires git. Execute
from apps/runner with the api venv:
    ../api/.venv/Scripts/python.exe -m pytest tests/ -v
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import provider_claude  # noqa: E402

GIT = shutil.which("git")
pytestmark = pytest.mark.skipif(GIT is None, reason="git not installed")

FAKE_CLI = '''
import json, os, pathlib, sys, time

mode = os.environ.get("FAKE_CLAUDE_MODE", "code")
out = pathlib.Path(".factory-out")
if mode == "plan":
    out.mkdir(exist_ok=True)
    (out / "plan.md").write_text("# Plan from fake CLI")
    (out / "test_plan.md").write_text("# Test plan from fake CLI")
    (out / "test_cases.json").write_text(json.dumps(
        [{"title": "t1", "steps": "s", "expected_result": "e",
          "test_types": [], "environments": []}]))
    print("planned")
elif mode == "code":
    pathlib.Path("hello.txt").write_text("made by fake claude\\n")
    out.mkdir(exist_ok=True)
    (out / "test_cases.json").write_text(json.dumps([{"title": "verify hello"}]))
    print("coded")
elif mode == "sleep":
    time.sleep(30)
    print("late")
else:
    print("did nothing")
'''


def _git(*args, cwd=None):
    proc = subprocess.run(
        [GIT, *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.fixture()
def stack(tmp_path, monkeypatch):
    bare = tmp_path / "origin.git"
    _git("init", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    _git("clone", bare.as_uri(), str(seed))
    (seed / "README.md").write_text("seed\n")
    _git("add", ".", cwd=seed)
    _git(
        "-c", "user.name=t", "-c", "user.email=t@example.com",
        "commit", "-m", "seed", cwd=seed,
    )
    _git("push", "origin", "main", cwd=seed)

    fake = tmp_path / "fake_claude.py"
    fake.write_text(FAKE_CLI)
    monkeypatch.setattr(
        provider_claude, "CLAUDE_CMD", [sys.executable, str(fake)]
    )
    monkeypatch.setattr(provider_claude, "CLAUDE_ARGS", [])
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("FACTORY_WORKER_TOKEN", raising=False)

    issue_id = str(uuid.uuid4())
    return {
        "bare": bare,
        "ctx": {
            "issue_id": issue_id,
            "title": "Add hello file",
            "type": "story",
            "story": "As a user I want hello.",
            "acceptance_criteria": ["hello.txt exists"],
            "run_kind": "code",
            "git_remote_url": bare.as_uri(),
            "branch_name": f"factory/issue-{issue_id}",
            "default_branch": "main",
        },
    }


def test_code_run_commits_and_pushes_branch(stack, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "code")
    result = provider_claude.execute(stack["ctx"], timeout_seconds=60)
    assert result.outcome == "succeeded", result.error
    assert result.branch_ref == stack["ctx"]["branch_name"]
    assert result.test_cases and result.test_cases[0]["title"] == "verify hello"

    heads = _git(
        "ls-remote", "--heads", str(stack["bare"]), stack["ctx"]["branch_name"]
    )
    assert stack["ctx"]["branch_name"] in heads

    # the pushed tree carries the change but never .factory-out
    files = _git(
        "ls-tree", "-r", "--name-only", stack["ctx"]["branch_name"],
        cwd=stack["bare"],
    )
    assert "hello.txt" in files
    assert ".factory-out" not in files


def test_plan_run_collects_artifacts_without_touching_git(stack, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "plan")
    ctx = {**stack["ctx"], "run_kind": "plan"}
    result = provider_claude.execute(ctx, timeout_seconds=60)
    assert result.outcome == "succeeded", result.error
    assert result.plan == "# Plan from fake CLI"
    assert result.test_plan == "# Test plan from fake CLI"
    assert result.test_cases[0]["title"] == "t1"

    heads = _git("ls-remote", "--heads", str(stack["bare"]))
    assert "factory/issue-" not in heads  # nothing pushed


def test_timeout_kills_cli_and_reports_failed(stack, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    result = provider_claude.execute(stack["ctx"], timeout_seconds=2)
    assert result.outcome == "failed"
    assert "killed after" in (result.stdout or "")


def test_no_changes_is_failed(stack, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "noop")
    result = provider_claude.execute(stack["ctx"], timeout_seconds=60)
    assert result.outcome == "failed"
    assert "no changes" in (result.error or "")


def test_authed_url_injects_token_for_http_only():
    assert (
        provider_claude._authed_url("http://host:8000/git/p.git", "sfw_x")
        == "http://worker:sfw_x@host:8000/git/p.git"
    )
    # existing creds are replaced, not doubled
    assert (
        provider_claude._authed_url("http://a:b@host/git/p.git", "tok")
        == "http://worker:tok@host/git/p.git"
    )
    file_url = "file:///tmp/origin.git"
    assert provider_claude._authed_url(file_url, "tok") == file_url
