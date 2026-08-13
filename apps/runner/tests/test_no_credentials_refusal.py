"""US-36.1: an agent with no credentials must not be started.

On 2026-07-27 a control socket died on an unrelated fault, `gateway.mint` went
with it, and the supervisor ran Claude Code anyway with no API key. The CLI does
not report "I have no key" — it reports `Not logged in · Please run /login`,
which reads as a broken machine. Five runs failed in four seconds each, all
naming the wrong cause, and the investigation went to the agent box where
nothing was wrong.

These tests pin the contract: refuse, say why, and say the agent never started.
"""

from __future__ import annotations

import asyncio

from supervisor.workloop import Supervisor


class FakeResponse:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class FakeClient:
    def __init__(self, bundle):
        self.bundle = bundle
        self.submits: list[dict] = []
        self.releases: list[tuple] = []
        self.beats = 0

    async def get_context(self, run_id):
        return self.bundle

    async def submit(self, run_id, payload):
        self.submits.append(payload)
        return FakeResponse(200)

    async def release(self, run_id, note=""):
        self.releases.append((run_id, note))
        return FakeResponse(200)

    async def heartbeat(self, run_id):
        self.beats += 1


class FakeConnection:
    def __init__(self):
        self.notifications: list[tuple] = []

    async def notify(self, method, params):
        self.notifications.append((method, params))


def _bundle(kind="code"):
    return {
        "run_id": "r1",
        "kind": kind,
        "context": {"title": "Widget"},
        "branch_name": "factory/widget",
        "git_remote_url": "https://f/git/o/p.git",
        "default_branch": "main",
    }


def _dead_socket(*_a, **_kw):
    raise RuntimeError("control socket is not connected")


def _run(kind="code", env_provider=_dead_socket, modules=("sim",)):
    client = FakeClient(_bundle(kind))
    conn = FakeConnection()

    async def provider(run_id, run_kind, module, resolved=None):
        return env_provider(run_id, run_kind, module, resolved)

    sup = Supervisor(
        client,
        config_provider=lambda: {"enabled_modules": list(modules)},
        env_provider=provider,
        connection=conn,
    )
    result = asyncio.run(sup.run_claimed("r1"))
    return client, conn, result


# --------------------------------------------------------------- the refusal


def test_module_needing_credentials_is_not_started(monkeypatch):
    """The whole point: the CLI must never be invoked without a key."""
    from supervisor import modules

    started: list[str] = []
    sim = modules.get("sim")

    async def spy(ctx, prim):  # pragma: no cover - must not run
        started.append(ctx.run_id)
        raise AssertionError("the module was started without credentials")

    monkeypatch.setattr(sim, "execute", spy)
    # `sim` declares no provider_type, so give it one for this test: the
    # refusal keys on "does this module need a gateway", not on its name.
    monkeypatch.setattr(sim, "provider_type", "anthropic", raising=False)

    client, _conn, result = _run()

    assert started == []
    assert result.outcome == "failed"
    assert len(client.submits) == 1


def test_the_reported_reason_names_the_real_cause(monkeypatch):
    """The manager must not be told 'Not logged in'."""
    from supervisor import modules

    monkeypatch.setattr(modules.get("sim"), "provider_type", "anthropic", raising=False)
    client, _conn, _result = _run()

    error = client.submits[0]["error"]
    assert "could not obtain model credentials" in error
    assert "control socket is not connected" in error  # the underlying cause
    assert "NOT started" in error
    # and it must steer away from the machine, which is where this sent people
    assert "nothing there needs fixing" in error
    # never the CLI's phrase — even quoted to explain it, a skimming reader
    # sees those words and goes to the machine.
    assert "Not logged in" not in error
    assert "/login" not in error


def test_refusal_is_a_runner_fault_and_raises_an_incident(monkeypatch):
    from supervisor import modules

    monkeypatch.setattr(modules.get("sim"), "provider_type", "anthropic", raising=False)
    client, conn, _result = _run()

    assert client.submits[0]["fault_class"] == "runner-fault"
    incidents = [p for m, p in conn.notifications if m == "runner.incident"]
    assert incidents and "credentials" in incidents[0]["message"]


def test_a_module_needing_no_gateway_still_runs(monkeypatch):
    """`sim` has no provider_type and legitimately needs no key — refusing it
    would break the harness that proves the pipeline without a model."""
    from supervisor import modules

    sim = modules.get("sim")
    monkeypatch.setattr(sim, "provider_type", "", raising=False)

    client, _conn, result = _run()

    assert result.outcome == "succeeded"
    assert "error" not in client.submits[0]


# ------------------------------------------------------- the trace kind fix


def test_traces_are_sent_with_a_kind_the_server_permits(monkeypatch):
    """The trigger for the whole incident: no kind was sent, the server
    defaulted to `note`, and the constraint rejected every single one."""
    from supervisor import modules

    monkeypatch.setattr(modules.get("sim"), "provider_type", "anthropic", raising=False)
    _client, conn, _result = _run()

    traces = [p for m, p in conn.notifications if m == "run.trace"]
    assert traces, "the refusal should be traced"
    for params in traces:
        assert params.get("kind") == "progress"
