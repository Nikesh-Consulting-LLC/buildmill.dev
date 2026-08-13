"""US-26.1 RLS: an admin can actually register an agent server.

Live SQL coverage for the write paths `routers/agent_servers.py` takes through
PostgREST **with the caller's own JWT** — so RLS, not the router, is the last
word on them. Migration 142 shipped with SELECT policies only, which made every
one of those writes fail with 42501 for an owner who passed both the router's
capability check and `is_org_member`. That is the regression these tests exist
to hold shut.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every test
runs inside a transaction that is rolled back — nothing is left behind, which
matters because that URL may point at a live project.
"""

from __future__ import annotations

import os
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


@pytest.fixture
def ctx(db):
    """An org with an owner, plus a throwaway server to point at.

    The server is created here rather than found: a database with no
    registered server would otherwise skip these tests silently, which is
    exactly how the RLS gap shipped. Everything rolls back.
    """
    db.rollback()
    owner = db.execute(
        """
        select m.user_id, m.org_id
        from public.organization_members m
        where m.user_id is not null and m.role = 'owner'
          and coalesce(m.status, 'active') = 'active'
        order by m.created_at
        limit 1
        """
    ).fetchone()
    if not owner:
        pytest.skip("no org with an owner")
    server = db.execute(
        "insert into public.servers (org_id, name, host, username, auth_method)"
        " values (%s, %s, '198.51.100.7', 'ops', 'password') returning id",
        (owner["org_id"], f"rls-test-{os.getpid()}"),
    ).fetchone()
    return {**owner, "server_id": server["id"]}


def _act_as(db, user_id) -> None:
    """Speak to Postgres exactly as PostgREST does for a signed-in user."""
    db.execute("select set_config('role', 'authenticated', true)")
    db.execute(
        "select set_config('request.jwt.claims',"
        " json_build_object('sub', %s::text, 'role', 'authenticated')::text, true)",
        (str(user_id),),
    )


def _reset(db) -> None:
    db.rollback()


def test_an_owner_can_register_an_agent_server(db, ctx):
    """The bug: this failed with 42501 while every capability check passed."""
    try:
        _act_as(db, ctx["user_id"])
        assert db.execute(
            "select public.has_org_capability(%s, 'manage_org') as ok", (ctx["org_id"],)
        ).fetchone()["ok"], "fixture owner should hold manage_org"

        row = db.execute(
            "insert into public.agent_servers (org_id, server_id, workdir)"
            " values (%s, %s, '/opt/buildmill') returning id, status",
            (ctx["org_id"], ctx["server_id"]),
        ).fetchone()
        assert row["status"] == "new"
    finally:
        _reset(db)


def test_an_owner_can_edit_the_host_definition(db, ctx):
    try:
        _act_as(db, ctx["user_id"])
        host = db.execute(
            "insert into public.agent_servers (org_id, server_id, workdir)"
            " values (%s, %s, '/opt/buildmill') returning id",
            (ctx["org_id"], ctx["server_id"]),
        ).fetchone()
        updated = db.execute(
            "update public.agent_servers set modules = array['claude']"
            " where id = %s returning modules",
            (host["id"],),
        ).fetchone()
        assert updated is not None, "PATCH /agent-servers/{id} would 500"
        assert updated["modules"] == ["claude"]
    finally:
        _reset(db)


def test_an_owner_can_pause_a_slot(db, ctx):
    """`PATCH .../slots/{id}` writes desired_state with the caller's JWT."""
    try:
        _act_as(db, ctx["user_id"])
        host = db.execute(
            "insert into public.agent_servers (org_id, server_id, workdir)"
            " values (%s, %s, '/opt/buildmill') returning id",
            (ctx["org_id"], ctx["server_id"]),
        ).fetchone()
        # the slot itself is written by the job engine (service role), so this
        # inserts as the owner only to have a row to update
        db.execute("select set_config('role', 'postgres', true)")
        slot = db.execute(
            "insert into public.agent_slots"
            " (org_id, agent_server_id, slot_index, name, service_name, workspace_path)"
            " values (%s, %s, 1, 'test-1', 'buildmill-agent@1', '/opt/buildmill/agents/1/workspace')"
            " returning id",
            (ctx["org_id"], host["id"]),
        ).fetchone()
        _act_as(db, ctx["user_id"])

        updated = db.execute(
            "update public.agent_slots set desired_state = 'enabled'"
            " where id = %s returning desired_state",
            (slot["id"],),
        ).fetchone()
        assert updated is not None, "enable/pause would 500"
        assert updated["desired_state"] == "enabled"
    finally:
        _reset(db)


def test_a_member_without_manage_org_cannot_register(db, ctx):
    """The capability is enforced by RLS, not only by the router."""
    try:
        member = db.execute(
            """
            select m.user_id from public.organization_members m
            where m.org_id = %s and m.user_id is not null
              and m.role in ('developer', 'reviewer', 'viewer')
              and coalesce(m.status, 'active') = 'active'
            order by m.created_at limit 1
            """,
            (ctx["org_id"],),
        ).fetchone()
        if not member:
            pytest.skip("no non-admin member in this org")
        _act_as(db, member["user_id"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                "insert into public.agent_servers (org_id, server_id, workdir)"
                " values (%s, %s, '/opt/buildmill')",
                (ctx["org_id"], ctx["server_id"]),
            )
    finally:
        _reset(db)


def test_another_orgs_member_sees_and_writes_nothing(db, ctx):
    """Cross-org isolation on the write path, not just the read path."""
    try:
        # Must be someone with NO membership in the target org. Picking "any
        # member of another org" is wrong wherever one person owns two orgs —
        # then the insert legitimately succeeds and the test lies about why.
        # Members of a platform-admin org are excluded too: is_platform_admin()
        # legitimately lets them write everywhere, which is not the isolation
        # this test is about.
        outsider = db.execute(
            """
            select m.user_id, m.org_id from public.organization_members m
            where m.org_id <> %(org)s and m.user_id is not null
              and not exists (
                select 1 from public.organization_members x
                where x.org_id = %(org)s and x.user_id = m.user_id
              )
              and not exists (
                select 1 from public.organization_members pm
                join public.organizations po on po.id = pm.org_id
                where pm.user_id = m.user_id and po.is_platform_admin = true
              )
            order by m.created_at limit 1
            """,
            {"org": ctx["org_id"]},
        ).fetchone()
        if not outsider:
            pytest.skip("no user outside this org to test isolation with")
        _act_as(db, outsider["user_id"])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                "insert into public.agent_servers (org_id, server_id, workdir)"
                " values (%s, %s, '/opt/buildmill')",
                (ctx["org_id"], ctx["server_id"]),
            )
    finally:
        _reset(db)
