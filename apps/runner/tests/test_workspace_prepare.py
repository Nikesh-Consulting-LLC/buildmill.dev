"""workspace_prepare: the manager-triggered "prepare codebase" test â€”
handle() is the runner side of the workspace.prepare round-trip â€” and the
US-85.1 full checklist that streams `prep.step` progress."""

import asyncio
from pathlib import Path

import pytest

from supervisor import workspace_prepare


class FakeConnection:
    def __init__(self, config=None):
        self.token = "tok"
        self.api_url = "https://api.example.test"
        self.config = config or {}
        self.replies: list[tuple] = []
        self.notifies: list[tuple] = []

    async def reply(self, req_id, result=None, error=None):
        self.replies.append((req_id, result, error))

    async def notify(self, method, params=None):
        self.notifies.append((method, params or {}))


def test_missing_params_replies_with_error():
    conn = FakeConnection()
    msg = {"jsonrpc": "2.0", "id": "srv-1", "method": "workspace.prepare", "params": {}}
    asyncio.run(workspace_prepare.handle(conn, msg))
    assert len(conn.replies) == 1
    req_id, result, error = conn.replies[0]
    assert req_id == "srv-1"
    assert result is None
    assert "project_id" in error


def test_ignores_other_methods():
    conn = FakeConnection()
    msg = {"jsonrpc": "2.0", "id": "srv-1", "method": "config.update", "params": {}}
    asyncio.run(workspace_prepare.handle(conn, msg))
    assert conn.replies == []


def test_success_reports_base_sha_and_bytes(monkeypatch, tmp_path):
    conn = FakeConnection()
    workdir = tmp_path / "project-abcd1234"
    workdir.mkdir()

    async def fake_prepare_checkout(prim, remote, issue, project_id=None):
        assert "worker:" not in remote  # US-89.1: remotes stay clean
        return workdir

    async def fake_git(prim, args, cwd=None, timeout=300):
        assert args == ["rev-parse", "HEAD"]
        return "deadbeef\n"

    monkeypatch.setattr(workspace_prepare.gitwork, "prepare_checkout", fake_prepare_checkout)
    monkeypatch.setattr(workspace_prepare.gitwork, "git", fake_git)
    monkeypatch.setattr(workspace_prepare.workspace, "dir_size_bytes", lambda p: 42)
    written = {}
    monkeypatch.setattr(
        workspace_prepare.workspace,
        "write_state",
        lambda p, **fields: written.update(fields),
    )

    msg = {
        "jsonrpc": "2.0",
        "id": "srv-2",
        "method": "workspace.prepare",
        "params": {
            "project_id": "abcd1234-0000-0000-0000-000000000000",
            "remote": "https://api.buildmill.dev/git/acme/demo.git",
        },
    }
    asyncio.run(workspace_prepare.handle(conn, msg))

    assert len(conn.replies) == 1
    req_id, result, error = conn.replies[0]
    assert req_id == "srv-2"
    assert error is None
    assert result["ok"] is True
    assert result["base_sha"] == "deadbeef"
    assert result["bytes"] == 42
    assert result["workdir"] == str(workdir)
    assert written["base_sha"] == "deadbeef"


def test_failure_is_reported_not_raised(monkeypatch):
    conn = FakeConnection()

    async def failing_prepare_checkout(prim, remote, issue, project_id=None):
        raise workspace_prepare.gitwork.GitError("git clone failed: repository not found")

    monkeypatch.setattr(workspace_prepare.gitwork, "prepare_checkout", failing_prepare_checkout)

    msg = {
        "jsonrpc": "2.0",
        "id": "srv-3",
        "method": "workspace.prepare",
        "params": {
            "project_id": "abcd1234-0000-0000-0000-000000000000",
            "remote": "https://api.buildmill.dev/git/acme/demo.git",
        },
    }
    asyncio.run(workspace_prepare.handle(conn, msg))

    req_id, result, error = conn.replies[0]
    assert error is None  # the failure rides in `result`, not a JSON-RPC error
    assert result["ok"] is False
    assert "repository not found" in result["error"]


# --------------------------------------------------------------------------
# US-85.1: the full checklist (job_id in params)
# --------------------------------------------------------------------------


class FakeShellResult:
    exit_code = 0
    stdout = "prep-ok\n"


class FakePrimitives:
    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        return FakeShellResult()


def _full_msg():
    return {
        "jsonrpc": "2.0",
        "id": "srv-9",
        "method": "workspace.prepare",
        "params": {
            "job_id": "job-1",
            "project_id": "abcd1234-0000-0000-0000-000000000000",
            "remote": "https://api.example.test/git/acme/demo.git",
            "tool_servers": [
                {"slug": "browser", "name": "Browser", "transport": "stdio",
                 "command": "npx playwright-mcp"},
                {"slug": "jira", "name": "Jira", "transport": "http",
                 "url": "https://api.example.test/api/v1/mcp-proxy/jira"},
            ],
        },
    }


def _run_full(conn, msg):
    async def _go():
        await workspace_prepare.handle(conn, msg)
        await asyncio.gather(*workspace_prepare._TASKS)

    asyncio.run(_go())


@pytest.fixture
def full_stubs(monkeypatch, tmp_path):
    workdir = tmp_path / "project-abcd1234"
    workdir.mkdir()

    async def fake_prepare_checkout(prim, remote, issue, project_id=None):
        assert "worker:" not in remote  # US-89.1: remotes stay clean
        return workdir

    async def fake_git(prim, args, cwd=None, timeout=300):
        return "deadbeef\n"

    removed = []
    monkeypatch.setattr(
        workspace_prepare.gitwork, "prepare_checkout", fake_prepare_checkout
    )
    monkeypatch.setattr(workspace_prepare.gitwork, "git", fake_git)
    monkeypatch.setattr(workspace_prepare.workspace, "dir_size_bytes", lambda p: 7)
    monkeypatch.setattr(
        workspace_prepare.workspace, "write_state", lambda p, **f: None
    )
    monkeypatch.setattr(
        workspace_prepare.workspace,
        "workspace_for",
        lambda pid, fallback: workdir,
    )
    monkeypatch.setattr(
        workspace_prepare.mcpconfig,
        "write",
        lambda wd, api, tok, pid, tool_servers=None: wd / ".factory-mcp.json",
    )
    monkeypatch.setattr(
        workspace_prepare.mcpconfig, "remove", lambda wd: removed.append(wd)
    )
    monkeypatch.setattr(workspace_prepare, "LocalPrimitives", FakePrimitives)
    monkeypatch.setattr(workspace_prepare.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    # Only the bash-location probe â€” Path.exists() delegates to os.path.exists
    # on newer Pythons, so a blanket True would blind the tests' own asserts.
    real_exists = workspace_prepare.os.path.exists
    monkeypatch.setattr(
        workspace_prepare.os.path,
        "exists",
        lambda p: True if p == "/usr/bin/bash" else real_exists(p),
    )

    async def mcp_ok(api_url, token):
        return True, ""

    async def http_ok(url):
        return True, ""

    monkeypatch.setattr(workspace_prepare, "_factory_mcp_answers", mcp_ok)
    monkeypatch.setattr(workspace_prepare, "_http_reachable", http_ok)
    return {"workdir": workdir, "removed": removed}


def test_full_prepare_streams_every_step_and_replies_ok(full_stubs):
    conn = FakeConnection(config={"enabled_modules": ["claude"]})
    _run_full(conn, _full_msg())

    # Every runner-owned step reported ok, in order, tagged with the job.
    ok_steps = [p["step"] for m, p in conn.notifies if p.get("status") == "ok"]
    assert ok_steps == ["invoke", "workdir", "fetch", "configure", "mcp", "tools", "checks"]
    assert all(p["job_id"] == "job-1" for _, p in conn.notifies)

    req_id, result, error = conn.replies[0]
    assert req_id == "srv-9"
    assert error is None
    assert result["ok"] is True
    assert result["base_sha"] == "deadbeef"
    # The token-bearing config never outlives the preparation that verified it.
    assert full_stubs["removed"] == [full_stubs["workdir"]]


def test_full_prepare_fetch_failure_stops_the_checklist(full_stubs, monkeypatch):
    async def failing_checkout(prim, remote, issue, project_id=None):
        raise workspace_prepare.gitwork.GitError("git clone failed: 401")

    monkeypatch.setattr(
        workspace_prepare.gitwork, "prepare_checkout", failing_checkout
    )
    conn = FakeConnection(config={"enabled_modules": ["claude"]})
    _run_full(conn, _full_msg())

    failed = [(p["step"], p["detail"]) for m, p in conn.notifies if p.get("status") == "failed"]
    assert len(failed) == 1 and failed[0][0] == "fetch"
    assert "401" in failed[0][1]
    # Later steps were never attempted.
    touched = {p["step"] for _, p in conn.notifies}
    assert "tools" not in touched and "checks" not in touched

    req_id, result, error = conn.replies[0]
    assert result["ok"] is False
    assert result["failed_step"] == "fetch"


def test_full_prepare_missing_stdio_tool_fails_the_tools_step(full_stubs, monkeypatch):
    monkeypatch.setattr(workspace_prepare.shutil, "which", lambda cmd: None)
    conn = FakeConnection(config={"enabled_modules": ["claude"]})
    _run_full(conn, _full_msg())

    req_id, result, error = conn.replies[0]
    assert result["ok"] is False
    assert result["failed_step"] == "tools"
    assert "npx" in result["error"]
    # A machine-level failure raises a runner incident (fleet news), where a
    # project-level one (see the fetch test) stays a popup line.
    incidents = [p for m, p in conn.notifies if m == "runner.incident"]
    assert len(incidents) == 1
    assert incidents[0]["kind"] == "workspace-prepare"
    assert "tools" in incidents[0]["message"]


def test_full_prepare_fetch_failure_raises_no_incident(full_stubs, monkeypatch):
    async def failing_checkout(prim, remote, issue, project_id=None):
        raise workspace_prepare.gitwork.GitError("git clone failed: 401")

    monkeypatch.setattr(
        workspace_prepare.gitwork, "prepare_checkout", failing_checkout
    )
    conn = FakeConnection(config={"enabled_modules": ["claude"]})
    _run_full(conn, _full_msg())

    assert [m for m, _ in conn.notifies if m == "runner.incident"] == []


def test_full_prepare_writes_grok_config_when_grok_enabled(full_stubs, monkeypatch, tmp_path):
    # A real .factory-mcp.json for the grok translation to read.
    workdir = full_stubs["workdir"]
    mcp_path = workdir / ".factory-mcp.json"
    mcp_path.write_text(
        '{"mcpServers": {"factory": {"type": "http", "url": "https://x/mcp",'
        ' "headers": {"X-Worker-Token": "tok"}}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        workspace_prepare.mcpconfig,
        "write",
        lambda wd, api, tok, pid, tool_servers=None: mcp_path,
    )
    conn = FakeConnection(config={"enabled_modules": ["grok"]})
    _run_full(conn, _full_msg())

    req_id, result, error = conn.replies[0]
    assert result["ok"] is True
    # Written to verify the machine can materialize it, then removed â€” the
    # toml carries the worker token in its headers.
    assert not (workdir / ".grok" / "config.toml").exists()
    mcp_step = next(p for _, p in conn.notifies if p["step"] == "mcp" and p["status"] == "ok")
    assert "grok" in mcp_step["detail"]

