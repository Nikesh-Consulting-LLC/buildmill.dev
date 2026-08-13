"""US-68.3: the auto-repair service — a slot the health probe found failed or
inactive gets picked up by `slots_due_for_repair` and worked through an
escalating ladder by `auto_repair_sweep`.

Runs against DATABASE_URL (apps/api/.env). Skips if unreachable. `create_job`
and `launch` are monkeypatched so a test never opens a real SSH connection or
spawns a real background job — only the sweep's own decision-making (which
action, whose turn, when to give up) is under test here.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app import agent_provision as ap
from app.config import Settings


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="module")
def db():
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        conn.execute("select 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def settings():
    return Settings(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        cors_origins="http://localhost:3000",
        database_url=_database_url(),
    )


@pytest.fixture
def ctx(db):
    db.rollback()
    org = db.execute("select id from public.organizations limit 1").fetchone()
    if not org:
        pytest.skip("no organization")
    return {"org_id": org["id"]}


def _make_host(db, ctx, *, auto_repair_enabled=True, host_status="ready"):
    server_id = db.execute(
        "insert into public.servers (org_id, name, host, username, auth_method)"
        " values (%s, %s, '198.51.100.9', 'ops', 'password') returning id",
        (ctx["org_id"], f"auto-repair-test-{uuid.uuid4().hex[:8]}"),
    ).fetchone()["id"]
    host_id = db.execute(
        "insert into public.agent_servers"
        " (org_id, server_id, workdir, status, auto_repair_enabled)"
        " values (%s, %s, '/opt/buildmill', %s, %s) returning id",
        (ctx["org_id"], server_id, host_status, auto_repair_enabled),
    ).fetchone()["id"]
    db.commit()
    return host_id, server_id


def _make_slot(
    db,
    ctx,
    host_id,
    *,
    slot_index=1,
    service_state="failed",
    desired_state="enabled",
    slot_status="active",
    attempts=0,
    last_at=None,
    needs_attention=False,
):
    slot_id = db.execute(
        "insert into public.agent_slots"
        " (org_id, agent_server_id, slot_index, name, service_name, workspace_path,"
        "  desired_state, service_state, status, auto_repair_attempts,"
        "  auto_repair_last_at, auto_repair_needs_attention)"
        " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id",
        (
            ctx["org_id"],
            host_id,
            slot_index,
            f"auto-repair-slot-{slot_index}",
            f"buildmill-agent@{slot_index}",
            f"/opt/buildmill/agents/{slot_index}/workspace",
            desired_state,
            service_state,
            slot_status,
            attempts,
            last_at,
            needs_attention,
        ),
    ).fetchone()["id"]
    db.commit()
    return slot_id


def _cleanup(db, host_id, server_id):
    db.rollback()
    db.execute("delete from public.agent_server_jobs where agent_server_id = %s", (host_id,))
    db.execute("delete from public.agent_slots where agent_server_id = %s", (host_id,))
    db.execute("delete from public.agent_servers where id = %s", (host_id,))
    db.execute("delete from public.servers where id = %s", (server_id,))
    db.commit()


def test_slots_due_for_repair_finds_a_failed_slot(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id)
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_slots_due_for_repair_ignores_a_healthy_slot(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id, service_state="active")
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) not in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_slots_due_for_repair_respects_the_disabled_toggle(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx, auto_repair_enabled=False)
    try:
        slot_id = _make_slot(db, ctx, host_id)
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) not in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_slots_due_for_repair_respects_the_cooldown(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id)
        db.execute(
            "update public.agent_slots set auto_repair_last_at = now() where id = %s",
            (slot_id,),
        )
        db.commit()
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) not in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_slots_due_for_repair_ignores_a_slot_needing_attention(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id, needs_attention=True)
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) not in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_slots_due_for_repair_skips_a_host_with_a_job_in_flight(db, settings, ctx):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id)
        db.execute(
            "insert into public.agent_server_jobs"
            " (org_id, agent_server_id, kind, status, started_at)"
            " values (%s, %s, 'probe', 'running', now())",
            (ctx["org_id"], host_id),
        )
        db.commit()
        due = ap.slots_due_for_repair(settings)
        assert str(slot_id) not in {str(r["id"]) for r in due}
    finally:
        _cleanup(db, host_id, server_id)


def test_auto_repair_sweep_tries_restart_first(db, settings, ctx, monkeypatch):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id, attempts=0)
        created: list[dict] = []
        launched: list[dict] = []
        monkeypatch.setattr(
            ap,
            "create_job",
            lambda settings, **kw: (created.append(kw) or {"id": uuid.uuid4()}),
        )
        monkeypatch.setattr(ap, "launch", lambda settings, ctx: launched.append(ctx))

        count = asyncio.run(ap.auto_repair_sweep(settings))
        assert count == 1
        assert created[0]["kind"] == "restart"
        assert created[0]["slot_id"] == str(slot_id)
        assert launched[0]["kind"] == "restart"

        db.rollback()
        slot = db.execute(
            "select auto_repair_attempts, auto_repair_last_at"
            " from public.agent_slots where id = %s",
            (slot_id,),
        ).fetchone()
        assert slot["auto_repair_attempts"] == 1
        assert slot["auto_repair_last_at"] is not None
    finally:
        _cleanup(db, host_id, server_id)


def test_auto_repair_sweep_escalates_to_reissue_then_update(db, settings, ctx, monkeypatch):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id, attempts=1)
        created: list[dict] = []
        monkeypatch.setattr(
            ap,
            "create_job",
            lambda settings, **kw: (created.append(kw) or {"id": uuid.uuid4()}),
        )
        monkeypatch.setattr(ap, "launch", lambda settings, ctx: None)
        asyncio.run(ap.auto_repair_sweep(settings))
        assert created[0]["kind"] == "reissue_token"

        db.rollback()
        db.execute(
            "update public.agent_slots set auto_repair_attempts = 2,"
            " auto_repair_last_at = null where id = %s",
            (slot_id,),
        )
        db.commit()
        created.clear()
        asyncio.run(ap.auto_repair_sweep(settings))
        assert created[0]["kind"] == "update"
        assert created[0]["slot_id"] is None  # host-wide, not slot-scoped
    finally:
        _cleanup(db, host_id, server_id)


def test_auto_repair_sweep_gives_up_after_the_ladder_is_exhausted(db, settings, ctx, monkeypatch):
    host_id, server_id = _make_host(db, ctx)
    try:
        slot_id = _make_slot(db, ctx, host_id, attempts=3, last_at=None)
        created: list[dict] = []
        monkeypatch.setattr(
            ap,
            "create_job",
            lambda settings, **kw: (created.append(kw) or {"id": uuid.uuid4()}),
        )
        monkeypatch.setattr(ap, "launch", lambda settings, ctx: None)
        asyncio.run(ap.auto_repair_sweep(settings))
        assert created == []  # no job created — the ladder is exhausted

        db.rollback()
        slot = db.execute(
            "select auto_repair_needs_attention from public.agent_slots where id = %s",
            (slot_id,),
        ).fetchone()
        assert slot["auto_repair_needs_attention"] is True
    finally:
        _cleanup(db, host_id, server_id)
