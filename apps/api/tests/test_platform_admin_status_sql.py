"""Migration 193: live SQL coverage — is_platform_admin requires ACTIVE membership.

Migration 089 made is_org_member / is_org_owner / has_org_capability require
organization_members.status = 'active', but is_platform_admin() was missed: a
suspended platform-org member kept global admin over every org (and rode the
is_platform_admin() short-circuit inside has_org_capability past its status
check). These tests pin the fix.

Everything runs inside one transaction that is always rolled back — the auth
user, the trigger-created profile/principal, the org and the membership all
vanish with it; nothing commits.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
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


@pytest.fixture
def platform_ctx(db):
    """A fresh auth user, its trigger-created principal, and an active
    membership in a fresh platform-admin org. Never committed: the trailing
    rollback erases the whole graph."""
    db.rollback()
    user_id = str(uuid.uuid4())
    email = f"sql-test-{user_id[:8]}@example.invalid"
    db.execute("insert into auth.users (id, email) values (%s, %s)", (user_id, email))
    # handle_new_user() has already minted the principal for this auth user.
    principal = db.execute(
        "select id from public.principals where auth_user_id = %s", (user_id,)
    ).fetchone()
    org = db.execute(
        "insert into public.organizations (name, is_platform_admin) "
        "values (%s, true) returning id",
        (f"sql-test platform org {user_id[:8]}",),
    ).fetchone()
    # role 'developer', not 'owner': is_platform_admin is role-blind, and a
    # sole owner could not be suspended past the last-active-owner guard.
    db.execute(
        "insert into public.organization_members (org_id, principal_id, role, status) "
        "values (%s, %s, 'developer', 'active')",
        (org["id"], principal["id"]),
    )
    yield {
        "user_id": user_id,
        "principal_id": principal["id"],
        "org_id": org["id"],
    }
    db.rollback()


def _set_auth(db, user_id: str):
    """Transaction-local JWT claims so auth.uid() resolves (legacy + json)."""
    db.execute("select set_config('request.jwt.claim.sub', %s, true)", (user_id,))
    db.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


def _is_platform_admin(db) -> bool:
    return db.execute("select public.is_platform_admin() as ok").fetchone()["ok"]


def test_active_platform_org_member_is_platform_admin(db, platform_ctx):
    """Control: the positive path survives the 193 status check."""
    _set_auth(db, platform_ctx["user_id"])
    assert _is_platform_admin(db) is True


def test_suspended_platform_org_member_is_not_platform_admin(db, platform_ctx):
    """The regression 193 fixes: suspension must revoke platform admin."""
    db.execute(
        "update public.organization_members set status = 'suspended' "
        "where org_id = %s and principal_id = %s",
        (platform_ctx["org_id"], platform_ctx["principal_id"]),
    )
    _set_auth(db, platform_ctx["user_id"])
    assert _is_platform_admin(db) is False


def test_removed_platform_org_member_is_not_platform_admin(db, platform_ctx):
    """Removal (the membership row deleted) revokes platform admin too."""
    db.execute(
        "delete from public.organization_members "
        "where org_id = %s and principal_id = %s",
        (platform_ctx["org_id"], platform_ctx["principal_id"]),
    )
    _set_auth(db, platform_ctx["user_id"])
    assert _is_platform_admin(db) is False


def test_active_member_of_ordinary_org_is_not_platform_admin(db, platform_ctx):
    """Control: active membership grants nothing without the org flag."""
    db.execute(
        "update public.organizations set is_platform_admin = false where id = %s",
        (platform_ctx["org_id"],),
    )
    _set_auth(db, platform_ctx["user_id"])
    assert _is_platform_admin(db) is False
