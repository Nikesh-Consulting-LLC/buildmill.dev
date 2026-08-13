"""US-55.2: suspend stamps what it revokes; reactivate restores exactly that.

Live-SQL coverage for migration 232. The suspend cascade (migration 089)
revoked a principal's workers and forgot which ones; reactivation restored the
membership and nothing else, leaving a member that reads "active" with every
credential silently dead. Migration 232 stamps `revoked_by_suspension_at` on
exactly the workers the cascade revokes, restores exactly the stamped ones on
reactivate, and a hygiene trigger keeps every other revoke path unstamped so a
deliberate revoke survives any suspend cycle.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every test
works inside one transaction and rolls back — nothing is left behind.
"""

from __future__ import annotations

import hashlib
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


@pytest.fixture
def scratch(db):
    """A fresh agent principal with an active membership and one active
    worker, in an existing org, all inside the test's transaction."""
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    principal_id = str(uuid.uuid4())
    db.execute(
        "insert into public.principals (id, kind, display_name) "
        "values (%s, 'agent', %s)",
        (principal_id, f"sql-test us-55.2 {principal_id[:8]}"),
    )
    db.execute(
        "insert into public.organization_members (org_id, principal_id, role, status) "
        "values (%s, %s, 'agent', 'active')",
        (org["id"], principal_id),
    )
    worker_id = str(uuid.uuid4())
    db.execute(
        "insert into public.workers "
        "(id, org_id, name, type, token_hash, token_last4, status, principal_id) "
        "values (%s, %s, %s, 'autonomous', %s, '0000', 'active', %s)",
        (
            worker_id,
            org["id"],
            f"sql-test us-55.2 {worker_id[:8]}",
            hashlib.sha256(worker_id.encode()).hexdigest(),
            principal_id,
        ),
    )
    yield {"org_id": org["id"], "principal_id": principal_id, "worker_id": worker_id}
    db.rollback()


def _worker(db, worker_id):
    return db.execute(
        "select status, revoked_by_suspension_at from public.workers where id = %s",
        (worker_id,),
    ).fetchone()


def _set_membership(db, ctx, status):
    db.execute(
        "update public.organization_members set status = %s "
        "where org_id = %s and principal_id = %s",
        (status, ctx["org_id"], ctx["principal_id"]),
    )


def test_suspend_stamps_and_reactivate_restores(db, scratch):
    _set_membership(db, scratch, "suspended")
    w = _worker(db, scratch["worker_id"])
    assert w["status"] == "revoked"
    assert w["revoked_by_suspension_at"] is not None

    _set_membership(db, scratch, "active")
    w = _worker(db, scratch["worker_id"])
    assert w["status"] == "active"
    assert w["revoked_by_suspension_at"] is None


def test_deliberate_revoke_survives_a_suspend_cycle(db, scratch):
    # The Revoke button: a bare status update, no stamp — the hygiene trigger
    # guarantees the marker is clear no matter which call site did it.
    db.execute(
        "update public.workers set status = 'revoked' where id = %s",
        (scratch["worker_id"],),
    )
    w = _worker(db, scratch["worker_id"])
    assert w["revoked_by_suspension_at"] is None

    _set_membership(db, scratch, "suspended")
    _set_membership(db, scratch, "active")
    w = _worker(db, scratch["worker_id"])
    assert w["status"] == "revoked", "a deliberate revoke must not resurrect"
    assert w["revoked_by_suspension_at"] is None


def test_removal_revokes_without_a_stamp(db, scratch):
    # DELETE has no reactivate, so nothing may be marked restorable.
    db.execute(
        "delete from public.organization_members "
        "where org_id = %s and principal_id = %s",
        (scratch["org_id"], scratch["principal_id"]),
    )
    w = _worker(db, scratch["worker_id"])
    assert w["status"] == "revoked"
    assert w["revoked_by_suspension_at"] is None


def test_any_activation_clears_the_stamp(db, scratch):
    # Re-issue mints a new credential and flips the worker active directly;
    # an active worker must never carry restoration debt.
    _set_membership(db, scratch, "suspended")
    assert _worker(db, scratch["worker_id"])["revoked_by_suspension_at"] is not None
    db.execute(
        "update public.workers set status = 'active' where id = %s",
        (scratch["worker_id"],),
    )
    w = _worker(db, scratch["worker_id"])
    assert w["status"] == "active"
    assert w["revoked_by_suspension_at"] is None
