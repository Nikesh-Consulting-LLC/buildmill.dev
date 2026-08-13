"""US-3.1: live SQL coverage — workers registry, write-only token RPCs, RLS.

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
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    # These tests impersonate a signed-in *person*: the uid goes into
    # request.jwt.claims and is read back by is_platform_admin /
    # has_org_capability. Since Phase 9's unified principals, an org member
    # can be an agent, and agents have no auth user — organization_members
    # .user_id is NULL for every one of them (principal_id is the identity;
    # user_id is only the auth link). An unordered LIMIT 1 therefore usually
    # returned an agent row, str(None) became the literal "None", and it
    # reached Postgres as a uuid — failing a different subset of tests on
    # every run. Filter to a real user and order deterministically.
    member = db.execute(
        """
        select user_id
        from public.organization_members
        where org_id = %s and user_id is not null
        order by user_id
        limit 1
        """,
        (project["org_id"],),
    ).fetchone()
    if not member:
        pytest.skip("no human org member")

    # US-57.2: create_worker now refuses an autonomous worker past
    # organizations.max_agents. These tests exercise token/vault mechanics,
    # not the quota, and `finally` blocks below only ever delete the
    # `workers` row they created — Phase 26 deliberately keeps the
    # principal/membership behind ("kept, not deleted, so past runs still
    # name who did them"), so this org's agent-kind membership count only
    # ever grows across every run of this suite. Keep the ceiling ahead of
    # that count rather than let an unrelated test start failing on it.
    agents = db.execute(
        """
        select count(*) as n
        from public.organization_members om
        join public.principals pr on pr.id = om.principal_id
        where om.org_id = %s and pr.kind = 'agent'
        """,
        (project["org_id"],),
    ).fetchone()["n"]
    db.execute(
        "update public.organizations set max_agents = %s where id = %s and max_agents < %s",
        (agents + 50, project["org_id"], agents + 50),
    )
    db.commit()

    return {"org_id": project["org_id"], "user_id": str(member["user_id"])}


def _set_auth(db, user_id: str):
    """Transaction-local JWT claims so auth.uid() resolves (legacy + json)."""
    db.execute(
        "select set_config('request.jwt.claim.sub', %s, true)", (user_id,)
    )
    db.execute(
        "select set_config('request.jwt.claim.role', 'authenticated', true)"
    )
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


def _create_worker(db, org_id, name, wtype, user_id=None):
    return db.execute(
        "select * from public.create_worker(%s, %s, %s, %s)",
        (org_id, name, wtype, user_id),
    ).fetchone()


def test_create_worker_mints_token_and_stores_hash_only(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test runner {uuid.uuid4()}", "autonomous"
        )
        worker_id, token = row["worker_id"], row["token"]
        assert token.startswith("sfw_") and len(token) >= 40
        db.commit()

        w = db.execute(
            "select * from public.workers where id = %s", (worker_id,)
        ).fetchone()
        assert w["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert w["token_last4"] == token[-4:]
        assert w["status"] == "active"
        assert w["type"] == "autonomous"
        assert w["user_id"] is None
        assert w["last_seen_at"] is None

        cols = {
            r["column_name"]
            for r in db.execute(
                "select column_name from information_schema.columns "
                "where table_schema = 'public' and table_name = 'workers'"
            ).fetchall()
        }
        assert "token" not in cols  # plaintext is never stored
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_create_worker_rejects_non_member(db, ctx):
    try:
        _set_auth(db, str(uuid.uuid4()))
        with pytest.raises(psycopg.errors.RaiseException, match="not authorized"):
            _create_worker(db, ctx["org_id"], "intruder", "autonomous")
    finally:
        db.rollback()


def test_create_worker_refuses_past_the_org_agent_quota(db, ctx):
    """US-57.2: the ceiling is read live against the roster, not cached —
    pin it to the org's CURRENT count so the very next autonomous create
    is the one that trips it, then restore the ctx fixture's self-healing
    headroom so later tests in this file are unaffected."""
    prior = db.execute(
        "select max_agents from public.organizations where id = %s", (ctx["org_id"],)
    ).fetchone()["max_agents"]
    agents = db.execute(
        """
        select count(*) as n
        from public.organization_members om
        join public.principals pr on pr.id = om.principal_id
        where om.org_id = %s and pr.kind = 'agent'
        """,
        (ctx["org_id"],),
    ).fetchone()["n"]
    try:
        db.execute(
            "update public.organizations set max_agents = %s where id = %s",
            (agents, ctx["org_id"]),
        )
        db.commit()
        _set_auth(db, ctx["user_id"])
        with pytest.raises(
            psycopg.errors.RaiseException, match="agent limit"
        ):
            _create_worker(db, ctx["org_id"], "one-too-many", "autonomous")
    finally:
        db.rollback()
        db.execute(
            "update public.organizations set max_agents = %s where id = %s",
            (max(prior, agents + 50), ctx["org_id"]),
        )
        db.commit()


def test_create_worker_rejects_unknown_linked_user(db, ctx):
    """A uuid with no principal at all fails on the principal guard.

    create_worker resolves the principal before checking org membership,
    which is the only order that can work — there is nothing to check
    membership *for* until the principal exists.
    """
    try:
        _set_auth(db, ctx["user_id"])
        with pytest.raises(
            psycopg.errors.RaiseException, match="no principal for user"
        ):
            _create_worker(
                db, ctx["org_id"], "human x", "human", str(uuid.uuid4())
            )
    finally:
        db.rollback()


def test_create_worker_rejects_linked_user_outside_org(db, ctx):
    """A real user who is not a member of *this* org fails on membership.

    This previously passed a random uuid, which has no principal, so it
    tripped the principal guard and never reached the membership guard it
    is named for. It needs a user who genuinely has a principal but no
    membership in ctx's org.
    """
    outsider = db.execute(
        """
        select p.auth_user_id
        from public.principals p
        where p.auth_user_id is not null
          and not exists (
            select 1 from public.organization_members om
            where om.principal_id = p.id and om.org_id = %s
          )
        order by p.auth_user_id
        limit 1
        """,
        (ctx["org_id"],),
    ).fetchone()
    if not outsider:
        pytest.skip("every principal with a user is already in this org")
    try:
        _set_auth(db, ctx["user_id"])
        with pytest.raises(psycopg.errors.RaiseException, match="not an org member"):
            _create_worker(
                db,
                ctx["org_id"],
                "human x",
                "human",
                str(outsider["auth_user_id"]),
            )
    finally:
        db.rollback()


def test_regenerate_rotates_token_and_reactivates(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test regen {uuid.uuid4()}", "autonomous"
        )
        worker_id, old_token = row["worker_id"], row["token"]
        db.execute(
            "update public.workers set status = 'revoked' where id = %s",
            (worker_id,),
        )
        db.commit()

        _set_auth(db, ctx["user_id"])
        new_token = db.execute(
            "select public.regenerate_worker_token(%s) as token", (worker_id,)
        ).fetchone()["token"]
        db.commit()

        assert new_token != old_token
        w = db.execute(
            "select * from public.workers where id = %s", (worker_id,)
        ).fetchone()
        assert w["token_hash"] == hashlib.sha256(new_token.encode()).hexdigest()
        assert w["token_last4"] == new_token[-4:]
        assert w["status"] == "active"  # fresh token on the same row
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_regenerate_rejects_non_member(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test regen-guard {uuid.uuid4()}", "autonomous"
        )
        worker_id = row["worker_id"]
        db.commit()

        _set_auth(db, str(uuid.uuid4()))
        with pytest.raises(psycopg.errors.RaiseException, match="not authorized"):
            db.execute(
                "select public.regenerate_worker_token(%s)", (worker_id,)
            )
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_create_worker_stores_vault_secret_and_reveal_returns_it(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test reveal {uuid.uuid4()}", "autonomous"
        )
        worker_id, token = row["worker_id"], row["token"]
        db.commit()

        w = db.execute(
            "select vault_secret_id from public.workers where id = %s", (worker_id,)
        ).fetchone()
        assert w["vault_secret_id"] is not None

        _set_auth(db, ctx["user_id"])
        revealed = db.execute(
            "select public.reveal_worker_token(%s) as token", (worker_id,)
        ).fetchone()["token"]
        assert revealed == token
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_regenerate_updates_vault_secret_in_place(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test reveal-regen {uuid.uuid4()}", "autonomous"
        )
        worker_id = row["worker_id"]
        db.commit()

        original_secret_id = db.execute(
            "select vault_secret_id from public.workers where id = %s", (worker_id,)
        ).fetchone()["vault_secret_id"]

        _set_auth(db, ctx["user_id"])
        new_token = db.execute(
            "select public.regenerate_worker_token(%s) as token", (worker_id,)
        ).fetchone()["token"]
        db.commit()

        w = db.execute(
            "select vault_secret_id from public.workers where id = %s", (worker_id,)
        ).fetchone()
        assert w["vault_secret_id"] == original_secret_id  # updated in place, not replaced

        _set_auth(db, ctx["user_id"])
        revealed = db.execute(
            "select public.reveal_worker_token(%s) as token", (worker_id,)
        ).fetchone()["token"]
        assert revealed == new_token
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_reveal_worker_token_rejects_non_member(db, ctx):
    worker_id = None
    try:
        _set_auth(db, ctx["user_id"])
        row = _create_worker(
            db, ctx["org_id"], f"sql-test reveal-guard {uuid.uuid4()}", "autonomous"
        )
        worker_id = row["worker_id"]
        db.commit()

        _set_auth(db, str(uuid.uuid4()))
        with pytest.raises(psycopg.errors.RaiseException, match="not authorized"):
            db.execute("select public.reveal_worker_token(%s)", (worker_id,))
    finally:
        db.rollback()
        if worker_id:
            db.execute("delete from public.workers where id = %s", (worker_id,))
            db.commit()


def test_workers_cross_org_isolation(db, ctx):
    other_org = None
    try:
        other_org = db.execute(
            "insert into public.organizations (name) values ('rls-test-org') "
            "returning id"
        ).fetchone()["id"]
        foreign_worker = db.execute(
            "insert into public.workers (org_id, name, type, token_hash, token_last4) "
            "values (%s, 'foreign worker', 'autonomous', %s, 'abcd') returning id",
            (other_org, "f" * 64),
        ).fetchone()["id"]
        db.commit()

        _set_auth(db, ctx["user_id"])
        db.execute("set local role authenticated")
        visible = db.execute(
            "select count(*) as n from public.workers where org_id = %s",
            (other_org,),
        ).fetchone()["n"]
        assert visible == 0

        updated = db.execute(
            "update public.workers set name = 'hijacked' where id = %s",
            (foreign_worker,),
        ).rowcount
        assert updated == 0

        deleted = db.execute(
            "delete from public.workers where id = %s", (foreign_worker,)
        ).rowcount
        assert deleted == 0
        db.rollback()  # discard role + any effects

        # A caller with no membership anywhere is refused outright. This is the
        # isolation property proper, and it does not depend on who the fixture
        # user happens to be.
        _set_auth(db, str(uuid.uuid4()))
        with pytest.raises(psycopg.errors.RaiseException, match="not authorized"):
            _create_worker(db, other_org, "stranger", "autonomous")
        db.rollback()

        # US-75.1: this used to assert the fixture user was refused too, and
        # went red when that user became an owner of the platform-admin org.
        # `has_org_capability` grants a platform admin every capability in
        # every org BY DESIGN (us-57), so the refusal was never going to come.
        # Assert whichever rule actually governs this user, so the test says
        # something true on any database instead of on seed data:
        _set_auth(db, ctx["user_id"])
        is_admin = db.execute(
            "select public.is_platform_admin() as ok"
        ).fetchone()["ok"]
        allowed = db.execute(
            "select public.has_org_capability(%s, 'develop') as ok", (other_org,)
        ).fetchone()["ok"]
        if is_admin:
            # The escape hatch, pinned: remove it and this trips.
            assert allowed is True, (
                "a platform admin lost cross-org capability — if that is "
                "intended, this test and us-57's superadmin pools disagree"
            )
        else:
            assert allowed is False
            with pytest.raises(
                psycopg.errors.RaiseException, match="not authorized"
            ):
                _create_worker(db, other_org, "intruder", "autonomous")
        db.rollback()
    finally:
        db.rollback()
        if other_org:
            db.execute(
                "delete from public.organizations where id = %s", (other_org,)
            )
            db.commit()
