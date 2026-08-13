"""US-57.18: the orphan-slot signature, proven against the live schema.

The sweep removes a slot only when all three hold: the slot is live, its
worker's token is revoked, and the worker's principal has no membership row
in the slot's org. This runs the sweep's own SQL (the shared constant, so
the test can't drift from the code) over fixture rows inside one rolled-back
transaction, and proves it matches the deleted-agent shape and nothing else —
a false positive here would tear a live agent off its machine.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app.agent_provision import ORPHANED_SLOTS_SQL

# The predicate is what the test owns; the batching tail would only hide our
# fixture row behind whatever real orphans the database happens to hold.
_SQL = ORPHANED_SLOTS_SQL.replace("limit 3", "")


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
    """A live agent on a ready host: org, principal, active membership,
    active worker, active slot — the shape the sweep must never touch."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    org_id = org["id"]
    server_id = str(uuid.uuid4())
    db.execute(
        "insert into public.servers (id, org_id, name, host, username, auth_method) "
        "values (%s, %s, 'sql-test us-57.18', 'test.invalid', 'noone', 'password')",
        (server_id, org_id),
    )
    host_id = str(uuid.uuid4())
    db.execute(
        "insert into public.agent_servers (id, org_id, server_id, status) "
        "values (%s, %s, %s, 'ready')",
        (host_id, org_id, server_id),
    )
    principal_id = str(uuid.uuid4())
    db.execute(
        "insert into public.principals (id, kind, display_name) "
        "values (%s, 'agent', 'sql-test us-57.18')",
        (principal_id,),
    )
    db.execute(
        "insert into public.organization_members (org_id, principal_id, role, status) "
        "values (%s, %s, 'agent', 'active')",
        (org_id, principal_id),
    )
    worker_id = str(uuid.uuid4())
    db.execute(
        "insert into public.workers "
        "(id, org_id, name, type, token_hash, token_last4, status, principal_id) "
        "values (%s, %s, 'sql-test us-57.18', 'autonomous', %s, '0000', 'active', %s)",
        (worker_id, org_id, hashlib.sha256(worker_id.encode()).hexdigest(), principal_id),
    )
    slot_id = str(uuid.uuid4())
    db.execute(
        "insert into public.agent_slots "
        "(id, org_id, agent_server_id, slot_index, name, service_name, "
        " workspace_path, worker_id, status) "
        "values (%s, %s, %s, 64, 'sql-test us-57.18', 'buildmill-agent@64', "
        "        '/tmp/sql-test/64', %s, 'active')",
        (slot_id, org_id, host_id, worker_id),
    )
    yield {
        "org_id": org_id,
        "host_id": host_id,
        "principal_id": principal_id,
        "worker_id": worker_id,
        "slot_id": slot_id,
    }
    db.rollback()


def _orphans(db, ctx):
    rows = db.execute(_SQL).fetchall()
    return [r for r in rows if str(r["id"]) == ctx["slot_id"]]


def test_a_live_agent_is_not_an_orphan(db, ctx):
    assert _orphans(db, ctx) == []


def test_a_suspended_member_is_not_an_orphan(db, ctx):
    # Suspension revokes the worker (migration 089/232) but keeps the
    # membership row — us-55.2's reactivate must find this agent intact.
    db.execute(
        "update public.organization_members set status = 'suspended' "
        "where org_id = %s and principal_id = %s",
        (ctx["org_id"], ctx["principal_id"]),
    )
    assert (
        db.execute(
            "select status from public.workers where id = %s", (ctx["worker_id"],)
        ).fetchone()["status"]
        == "revoked"
    )
    assert _orphans(db, ctx) == []


def test_a_deliberate_revoke_is_not_an_orphan(db, ctx):
    db.execute(
        "update public.workers set status = 'revoked' where id = %s",
        (ctx["worker_id"],),
    )
    assert _orphans(db, ctx) == []


def test_a_deleted_agent_is_an_orphan(db, ctx):
    db.execute(
        "delete from public.organization_members "
        "where org_id = %s and principal_id = %s",
        (ctx["org_id"], ctx["principal_id"]),
    )
    matches = _orphans(db, ctx)
    assert len(matches) == 1
    assert int(matches[0]["slot_index"]) == 64


def test_a_recent_removal_job_defers_the_orphan(db, ctx):
    # The retry guard: a cleanup attempted in the last 15 minutes is not
    # retried on every tick, even though the slot still matches.
    db.execute(
        "delete from public.organization_members "
        "where org_id = %s and principal_id = %s",
        (ctx["org_id"], ctx["principal_id"]),
    )
    db.execute(
        "insert into public.agent_server_jobs "
        "(org_id, agent_server_id, slot_id, kind, status, started_by_email) "
        "values (%s, %s, %s, 'remove_slot', 'failed', 'orphan-cleanup')",
        (ctx["org_id"], ctx["host_id"], ctx["slot_id"]),
    )
    assert _orphans(db, ctx) == []
