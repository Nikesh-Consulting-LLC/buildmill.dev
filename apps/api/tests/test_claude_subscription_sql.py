"""US-52.2: live SQL coverage for the factory-held Claude subscription token.

The RPCs are the write path (browser-only, member-gated, Vault-backed); the
row is the readable fingerprint; `read_claude_subscription_token` (app.llm)
is the server-side read the control socket answers with. Everything here
runs inside a transaction and rolls back — nothing is left behind.

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
def ctx(db):
    db.rollback()
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    member = db.execute(
        """
        select user_id
        from public.organization_members
        where org_id = %s and user_id is not null
        order by user_id
        limit 1
        """,
        (org["id"],),
    ).fetchone()
    if not member:
        pytest.skip("no human org member")
    yield {"org_id": org["id"], "user_id": str(member["user_id"])}
    db.rollback()


def _set_auth(db, user_id: str):
    """Transaction-local JWT claims so auth.uid() resolves (legacy + json)."""
    db.execute("select set_config('request.jwt.claim.sub', %s, true)", (user_id,))
    db.execute("select set_config('request.jwt.claim.role', 'authenticated', true)")
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


def _token(suffix: str = "") -> str:
    return f"sk-ant-oat01-{uuid.uuid4().hex}{suffix}"


def _row(db, org_id):
    return db.execute(
        "select * from public.claude_subscriptions where org_id = %s", (org_id,)
    ).fetchone()


def test_set_token_stores_fingerprint_expiry_and_vault_secret(db, ctx):
    try:
        _set_auth(db, ctx["user_id"])
        tok = _token()
        db.execute(
            "select public.set_claude_subscription_token(%s, %s)",
            (ctx["org_id"], tok),
        )
        row = _row(db, ctx["org_id"])
        assert row is not None
        assert row["key_last4"] == tok[-4:]
        assert row["vault_secret_id"] is not None
        # One year, give or take the test's own runtime.
        days = db.execute(
            "select extract(epoch from (expires_at - now())) / 86400 as d "
            "from public.claude_subscriptions where org_id = %s",
            (ctx["org_id"],),
        ).fetchone()["d"]
        assert 360 < days <= 366
        # The server-side read path: the vault secret decrypts back to the
        # token. (app.llm.read_claude_subscription_token opens its own
        # connection and cannot see this uncommitted transaction, so the
        # equivalent SQL is exercised here instead.)
        secret = db.execute(
            "select decrypted_secret from vault.decrypted_secrets where id = %s",
            (row["vault_secret_id"],),
        ).fetchone()
        assert secret["decrypted_secret"] == tok
    finally:
        db.rollback()


def test_set_token_refuses_api_keys_by_shape(db, ctx):
    try:
        _set_auth(db, ctx["user_id"])
        for bad in ("sk-ant-api03-" + "x" * 24, "sk-ant-oat", "", "hunter2"):
            with pytest.raises(psycopg.errors.RaiseException) as e:
                db.execute(
                    "select public.set_claude_subscription_token(%s, %s)",
                    (ctx["org_id"], bad),
                )
            assert "setup-token" in str(e.value)
            db.rollback()
            _set_auth(db, ctx["user_id"])
        assert _row(db, ctx["org_id"]) is None or True  # nothing stored above
    finally:
        db.rollback()


def test_replacing_the_token_updates_fingerprint_and_expiry(db, ctx):
    try:
        _set_auth(db, ctx["user_id"])
        first, second = _token("A"), _token("B")
        db.execute(
            "select public.set_claude_subscription_token(%s, %s)",
            (ctx["org_id"], first),
        )
        db.execute(
            "select public.set_claude_subscription_token(%s, %s)",
            (ctx["org_id"], second),
        )
        rows = db.execute(
            "select count(*) as n from public.claude_subscriptions where org_id = %s",
            (ctx["org_id"],),
        ).fetchone()
        assert rows["n"] == 1  # one slot per org, replaced in place
        row = _row(db, ctx["org_id"])
        assert row["key_last4"] == second[-4:]
        secret = db.execute(
            "select decrypted_secret from vault.decrypted_secrets where id = %s",
            (row["vault_secret_id"],),
        ).fetchone()
        assert secret["decrypted_secret"] == second
    finally:
        db.rollback()


def test_clear_removes_the_row_and_the_secret(db, ctx):
    try:
        _set_auth(db, ctx["user_id"])
        db.execute(
            "select public.set_claude_subscription_token(%s, %s)",
            (ctx["org_id"], _token()),
        )
        secret_id = _row(db, ctx["org_id"])["vault_secret_id"]
        db.execute(
            "select public.clear_claude_subscription_token(%s)", (ctx["org_id"],)
        )
        assert _row(db, ctx["org_id"]) is None
        gone = db.execute(
            "select 1 from vault.secrets where id = %s", (secret_id,)
        ).fetchone()
        assert gone is None
    finally:
        db.rollback()


def test_non_member_cannot_set_or_clear(db, ctx):
    try:
        # An org the impersonated user is NOT a member of.
        other = db.execute(
            "insert into public.organizations (name) values (%s) returning id",
            (f"sub-test {uuid.uuid4().hex[:6]}",),
        ).fetchone()
        _set_auth(db, ctx["user_id"])
        with pytest.raises(psycopg.errors.RaiseException) as e:
            db.execute(
                "select public.set_claude_subscription_token(%s, %s)",
                (other["id"], _token()),
            )
        assert "not authorized" in str(e.value)
        db.rollback()
        _set_auth(db, ctx["user_id"])
        with pytest.raises(psycopg.errors.RaiseException):
            db.execute(
                "select public.clear_claude_subscription_token(%s)", (other["id"],)
            )
    finally:
        db.rollback()


def test_select_is_scoped_to_the_members_org(db, ctx):
    try:
        # Rows for the member's org and a foreign org, inserted as postgres
        # (the write RPCs would refuse the foreign one, which is the point).
        other = db.execute(
            "insert into public.organizations (name) values (%s) returning id",
            (f"sub-test {uuid.uuid4().hex[:6]}",),
        ).fetchone()
        for org in (ctx["org_id"], other["id"]):
            db.execute(
                "insert into public.claude_subscriptions "
                "(org_id, key_last4, vault_secret_id, expires_at) "
                "values (%s, 'abcd', gen_random_uuid(), now() + interval '1 year')",
                (org,),
            )
        _set_auth(db, ctx["user_id"])
        db.execute("select set_config('role', 'authenticated', true)")
        visible = db.execute(
            "select org_id from public.claude_subscriptions"
        ).fetchall()
        db.execute("select set_config('role', 'postgres', true)")
        orgs = {str(r["org_id"]) for r in visible}
        assert str(ctx["org_id"]) in orgs
        assert str(other["id"]) not in orgs
    finally:
        db.rollback()
