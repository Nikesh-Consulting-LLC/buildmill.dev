"""US-78.2: `run_session` — the audited spawn that keeps stdin open.

Run against a real child process, because the things that break a JSON-RPC
stream are all about how bytes actually arrive: a frame split across two
writes, two frames in one write, and the agent's log landing on stderr where it
cannot corrupt the protocol.
"""

import asyncio
import sys

import pytest

from supervisor.primitives import LocalPrimitives


def _child(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def test_two_frames_in_one_write_are_two_lines():
    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(
            _child(
                "import sys;"
                "sys.stdout.write('{\"a\":1}\\n{\"b\":2}\\n');"
                "sys.stdout.flush()"
            )
        )
        assert await asyncio.wait_for(proc.next_line(), 10) == '{"a":1}'
        assert await asyncio.wait_for(proc.next_line(), 10) == '{"b":2}'
        await proc.close()

    asyncio.run(scenario())


def test_a_frame_split_across_writes_is_reassembled():
    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(
            _child(
                "import sys,time;"
                "sys.stdout.write('{\"c\":'); sys.stdout.flush(); time.sleep(0.2);"
                "sys.stdout.write('3}\\n'); sys.stdout.flush()"
            )
        )
        assert await asyncio.wait_for(proc.next_line(), 10) == '{"c":3}'
        await proc.close()

    asyncio.run(scenario())


def test_stdin_stays_open_and_the_child_can_be_spoken_to():
    """The whole point of the primitive. `run_shell` gives its child no stdin,
    which is why nothing in this runner could answer a subprocess before."""

    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(
            _child(
                "import sys\n"
                "for line in sys.stdin:\n"
                "    sys.stdout.write('echo:' + line.strip() + '\\n')\n"
                "    sys.stdout.flush()\n"
            )
        )
        await proc.send('{"hello":1}')
        assert await asyncio.wait_for(proc.next_line(), 10) == 'echo:{"hello":1}'
        await proc.send('{"hello":2}')
        assert await asyncio.wait_for(proc.next_line(), 10) == 'echo:{"hello":2}'
        await proc.close()

    asyncio.run(scenario())


def test_stderr_never_lands_in_the_protocol_stream():
    """`run_shell` merges stderr into stdout because for a one-shot CLI the
    merged text IS the output. Here it would interleave the agent's log into
    its own frames and corrupt every one it landed in."""

    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(
            _child(
                "import sys;"
                "sys.stderr.write('warning: update available\\n'); sys.stderr.flush();"
                "sys.stdout.write('{\"ok\":true}\\n'); sys.stdout.flush()"
            )
        )
        assert await asyncio.wait_for(proc.next_line(), 10) == '{"ok":true}'
        assert await asyncio.wait_for(proc.next_line(), 10) is None
        await proc.close()
        assert "update available" in proc.stderr_tail()

    asyncio.run(scenario())


def test_eof_is_reported_once_and_stays_reported():
    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(_child("pass"))
        # drain whatever there is (nothing), then EOF, repeatedly
        assert await asyncio.wait_for(proc.next_line(), 10) is None
        assert await asyncio.wait_for(proc.next_line(), 10) is None
        await proc.close()

    asyncio.run(scenario())


def test_a_policy_refusal_raises_rather_than_returning_a_dead_session():
    """`run_shell` can return a denied ShellResult because there is still a
    result. There is no session to hand back, and a caller that missed the
    difference would talk to a process that does not exist."""

    async def scenario():
        async def deny(argv, cwd):
            return False

        prim = LocalPrimitives(audit=deny)
        with pytest.raises(PermissionError):
            await prim.run_session(_child("pass"))

    asyncio.run(scenario())


def test_close_returns_an_exit_code_and_is_safe_twice():
    async def scenario():
        prim = LocalPrimitives()
        proc = await prim.run_session(
            _child("import sys,time\nfor line in sys.stdin:\n    pass\n")
        )
        code = await proc.close()
        assert isinstance(code, int)
        assert isinstance(await proc.close(), int)

    asyncio.run(scenario())
