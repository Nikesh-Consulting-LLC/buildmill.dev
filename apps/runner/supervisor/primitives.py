"""Runner primitives (US-10.4): the whole surface a module has onto the machine
and network.

`run_shell` runs a command locally and is the single point the audit/policy plane
hooks into (enforcement + streaming to the server land in US-10.7 via the
optional `audit` callback); `run_api` is a plain HTTP call (the LLM gateway, a
module's own API). Modules receive a `Primitives` and touch nothing else.

US-78.2 adds `run_session`: the same audited spawn, but with stdin held open and
stderr kept SEPARATE, for a child that is spoken to rather than merely watched.
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from typing import Any, Awaitable, Callable

from .modules.base import ShellResult

# audit(argv, cwd) -> allow?  (US-10.7 wires this to the control socket)
AuditHook = Callable[[list[str], "str | None"], Awaitable[bool]]

# US-39.1: called with each complete output line WHILE the child runs. Sync and
# must never raise -- it is the least important thing in the system and cannot
# be allowed to affect the command it is watching.
LineHook = Callable[[str], None]

# How much we will hold waiting for a newline. Claude's stream-json events are
# already ~8-10KB for `system/init` and a `hook_response`, and a tool result
# carrying a file goes far higher -- which is exactly why this reads chunks and
# splits by hand instead of using StreamReader.readline(), whose 64KiB limit
# would raise rather than truncate.
MAX_PENDING_LINE = 16 * 1024 * 1024

# US-78.2: how much of a session child's stderr we keep. It is the agent's own
# log, not its protocol, and it only matters when something went wrong -- so the
# tail is what a diagnosis needs and the head is what would grow without bound.
MAX_SESSION_STDERR_LINES = 200


class SessionProcess:
    """A child held open and spoken to, rather than run to completion.

    Three differences from `run_shell`'s child, all of them load-bearing for a
    JSON-RPC peer:

      * **stdin stays open** -- the entire point; `run_shell` gives its child no
        stdin at all, which is why nothing in this runner could ever answer a
        subprocess before now.
      * **stderr is a separate pipe.** `run_shell` merges it into stdout because
        for a one-shot CLI the merged text *is* the output. Merging it here
        would interleave the agent's log lines into its protocol stream and
        corrupt every frame they landed in.
      * **lines are handed out as they arrive**, not accumulated and returned at
        exit, because a request whose reply is only readable after the process
        ends is not a request.
    """

    def __init__(self, proc: Any, argv: list[str]) -> None:
        self._proc = proc
        self.argv = argv
        self._lines: asyncio.Queue = asyncio.Queue()
        self._stderr: deque[str] = deque(maxlen=MAX_SESSION_STDERR_LINES)
        self._eof = False
        self._pumps = [
            asyncio.create_task(self._pump_stdout()),
            asyncio.create_task(self._pump_stderr()),
        ]

    async def _pump_stdout(self) -> None:
        pending = bytearray()
        try:
            assert self._proc.stdout is not None
            while True:
                block = await self._proc.stdout.read(65536)
                if not block:
                    break
                pending.extend(block)
                while True:
                    nl = pending.find(b"\n")
                    if nl == -1:
                        break
                    await self._lines.put(
                        bytes(pending[:nl]).decode("utf-8", "replace").rstrip("\r")
                    )
                    del pending[: nl + 1]
                if len(pending) > MAX_PENDING_LINE:
                    # Same rule as run_shell: no newline in 16MB is not a line.
                    pending.clear()
        except Exception:  # noqa: BLE001 -- EOF and pipe teardown are normal
            pass
        finally:
            if pending:
                await self._lines.put(
                    bytes(pending).decode("utf-8", "replace").rstrip("\r")
                )
            await self._lines.put(None)  # the one EOF marker readers wait on

    async def _pump_stderr(self) -> None:
        try:
            if self._proc.stderr is None:
                return
            while True:
                raw = await self._proc.stderr.readline()
                if not raw:
                    break
                self._stderr.append(raw.decode("utf-8", "replace").rstrip("\r\n"))
        except Exception:  # noqa: BLE001
            pass

    async def next_line(self) -> str | None:
        """The next complete line of the child's stdout, or None at EOF."""
        if self._eof:
            return None
        line = await self._lines.get()
        if line is None:
            self._eof = True
            return None
        return line

    async def send(self, text: str) -> None:
        """Write one newline-terminated frame to the child's stdin."""
        stdin = self._proc.stdin
        if stdin is None:
            raise RuntimeError("session process has no stdin")
        stdin.write((text + "\n").encode("utf-8"))
        await stdin.drain()

    def stderr_tail(self) -> str:
        """The agent's own log, for a failure that needs explaining."""
        return "\n".join(self._stderr)

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode

    async def close(self, timeout: float = 5) -> int:
        """Stop the child, politely then not. Returns its exit code."""
        if self._proc.returncode is None:
            try:
                if self._proc.stdin is not None:
                    self._proc.stdin.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._proc.terminate()
            except Exception:  # noqa: BLE001 -- already gone
                pass
            try:
                await asyncio.wait_for(self._proc.wait(), timeout)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                try:
                    self._proc.kill()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await self._proc.wait()
                except Exception:  # noqa: BLE001
                    pass
        for task in self._pumps:
            task.cancel()
        return self._proc.returncode or 0


class LocalPrimitives:
    """Default primitives: run commands locally, HTTP via httpx. `audit` is a
    no-op here; US-10.7 replaces it with a control-socket policy check. `env`
    is merged into the child environment (the injected gateway env)."""

    def __init__(
        self,
        audit: AuditHook | None = None,
        env: dict[str, str] | None = None,
        report: "Callable[[str, int, str], Awaitable[None]] | None" = None,
    ):
        self._audit = audit
        self._env = env or {}
        self._report = report

    @property
    def env(self) -> dict[str, str]:
        """The injected environment (the gateway env the supervisor minted).

        US-78.5: read by the interactive module, which has to write some of it
        into the CLI's own config file rather than only handing it to the
        child — a config-file CLI needs the values, not just the variables.
        """
        return dict(self._env)

    async def run_shell(
        self,
        argv: list[str],
        cwd: str | None = None,
        timeout: int | None = None,
        on_line: "LineHook | None" = None,
    ) -> ShellResult:
        argv = [str(a) for a in argv]
        audit_id: str | None = None
        if self._audit is not None:
            verdict = await self._audit(argv, cwd)
            # the hook may return a bare bool, or (allow, audit_id) for reporting
            if isinstance(verdict, tuple):
                allowed, audit_id = verdict
            else:
                allowed = bool(verdict)
            if not allowed:
                return ShellResult(
                    argv=argv, exit_code=126, stdout="[denied by policy]", allowed=False
                )
        # 2026-08-13: a spawn that fails (argv[0] not a real program, missing
        # cwd) used to raise PAST the report below, leaving the audit row open
        # forever — exit_code null, output empty — which is how two runs'
        # terminal failures stayed invisible for days. It now closes the audit
        # with exit 127 and the OS's own words, and answers a result the
        # caller can read instead of an exception it must guess about.
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env={**os.environ, **self._env},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as e:
            result = ShellResult(
                argv=argv,
                exit_code=127,
                stdout=f"[spawn failed: {e}]",
            )
            if self._report is not None and audit_id is not None:
                await self._report(audit_id, result.exit_code, result.stdout)
            return result

        # US-39.1: read incrementally instead of communicate(), which returns
        # only on exit and is why a run was silent for its whole duration. The
        # captured text is byte-identical to what communicate() would have
        # returned -- this changes WHEN the bytes are seen, not what they are,
        # so exit codes, the timeout kill and the repair classifier all behave
        # exactly as before.
        chunks: list[bytes] = []
        pending = bytearray()

        def emit(line: bytes) -> None:
            if on_line is None:
                return
            try:
                on_line(line.decode("utf-8", "replace").rstrip("\r"))
            except Exception:  # noqa: BLE001 -- watching must not break running
                pass

        async def pump() -> None:
            assert proc.stdout is not None
            while True:
                block = await proc.stdout.read(65536)
                if not block:
                    break
                chunks.append(block)
                if on_line is None:
                    continue
                pending.extend(block)
                while True:
                    nl = pending.find(b"\n")
                    if nl == -1:
                        break
                    emit(bytes(pending[:nl]))
                    del pending[: nl + 1]
                if len(pending) > MAX_PENDING_LINE:
                    # No newline in 16MB is not a line. Stop holding it rather
                    # than growing without bound; the bytes are still captured.
                    pending.clear()
            await proc.wait()

        try:
            await asyncio.wait_for(pump(), timeout)
            if pending:
                emit(bytes(pending))
            result = ShellResult(
                argv=argv,
                exit_code=proc.returncode or 0,
                stdout=b"".join(chunks).decode("utf-8", "replace"),
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                rest, _ = await proc.communicate()
            except Exception:  # noqa: BLE001
                rest = b""
            result = ShellResult(
                argv=argv,
                exit_code=124,
                stdout=(b"".join(chunks) + (rest or b"")).decode("utf-8", "replace")
                + f"\n[killed after {timeout}s]",
            )
        if self._report is not None and audit_id is not None:
            await self._report(audit_id, result.exit_code, result.stdout)
        return result

    async def run_session(
        self,
        argv: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SessionProcess:
        """US-78.2: spawn a child to hold a conversation with.

        Audited exactly like `run_shell` -- a policy refusal raises rather than
        returning a denied `ShellResult`, because there is no session to hand
        back and a caller that ignored the difference would talk to a process
        that does not exist.
        """
        argv = [str(a) for a in argv]
        if self._audit is not None:
            verdict = await self._audit(argv, cwd)
            allowed = verdict[0] if isinstance(verdict, tuple) else bool(verdict)
            if not allowed:
                raise PermissionError(f"denied by policy: {' '.join(argv)}")
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**os.environ, **self._env, **(env or {})},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return SessionProcess(proc, argv)

    async def run_api(self, method: str, url: str, **kwargs: Any) -> Any:
        import httpx

        timeout = kwargs.pop("timeout", 60)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.request(method, url, **kwargs)
