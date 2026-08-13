"""US-78.10: a session with no work item, on the runner side.

The session host reuses the run path's client, handlers and LIVE registry, so
these cover the parts that are genuinely new: the open/close lifecycle, the
registration that makes a session typeable-into, and the guard that stops the
work loop claiming a run into a workspace a conversation is already using.
"""

import asyncio

import pytest

from supervisor import session_host
from supervisor.modules.interactive import LIVE


class FakeConnection:
    def __init__(self):
        self.replies: list[tuple] = []
        self.notes: list[tuple] = []

    async def reply(self, req_id, result=None, error=None):
        self.replies.append((req_id, result, error))

    async def notify(self, method, params=None):
        self.notes.append((method, params))


def test_it_ignores_methods_that_are_not_its_own():
    """Handlers stack on one `on_message`; each must pass the others through."""

    async def scenario():
        conn = FakeConnection()
        await session_host.handle(conn, {"method": "workspace.prepare", "id": 1})
        await session_host.handle(conn, {"method": "session.input", "id": 2})
        assert conn.replies == []

    asyncio.run(scenario())


def test_opening_without_a_remote_is_refused_with_a_reason():
    async def scenario():
        conn = FakeConnection()
        await session_host.handle(
            conn, {"method": "session.open", "id": 7, "params": {"session_id": "s1"}}
        )
        assert len(conn.replies) == 1
        req_id, result, _ = conn.replies[0]
        assert req_id == 7
        assert result["ok"] is False
        assert "git_remote_url" in result["error"]

    asyncio.run(scenario())


def test_closing_a_session_this_machine_does_not_hold_succeeds():
    """Already gone is the success case: the server closing a session this
    machine no longer holds is exactly what a restart looks like, and refusing
    would leave the row open forever."""

    async def scenario():
        conn = FakeConnection()
        await session_host.handle(
            conn,
            {"method": "session.close", "id": 3, "params": {"session_id": "nope"}},
        )
        _, result, _ = conn.replies[0]
        assert result["ok"] is True
        assert result["was_open"] is False
        assert result["workspace"] == "preserved"

    asyncio.run(scenario())


def test_a_held_session_makes_the_agent_busy_and_close_releases_it():
    """US-78.10 AC3: the session holds the slot for its life."""

    class DeadHost:
        async def close(self):
            return None

    async def scenario():
        assert session_host.is_busy() is False
        session_host.HOSTS["s-1"] = DeadHost()
        LIVE["s-1"] = object()
        try:
            assert session_host.is_busy() is True
        finally:
            conn = FakeConnection()
            await session_host.handle(
                conn,
                {"method": "session.close", "id": 1, "params": {"session_id": "s-1"}},
            )
        assert session_host.is_busy() is False
        # and it is no longer typeable-into
        assert "s-1" not in LIVE

    asyncio.run(scenario())


def test_close_all_releases_every_held_session():
    """On shutdown, so a restart does not leave rows claiming to be open
    against a process that is gone."""

    class DeadHost:
        def __init__(self):
            self.closed = False

        async def close(self):
            self.closed = True

    async def scenario():
        hosts = {f"s-{i}": DeadHost() for i in range(3)}
        session_host.HOSTS.update(hosts)
        for key in hosts:
            LIVE[key] = object()
        await session_host.close_all()
        assert session_host.HOSTS == {}
        assert all(h.closed for h in hosts.values())
        assert not any(k in LIVE for k in hosts)

    asyncio.run(scenario())


def test_the_work_loop_refuses_to_claim_while_a_session_is_open():
    """Two conversations in one checkout, editing the same files with no idea
    about each other, is the failure this guard exists to prevent."""
    from supervisor import workloop

    class DeadHost:
        async def close(self):
            return None

    class Client:
        def __init__(self):
            self.polled = False

        async def list_pool(self):
            self.polled = True
            return {"runs": []}

    async def scenario():
        client = Client()
        sup = workloop.Supervisor(client, config_provider=lambda: {})
        session_host.HOSTS["busy"] = DeadHost()
        try:
            await sup.supervise(once=True)
        finally:
            session_host.HOSTS.pop("busy", None)
        assert client.polled is False, "a held agent must not even be offered work"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# US-83.3: a session is a lease, not a latch
# ---------------------------------------------------------------------------


class _FakeClient:
    def __init__(self, supports_close=False):
        self.supports_session_close = supports_close
        self.calls: list = []

    async def session_close(self, session_id, timeout=5):
        self.calls.append(("session/close", session_id))

    async def close(self):
        self.calls.append(("close", None))


class _FakeProc:
    def __init__(self, tail="segfault at 0x0"):
        self._tail = tail
        self.closed = False

    def stderr_tail(self):
        return self._tail

    async def close(self, timeout: float = 5):
        self.closed = True
        return 0


class _FakeOpened:
    def __init__(self, client, proc, session_id="acp-1"):
        self.client = client
        self.proc = proc
        self.session_id = session_id
        self.resumed = False


def test_a_dead_child_releases_the_agent_and_tells_the_server():
    """US-83.3 AC2: before the watchdog, a crashed session CLI left HOSTS
    populated, is_busy() true, and the agent holding no runs forever."""
    from supervisor.modules.interactive import LIVE as live_registry

    async def scenario():
        conn = FakeConnection()
        narrator = session_host._Narrator(conn, "s-dead")
        host = session_host._Host(
            "s-dead", _FakeOpened(_FakeClient(), _FakeProc()), "/w", narrator
        )
        session_host.HOSTS["s-dead"] = host
        live_registry["s-dead"] = object()

        await session_host._reap(conn, "s-dead")

        assert session_host.is_busy() is False
        assert "s-dead" not in live_registry
        failed = [(m, p) for m, p in conn.notes if m == "session.failed"]
        assert len(failed) == 1
        assert failed[0][1]["session_id"] == "s-dead"
        assert "segfault" in failed[0][1]["error"]

    asyncio.run(scenario())


def test_the_reaper_is_idempotent_against_a_close_that_won_the_race():
    async def scenario():
        conn = FakeConnection()
        await session_host._reap(conn, "already-gone")
        assert conn.notes == [], "nothing held, nothing reported"

    asyncio.run(scenario())


def test_close_asks_the_agent_first_when_it_declared_the_capability():
    """US-83.3 AC3, measured on grok 1.0.0: session/close answers
    {'x.ai/closeOutcome': 'closed'} — the agent flushes its own state before
    the process dies. An agent without the capability is simply killed."""

    async def scenario():
        conn = FakeConnection()
        graceful = _FakeClient(supports_close=True)
        host = session_host._Host(
            "s-g", _FakeOpened(graceful, _FakeProc(), session_id="acp-9"),
            "/w", session_host._Narrator(conn, "s-g"),
        )
        await host.close()
        assert graceful.calls[0] == ("session/close", "acp-9")
        assert ("close", None) in graceful.calls

        blunt = _FakeClient(supports_close=False)
        host2 = session_host._Host(
            "s-b", _FakeOpened(blunt, _FakeProc()), "/w",
            session_host._Narrator(conn, "s-b"),
        )
        await host2.close()
        assert blunt.calls == [("close", None)]

    asyncio.run(scenario())
