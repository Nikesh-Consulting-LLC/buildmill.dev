"""US-57.9: a probe that succeeds puts a recovered pool back in service.

`run_job` skips the host-status update for probes, so a flaky probe can never
knock a working host into `error`. That protection had no counterpart: nothing
that runs on a schedule could leave `error` either, so one failed `add_slot`
stranded Pod-001 with 31 of 32 slots free and invisible to every tenant,
through four consecutive successful probes.

The promotion is deliberately one-directional and narrow, which is what these
tests pin: a successful probe may lift `error`/`degraded` to `ready`, and does
nothing else to any other status; a failed probe still only writes
`probe_error`.
"""

from __future__ import annotations

import asyncio

import pytest

from app import agent_provision
from app.config import Settings


@pytest.fixture()
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url="postgresql://test",
    )


HOST = {"id": "host-1", "workdir": "/opt/buildmill", "status": "error"}

PROBE_RESULT = {
    "os_release": "Ubuntu 24.04",
    "cpu_count": 8,
    "mem_total_mb": 32000,
    "mem_free_mb": 21000,
    "disk_total_gb": 200.0,
    "disk_free_gb": 150.0,
    "load_avg": 0.4,
    "bundle_hash": "sha-abc",
    "workspace_bytes": 0,
    "workspace_count": 0,
    "services": {},
}


class _Rig:
    def __init__(self):
        self.host_updates: list[dict] = []
        self.cleared: list[str] = []
        self.slot_updates: list[tuple] = []
        self.logs: list[str] = []

    def install(self, monkeypatch, *, probe_raises=None, cleared=True):
        monkeypatch.setattr(agent_provision, "get_slots", lambda s, h: [])
        monkeypatch.setattr(
            agent_provision,
            "update_host",
            lambda s, host_id, fields: self.host_updates.append(fields),
        )
        monkeypatch.setattr(
            agent_provision,
            "update_slot",
            lambda s, slot_id, fields: self.slot_updates.append((slot_id, fields)),
        )

        def fake_clear(s, host_id):
            self.cleared.append(host_id)
            return cleared

        monkeypatch.setattr(agent_provision, "clear_host_error", fake_clear)

        def fake_probe(transport, workdir, indexes):
            if probe_raises:
                raise probe_raises
            return dict(PROBE_RESULT)

        monkeypatch.setattr(agent_provision, "probe_host", fake_probe)
        # US-27.9 (pre-existing, unrelated to this story): _probe_into_row
        # always checks for revoked worker tokens after the status update —
        # stub it so this test stays about the recovery path it's named for.
        monkeypatch.setattr(
            agent_provision, "revoked_slot_workers", lambda s, h: []
        )
        return self

    def step(self):
        return agent_provision.StepCtx(
            transport=object(),
            host=dict(HOST),
            password=None,
            log=self.logs.append,
            mask=lambda s: s,
        )


def test_a_successful_probe_clears_a_stale_error(settings, monkeypatch):
    rig = _Rig().install(monkeypatch)

    asyncio.run(agent_provision._probe_into_row(settings, rig.step()))

    assert rig.cleared == ["host-1"]
    # and it says so in the job log, so the recovery is not silent
    assert any("cleared back to ready" in line for line in rig.logs)


def test_the_probe_row_write_still_clears_probe_error(settings, monkeypatch):
    """The promotion is additive — the existing write is untouched."""
    rig = _Rig().install(monkeypatch)

    asyncio.run(agent_provision._probe_into_row(settings, rig.step()))

    fields = rig.host_updates[0]
    assert fields["probe_error"] is None
    assert fields["bundle_hash"] == "sha-abc"
    assert fields["cpu_count"] == 8
    # the probe must never write `status` directly — the guarded UPDATE owns it
    assert "status" not in fields


def test_a_probe_that_finds_nothing_to_clear_stays_quiet(settings, monkeypatch):
    """A host that was already `ready` is the common case; it must not put a
    misleading recovery line in every probe's log."""
    rig = _Rig().install(monkeypatch, cleared=False)

    asyncio.run(agent_provision._probe_into_row(settings, rig.step()))

    assert rig.cleared == ["host-1"]
    assert not any("cleared back to ready" in line for line in rig.logs)


def test_a_failed_probe_records_the_reason_and_promotes_nothing(
    settings, monkeypatch
):
    """The half that was always right: a probe failure writes `probe_error`
    and leaves the status alone. A broken probe must not heal a host, and
    must not break one either."""
    err = agent_provision.JobError("probe", "ssh: connection refused")
    rig = _Rig().install(monkeypatch, probe_raises=err)

    asyncio.run(agent_provision._probe_into_row(settings, rig.step()))

    assert rig.cleared == []  # no promotion
    assert len(rig.host_updates) == 1
    fields = rig.host_updates[0]
    assert fields["probe_error"] == "ssh: connection refused"
    assert "status" not in fields


def test_the_promotion_is_a_single_guarded_update(settings, monkeypatch):
    """`clear_host_error` must decide in SQL, not read-then-write: a host at
    `provisioning` has a job in flight whose own tail sets the status, and a
    `removed` host must never be resurrected by a stray probe."""
    executed: list[tuple] = []

    class _Conn:
        def execute(self, sql, params):
            executed.append((" ".join(sql.split()), params))
            return self

        def fetchone(self):
            return {"id": "host-1"}

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(agent_provision, "_connect", lambda s: _Conn())

    assert agent_provision.clear_host_error(settings, "host-1") is True

    assert len(executed) == 1, "the promotion must be one statement"
    sql, params = executed[0]
    assert "set status = 'ready'" in sql
    assert "status in ('error', 'degraded')" in sql
    assert params == ("host-1",)
    for never in ("removed", "provisioning", "new"):
        assert never not in sql
