"""The client half of ACP: what the agent may ask the runner to do (US-78.2).

An ACP agent does not touch the filesystem or spawn processes itself — it asks
its client to, which is the property that makes this worth having. Every one of
those asks lands here, so the runner's existing policy and audit plane covers an
interactive agent exactly as it covers a headless one.

Two rails are enforced here rather than trusted to the agent:

  * **file access is confined to the run's own directories.** The machine also
    holds `env/<N>.env` — the slot's `FACTORY_WORKER_TOKEN`, the one secret on
    the box. An agent that could read an arbitrary absolute path could read
    that, and `fs/read_text_file` would be the exfiltration route. So a path
    outside the session's roots is refused, by this code, before any IO.
  * **shell work goes through `run_shell`**, never `create_subprocess` directly,
    so `terminal/create` is audited, policy-checked and reported like every
    other command this runner has ever run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Awaitable, Callable

from .client import AcpError

logger = logging.getLogger("supervisor.acp.handlers")

# ACP says absolute paths and 1-based line numbers, both directions.
MAX_TERMINAL_BYTES = 1 * 1024 * 1024


def _resolve_within(path: str, roots: list[Path]) -> Path:
    """The path, proven to be inside one of `roots`.

    `Path.resolve()` first so `..` and symlinks cannot walk out — comparing the
    unresolved string would accept `<workspace>/../../env/3.env`.
    """
    if not path:
        raise AcpError("no path given")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise AcpError(f"path must be absolute: {path}")
    resolved = candidate.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise AcpError(f"path is outside this session's directories: {path}")


class TerminalRegistry:
    """`terminal/*`, backed by the audited `run_shell`.

    Output accumulates while the command runs (via `run_shell`'s own line hook)
    so `terminal/output` can answer before exit — an agent that had to wait for
    the process to end to see anything could not tail a build.
    """

    def __init__(self, prim: Any, cwd: str):
        self._prim = prim
        self._cwd = cwd
        self._terms: dict[str, dict] = {}
        self._counter = 0

    async def create(self, params: dict) -> dict:
        command = params.get("command")
        if not command:
            raise AcpError("terminal/create needs a command")
        args = [str(a) for a in (params.get("args") or [])]
        command = str(command)
        # 2026-08-13 (FEAT-2.8 postmortem): the grok CLI sends its whole shell
        # line as ONE `command` string with no args — `/usr/bin/bash -lc '…'`.
        # Treating that as argv[0] asked the OS to exec a binary literally
        # named "/usr/bin/bash -lc '…'", so every terminal call died with
        # [Errno 2] and two runs' agents concluded "bash is missing" on a
        # machine whose bash was fine at every layer. A whitespace-bearing
        # command with no args is a command LINE: split it like a shell would,
        # so execution works and the audit/policy check reads real argv. A
        # line shlex cannot parse falls back to the shell itself.
        # posix=True unconditionally: ACP sessions exist only on the Linux
        # pools (interactive is pool-only, US-78.6/migration 236), and the
        # lines are POSIX shell regardless of where the tests run.
        if not args and any(c.isspace() for c in command):
            try:
                argv = shlex.split(command, posix=True)
            except ValueError:
                argv = ["/bin/sh", "-c", command]
        else:
            argv = [command, *args]
        env = {
            str(e.get("name")): str(e.get("value"))
            for e in (params.get("env") or [])
            if isinstance(e, dict) and e.get("name")
        }
        limit = params.get("outputByteLimit") or MAX_TERMINAL_BYTES
        self._counter += 1
        term_id = f"term-{self._counter}"
        state: dict[str, Any] = {
            "chunks": [],
            "bytes": 0,
            "truncated": False,
            "limit": int(limit),
            "exit": None,
        }

        def on_line(line: str) -> None:
            if state["bytes"] >= state["limit"]:
                state["truncated"] = True
                return
            state["chunks"].append(line)
            state["bytes"] += len(line) + 1

        async def run() -> None:
            try:
                res = await self._prim.run_shell(
                    argv,
                    cwd=str(params.get("cwd") or self._cwd),
                    on_line=on_line,
                )
                state["exit"] = {"exitCode": res.exit_code, "signal": None}
                if not res.allowed:
                    state["chunks"].append("[denied by policy]")
            except Exception as e:  # noqa: BLE001 — a dead terminal must report
                state["chunks"].append(f"[terminal failed: {e}]")
                state["exit"] = {"exitCode": 1, "signal": None}

        state["task"] = asyncio.ensure_future(run())
        state["env"] = env
        self._terms[term_id] = state
        return {"terminalId": term_id}

    def _get(self, params: dict) -> dict:
        term_id = str(params.get("terminalId") or "")
        state = self._terms.get(term_id)
        if state is None:
            raise AcpError(f"no such terminal: {term_id}")
        return state

    async def output(self, params: dict) -> dict:
        state = self._get(params)
        return {
            "output": "\n".join(state["chunks"]),
            "truncated": bool(state["truncated"]),
            "exitStatus": state["exit"],
        }

    async def wait_for_exit(self, params: dict) -> dict:
        state = self._get(params)
        try:
            await state["task"]
        except asyncio.CancelledError:
            pass
        return state["exit"] or {"exitCode": None, "signal": "killed"}

    async def kill(self, params: dict) -> dict:
        state = self._get(params)
        task = state["task"]
        if not task.done():
            task.cancel()
        state["exit"] = state["exit"] or {"exitCode": None, "signal": "killed"}
        return {}

    async def release(self, params: dict) -> dict:
        term_id = str(params.get("terminalId") or "")
        state = self._terms.pop(term_id, None)
        if state and not state["task"].done():
            state["task"].cancel()
        return {}


class ClientHandlers:
    """Routes one agent→client request to the thing that does it."""

    def __init__(
        self,
        prim: Any,
        cwd: str,
        *,
        roots: list[str] | None = None,
        on_permission: Callable[[dict, str, str], Awaitable[None]] | None = None,
    ):
        self.prim = prim
        self.cwd = cwd
        self.roots = [Path(r) for r in (roots or [cwd])]
        self.terminals = TerminalRegistry(prim, cwd)
        self._on_permission = on_permission

    async def __call__(self, method: str, params: dict) -> Any:
        handler = {
            "fs/read_text_file": self.read_text_file,
            "fs/write_text_file": self.write_text_file,
            "session/request_permission": self.request_permission,
            "terminal/create": self.terminals.create,
            "terminal/output": self.terminals.output,
            "terminal/wait_for_exit": self.terminals.wait_for_exit,
            "terminal/kill": self.terminals.kill,
            "terminal/release": self.terminals.release,
        }.get(method)
        if handler is None:
            raise NotImplementedError(method)
        return await handler(params)

    # -- fs ----------------------------------------------------------------

    async def read_text_file(self, params: dict) -> dict:
        path = _resolve_within(str(params.get("path") or ""), self.roots)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise AcpError(f"could not read {path}: {e}") from e
        line = params.get("line")
        limit = params.get("limit")
        if line is None and limit is None:
            return {"content": text}
        lines = text.splitlines(keepends=True)
        # ACP line numbers are 1-based.
        start = max(0, int(line) - 1) if line is not None else 0
        end = start + int(limit) if limit is not None else len(lines)
        return {"content": "".join(lines[start:end])}

    async def write_text_file(self, params: dict) -> dict:
        path = _resolve_within(str(params.get("path") or ""), self.roots)
        content = params.get("content")
        if content is None:
            raise AcpError("fs/write_text_file needs content")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(content), encoding="utf-8")
        except OSError as e:
            raise AcpError(f"could not write {path}: {e}") from e
        return {}

    # -- permission --------------------------------------------------------

    async def request_permission(self, params: dict) -> dict:
        """Answer from policy, so an unattended run never blocks on a human.

        US-78.8 puts a manager on the other end of this when one is attached;
        until then the rule is: grant, and record what was granted and why. A
        tool the agent runs is audited by `run_shell` anyway — this decides
        whether it gets to ask.

        US-83.4: `allow_once` over `allow_always`, always. An always-grant is
        a standing rule the agent persists in its own state under GROK_HOME —
        it would outlive this run and silently pre-approve the next one's
        asks, which is exactly the kind of leftover a shared slot must not
        accumulate.
        """
        options = params.get("options") or []
        allows = [
            o
            for o in options
            if isinstance(o, dict) and str(o.get("kind", "")).startswith("allow")
        ]
        chosen = next(
            (o for o in allows if o.get("kind") == "allow_once"),
            allows[0] if allows else None,
        )
        tool_call = params.get("toolCall") or {}
        if chosen is None:
            if self._on_permission is not None:
                await self._on_permission(tool_call, "cancelled", "no allow option offered")
            return {"outcome": {"outcome": "cancelled"}}
        if self._on_permission is not None:
            await self._on_permission(
                tool_call, "selected", str(chosen.get("kind") or "allow")
            )
        return {"outcome": {"outcome": "selected", "optionId": chosen.get("optionId")}}
