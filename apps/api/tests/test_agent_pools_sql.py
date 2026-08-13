"""US-57.1/US-57.4 RLS: a shared machine is the platform's, a slot is its
tenant's, and neither leaks to the other over PostgREST.

Live SQL coverage — runs against DATABASE_URL (apps/api/.env); skips if
unreachable. Every test runs inside a transaction rolled back at the end
(the fixture's own inserts included, same pattern as test_agent_servers_sql.py),
so nothing is left behind on what may be a live project.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row


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


def _act_as(db, user_id) -> None:
    db.execute("select set_config('role', 'authenticated', true)")
    db.execute(
        "select set_config('request.jwt.claims',"
        " json_build_object('sub', %s::text, 'role', 'authenticated')::text, true)",
        (str(user_id),),
    )


def _as_service_role(db) -> None:
    db.execute("select set_config('role', 'postgres', true)")


def _reset(db) -> None:
    db.rollback()


@pytest.fixture
def ctx(db):
    """A shared pool (platform-owned) with two tenant orgs' slots on it.

    Everything here is created inside the one open transaction the test's
    own `_reset` rolls back — the platform org and the two tenants are real
    rows found live, never created.
    """
    db.rollback()
    platform = db.execute(
        """
        select m.user_id, m.org_id
        from public.organization_members m
        join public.organizations o on o.id = m.org_id
        where o.is_platform_admin and m.user_id is not null
          and coalesce(m.status, 'active') = 'active'
        order by m.created_at limit 1
        """
    ).fetchone()
    if not platform:
        pytest.skip("no platform-admin org with a human owner")

    tenants = db.execute(
        """
        select distinct on (m.org_id) m.user_id, m.org_id
        from public.organization_members m
        join public.organizations o on o.id = m.org_id
        where not o.is_platform_admin and m.user_id is not null
          and coalesce(m.status, 'active') = 'active'
        order by m.org_id, m.created_at
        limit 2
        """
    ).fetchall()
    if len(tenants) < 2:
        pytest.skip("need two distinct tenant orgs with a human member")
    tenant_a, tenant_b = tenants[0], tenants[1]

    _as_service_role(db)
    server = db.execute(
        "insert into public.servers (org_id, name, host, username, auth_method)"
        " values (%s, %s, '198.51.100.9', 'ops', 'password') returning id",
        (platform["org_id"], f"pool-rls-test-{uuid.uuid4().hex[:8]}"),
    ).fetchone()
    host = db.execute(
        "insert into public.agent_servers"
        " (org_id, server_id, workdir, status, shared, pool_name, capacity)"
        " values (%s, %s, '/opt/buildmill', 'ready', true, %s, 4)"
        " returning id",
        (platform["org_id"], server["id"], f"pool-{uuid.uuid4().hex[:8]}"),
    ).fetchone()
    slot_a = db.execute(
        "insert into public.agent_slots"
        " (org_id, agent_server_id, slot_index, name, service_name, workspace_path)"
        " values (%s, %s, 1, 'tenant-a-1', 'buildmill-agent@1',"
        " '/opt/buildmill/agents/1/workspace') returning id",
        (tenant_a["org_id"], host["id"]),
    ).fetchone()
    slot_b = db.execute(
        "insert into public.agent_slots"
        " (org_id, agent_server_id, slot_index, name, service_name, workspace_path)"
        " values (%s, %s, 2, 'tenant-b-1', 'buildmill-agent@2',"
        " '/opt/buildmill/agents/2/workspace') returning id",
        (tenant_b["org_id"], host["id"]),
    ).fetchone()
    job = db.execute(
        "insert into public.agent_server_jobs"
        " (org_id, agent_server_id, kind, status, log)"
        " values (%s, %s, 'probe', 'succeeded', 'ssh output goes here')"
        " returning id",
        (platform["org_id"], host["id"]),
    ).fetchone()

    return {
        "platform_org": platform["org_id"],
        "host_id": host["id"],
        "tenant_a_org": tenant_a["org_id"],
        "tenant_a_user": tenant_a["user_id"],
        "tenant_b_org": tenant_b["org_id"],
        "tenant_b_user": tenant_b["user_id"],
        "slot_a": slot_a["id"],
        "slot_b": slot_b["id"],
        "job_id": job["id"],
    }


def test_available_agent_pools_returns_name_and_free_count_only(db, ctx):
    try:
        _act_as(db, ctx["tenant_a_user"])
        rows = db.execute(
            "select * from public.available_agent_pools() where pool_id = %s",
            (ctx["host_id"],),
        ).fetchall()
        assert len(rows) == 1
        # US-57.10: `status` joined the return columns — a tenant can tell
        # "no pool exists" from "no pool is ready" from "every ready pool is
        # full", instead of one empty list for all three.
        assert set(rows[0].keys()) == {"pool_id", "pool_name", "status", "free_slots"}
        assert rows[0]["status"] == "ready"
        # two slots already placed, capacity 4 -> two free
        assert rows[0]["free_slots"] == 2
    finally:
        _reset(db)


def test_a_tenant_cannot_select_the_shared_machine_row(db, ctx):
    try:
        _act_as(db, ctx["tenant_a_user"])
        rows = db.execute(
            "select id from public.agent_servers where id = %s", (ctx["host_id"],)
        ).fetchall()
        assert rows == []
    finally:
        _reset(db)


def test_a_tenant_cannot_select_the_shared_machines_jobs(db, ctx):
    try:
        _act_as(db, ctx["tenant_a_user"])
        rows = db.execute(
            "select id, log from public.agent_server_jobs where agent_server_id = %s",
            (ctx["host_id"],),
        ).fetchall()
        assert rows == []
    finally:
        _reset(db)


def test_a_tenant_sees_only_its_own_slot_on_a_shared_machine(db, ctx):
    try:
        _act_as(db, ctx["tenant_a_user"])
        rows = db.execute(
            "select id, org_id from public.agent_slots where agent_server_id = %s",
            (ctx["host_id"],),
        ).fetchall()
        seen = {str(r["id"]) for r in rows}
        assert str(ctx["slot_a"]) in seen
        assert str(ctx["slot_b"]) not in seen
    finally:
        _reset(db)


def test_a_non_platform_org_cannot_mark_its_own_host_shared(db, ctx):
    """201_shared_agent_server_platform_only.sql's trigger, exercised as a
    real tenant owner through RLS, not the service role."""
    try:
        owner = db.execute(
            """
            select user_id from public.organization_members
            where org_id = %s and role = 'owner' and user_id is not null
            order by created_at limit 1
            """,
            (ctx["tenant_a_org"],),
        ).fetchone()
        if not owner:
            pytest.skip("tenant org has no human owner")
        _as_service_role(db)
        own_server = db.execute(
            "insert into public.servers (org_id, name, host, username, auth_method)"
            " values (%s, %s, '198.51.100.10', 'ops', 'password') returning id",
            (ctx["tenant_a_org"], f"own-host-{uuid.uuid4().hex[:8]}"),
        ).fetchone()
        own_host = db.execute(
            "insert into public.agent_servers (org_id, server_id, workdir)"
            " values (%s, %s, '/opt/buildmill') returning id",
            (ctx["tenant_a_org"], own_server["id"]),
        ).fetchone()

        _act_as(db, owner["user_id"])
        with pytest.raises(psycopg.errors.RaiseException, match="platform-admin"):
            db.execute(
                "update public.agent_servers"
                " set shared = true, pool_name = 'sneaky', capacity = 5"
                " where id = %s",
                (own_host["id"],),
            )
    finally:
        _reset(db)
