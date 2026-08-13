"""US-78.2: what the agent may ask the runner to do, and what it may not.

The path rail is the one that matters: the machine holds `env/<N>.env` with the
slot's `FACTORY_WORKER_TOKEN`, the single secret on the box. Without this,
`fs/read_text_file` is an exfiltration route with a documented API.
"""

import asyncio
from pathlib import Path

import pytest

from supervisor.acp.client import AcpError
from supervisor.acp.handlers import ClientHandlers
from supervisor.modules.base import ShellResult


class FakePrim:
    def __init__(self, stdout="ok", exit_code=0, allowed=True):
        self.calls: list[list[str]] = []
        self._stdout = stdout
        self._exit = exit_code
        self._allowed = allowed

    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        self.calls.append(argv)
        if on_line:
            for line in self._stdout.splitlines():
                on_line(line)
        return ShellResult(
            argv=argv, exit_code=self._exit, stdout=self._stdout, allowed=self._allowed
        )


def test_a_shell_line_command_is_split_like_a_shell_would(tmp_path: Path):
    """2026-08-13 (FEAT-2.8): the grok CLI sends its whole shell line as ONE
    `command` string. Treated as argv[0], the OS was asked to exec a binary
    literally named "/usr/bin/bash -lc '…'" — every terminal call died with
    [Errno 2] and the agent concluded bash was missing on a healthy machine."""
    prim = FakePrim()
    h = ClientHandlers(prim, str(tmp_path))

    async def go():
        out = await h(
            "terminal/create",
            {"command": "/usr/bin/bash -lc 'echo hello && pwd'"},
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return out

    out = asyncio.run(go())
    assert out["terminalId"]
    assert prim.calls == [["/usr/bin/bash", "-lc", "echo hello && pwd"]]


def test_a_bare_program_with_args_stays_verbatim(tmp_path: Path):
    prim = FakePrim()
    h = ClientHandlers(prim, str(tmp_path))

    async def go():
        await h("terminal/create", {"command": "git", "args": ["status", "--porcelain"]})
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert prim.calls == [["git", "status", "--porcelain"]]


def test_reading_a_file_inside_the_workspace_works(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    h = ClientHandlers(FakePrim(), str(tmp_path))
    out = asyncio.run(h("fs/read_text_file", {"path": str(tmp_path / "a.txt")}))
    assert out["content"] == "hello\nworld\n"


def test_line_and_limit_are_one_based(tmp_path: Path):
    (tmp_path / "a.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    h = ClientHandlers(FakePrim(), str(tmp_path))
    out = asyncio.run(
        h("fs/read_text_file", {"path": str(tmp_path / "a.txt"), "line": 2, "limit": 1})
    )
    assert out["content"] == "two\n"


def test_reading_outside_the_workspace_is_refused(tmp_path: Path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    secret = tmp_path / "3.env"
    secret.write_text("FACTORY_WORKER_TOKEN=hunter2\n", encoding="utf-8")
    h = ClientHandlers(FakePrim(), str(workspace))
    with pytest.raises(AcpError) as caught:
        asyncio.run(h("fs/read_text_file", {"path": str(secret)}))
    assert "outside this session" in caught.value.message


def test_a_traversal_out_of_the_workspace_is_refused(tmp_path: Path):
    """Comparing the unresolved string would accept `<workspace>/../3.env`."""
    workspace = tmp_path / "work"
    workspace.mkdir()
    (tmp_path / "3.env").write_text("FACTORY_WORKER_TOKEN=hunter2\n", encoding="utf-8")
    h = ClientHandlers(FakePrim(), str(workspace))
    with pytest.raises(AcpError):
        asyncio.run(
            h("fs/read_text_file", {"path": str(workspace / ".." / "3.env")})
        )


def test_a_relative_path_is_refused_because_acp_says_absolute(tmp_path: Path):
    h = ClientHandlers(FakePrim(), str(tmp_path))
    with pytest.raises(AcpError) as caught:
        asyncio.run(h("fs/read_text_file", {"path": "a.txt"}))
    assert "absolute" in caught.value.message


def test_writing_creates_parents_inside_the_workspace(tmp_path: Path):
    h = ClientHandlers(FakePrim(), str(tmp_path))
    target = tmp_path / "src" / "new.py"
    asyncio.run(h("fs/write_text_file", {"path": str(target), "content": "x = 1\n"}))
    assert target.read_text(encoding="utf-8") == "x = 1\n"


def test_writing_outside_the_workspace_is_refused(tmp_path: Path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    h = ClientHandlers(FakePrim(), str(workspace))
    with pytest.raises(AcpError):
        asyncio.run(
            h(
                "fs/write_text_file",
                {"path": str(tmp_path / "escaped.txt"), "content": "no"},
            )
        )


def test_a_terminal_runs_through_the_audited_run_shell(tmp_path: Path):
    prim = FakePrim(stdout="line one\nline two")
    h = ClientHandlers(prim, str(tmp_path))

    async def scenario():
        created = await h("terminal/create", {"command": "ls", "args": ["-la"]})
        term = created["terminalId"]
        await h("terminal/wait_for_exit", {"terminalId": term})
        out = await h("terminal/output", {"terminalId": term})
        return out

    out = asyncio.run(scenario())
    assert prim.calls == [["ls", "-la"]], "must go through run_shell, not spawn"
    assert "line one" in out["output"]
    assert out["exitStatus"]["exitCode"] == 0


def test_a_policy_denied_terminal_reports_the_denial(tmp_path: Path):
    prim = FakePrim(stdout="[denied by policy]", exit_code=126, allowed=False)
    h = ClientHandlers(prim, str(tmp_path))

    async def scenario():
        created = await h("terminal/create", {"command": "rm", "args": ["-rf", "/"]})
        await h("terminal/wait_for_exit", {"terminalId": created["terminalId"]})
        return await h("terminal/output", {"terminalId": created["terminalId"]})

    out = asyncio.run(scenario())
    assert "denied by policy" in out["output"]


def test_an_unknown_terminal_is_an_error_not_a_silent_empty(tmp_path: Path):
    h = ClientHandlers(FakePrim(), str(tmp_path))
    with pytest.raises(AcpError):
        asyncio.run(h("terminal/output", {"terminalId": "nope"}))


def test_permission_takes_the_first_allow_option_and_records_it(tmp_path: Path):
    recorded = []

    async def on_permission(tool_call, outcome, reason):
        recorded.append((tool_call.get("title"), outcome, reason))

    h = ClientHandlers(FakePrim(), str(tmp_path), on_permission=on_permission)
    out = asyncio.run(
        h(
            "session/request_permission",
            {
                "toolCall": {"title": "Bash"},
                "options": [
                    {"optionId": "no", "kind": "reject_once"},
                    {"optionId": "yes", "kind": "allow_once"},
                ],
            },
        )
    )
    assert out["outcome"] == {"outcome": "selected", "optionId": "yes"}
    assert recorded == [("Bash", "selected", "allow_once")]


def test_permission_prefers_allow_once_over_a_standing_grant(tmp_path: Path):
    """US-83.4: `allow_always` is a rule the agent persists under GROK_HOME —
    it would outlive this run and pre-approve the next one's asks on a shared
    slot. `allow_once` whenever it is offered, whatever the order."""
    h = ClientHandlers(FakePrim(), str(tmp_path))
    out = asyncio.run(
        h(
            "session/request_permission",
            {
                "toolCall": {"title": "Bash"},
                "options": [
                    {"optionId": "forever", "kind": "allow_always"},
                    {"optionId": "just-this-one", "kind": "allow_once"},
                ],
            },
        )
    )
    assert out["outcome"] == {"outcome": "selected", "optionId": "just-this-one"}

    # an agent that ONLY offers always still gets a grant — refusing would
    # stall the run for a distinction the audit plane already covers
    out = asyncio.run(
        h(
            "session/request_permission",
            {
                "toolCall": {"title": "Bash"},
                "options": [{"optionId": "forever", "kind": "allow_always"}],
            },
        )
    )
    assert out["outcome"] == {"outcome": "selected", "optionId": "forever"}


def test_permission_with_no_allow_option_cancels_rather_than_hanging(tmp_path: Path):
    """An unattended run must never block waiting for a human."""
    h = ClientHandlers(FakePrim(), str(tmp_path))
    out = asyncio.run(
        h(
            "session/request_permission",
            {"toolCall": {}, "options": [{"optionId": "no", "kind": "reject_once"}]},
        )
    )
    assert out["outcome"] == {"outcome": "cancelled"}


def test_an_undeclared_method_raises_not_implemented(tmp_path: Path):
    h = ClientHandlers(FakePrim(), str(tmp_path))
    with pytest.raises(NotImplementedError):
        asyncio.run(h("session/set_mode", {}))
