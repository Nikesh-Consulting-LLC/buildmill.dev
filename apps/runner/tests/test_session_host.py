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


# ---------------------------------------------------------------------------
# us-116.3: a session opens through the run's own door.
#
# Every session from 2026-08-14 to 08-17 died on the machine after the API had
# said yes: `_open` passed a `token` name US-89.1 had removed. No test reached
# that line. These drive `_open` past checkout preparation with fakes for the
# checkout and the ACP agent, and pin that a session and a run open the CLI
# through ONE routine — the same config, the same env, the same refusal.
# ---------------------------------------------------------------------------


def _drive_open(monkeypatch, tmp_path, *, agent=None, model_env=None, env=None):
    """Run `session.open` against a scripted ACP agent; return (conn, prim, agent)."""
    from test_interactive_module import FakePrimitives, ScriptedAgent

    from supervisor import gitwork, mcpconfig, session_host as sh
    from supervisor.modules import interactive

    agent = agent or ScriptedAgent(http_mcp=True)
    prims: list = []

    class Prims(FakePrimitives):
        pass

    def make_prims(env=None):
        p = Prims(agent)
        p.env = dict(env or {})
        prims.append(p)
        return p

    async def fake_checkout(prim, remote, name, project_id=None):
        return tmp_path / "workspace"

    async def fake_probe(url, headers, timeout=30):
        return ["get_work_context", "submit_changeset"]

    monkeypatch.setattr(sh, "LocalPrimitives", make_prims)
    monkeypatch.setattr(gitwork, "prepare_checkout", fake_checkout)
    monkeypatch.setattr(mcpconfig, "probe", fake_probe)
    monkeypatch.setattr(interactive, "STOPPED_RUNS", set())
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "grok-home"))
    for key, value in {
        "FACTORY_API_URL": "https://api.example",
        "FACTORY_WORKER_TOKEN": "sfw_session_token_1163",
        **(env or {}),
    }.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)

    conn = FakeConnection()
    params = {
        "session_id": "s-open-1",
        "project_id": "proj-1",
        "git_remote_url": "https://git.example/org/repo.git",
        "model_env": model_env
        if model_env is not None
        else {"GROK_MODELS_BASE_URL": "https://g/v1", "GROK_MODEL": "grok-4.5"},
    }

    async def scenario():
        try:
            await sh.handle(conn, {"method": "session.open", "id": 11, "params": params})
        finally:
            await sh.close_all()

    asyncio.run(scenario())
    return conn, (prims[0] if prims else None), agent


def test_a_session_opens_with_the_token_from_the_process_env(monkeypatch, tmp_path):
    """The `NameError` this story fixes: `_open` named a `token` that did not
    exist. Against the pre-fix file this test fails with exactly that error in
    the reply. The token comes from FACTORY_WORKER_TOKEN and nowhere else — it
    is what the credential env the child receives is built from."""
    conn, prim, agent = _drive_open(monkeypatch, tmp_path)
    _, result, _ = conn.replies[0]
    assert result["ok"] is True, result
    assert result["acp_session_id"] == "sess-new"
    # The child carries the credential its config names — the worker token,
    # read from the env at open time.
    env = prim.session_env[0]
    assert env["FACTORY_MCP_KEY"] == "sfw_session_token_1163"


def test_a_session_and_a_run_open_the_cli_the_same_way(monkeypatch, tmp_path):
    """us-115.1's shape, on the SESSION path too: the servers in the CLI's own
    config, none on `session/new`, `MCP_INIT_STRATEGY=blocking` in the child's
    env, and no token-bearing file in the checkout."""
    conn, prim, agent = _drive_open(monkeypatch, tmp_path)
    assert conn.replies[0][1]["ok"] is True
    config = (tmp_path / "grok-home" / "config.toml").read_text(encoding="utf-8")
    assert '[mcp_servers."factory"]' in config
    assert '[model."grok-4.5"]' in config
    assert "auto_update = false" in config
    assert "sfw_session_token_1163" not in config
    assert agent.session_new_params["mcpServers"] == []
    assert prim.session_env[0]["MCP_INIT_STRATEGY"] == "blocking"
    # No `.factory-mcp.json` anywhere near the workspace: a session has no
    # `execute()` to remove it afterwards.
    assert not list(tmp_path.rglob(".factory-mcp.json"))


def test_both_owners_go_through_one_door(monkeypatch, tmp_path):
    """The seam this story exists for: `_run_cli` and `session_host._open`
    both call `open_agent_cli`. A third copy of the config-and-spawn sequence
    is how the last two regressions happened."""
    import inspect

    from supervisor import session_host as sh
    from supervisor.modules import interactive

    assert "open_agent_cli(" in inspect.getsource(sh._open)
    assert "open_agent_cli(" in inspect.getsource(interactive.InteractiveModule._run_cli)
    for src in (inspect.getsource(sh), inspect.getsource(interactive.InteractiveModule._run_cli)):
        assert "open_session(" not in src, "spawn belongs to open_agent_cli only"
        assert "write_model_config(" not in src, "the config write belongs to open_agent_cli only"


def test_a_session_with_no_model_is_refused_with_the_runs_own_sentence(
    monkeypatch, tmp_path
):
    """One refusal string for both owners (AC4)."""
    from supervisor.modules.interactive import NO_MODEL_REFUSAL

    conn, prim, agent = _drive_open(
        monkeypatch, tmp_path, model_env={"GROK_MODELS_BASE_URL": "https://g/v1"}
    )
    _, result, _ = conn.replies[0]
    assert result["ok"] is False
    assert result["error"] == NO_MODEL_REFUSAL[:500]
    assert not any(m.get("method") == "session/new" for m in agent.sent)
