"""US-16.1/16.2: live SQL coverage for the report-key RPCs and the dedup
upsert — key round-trip, rotation, cross-org isolation, RLS on `app_issues`,
and the partial unique index that makes a repeat safe under concurrency.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Every test
rolls back — nothing here is left behind on the database it ran against.
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


@pytest.fixture()
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
    # Every test in this module is a transaction that is thrown away.
    conn.rollback()
    conn.close()


def _set_auth(db, user_id: str) -> None:
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        ('{"sub": "%s", "role": "authenticated"}' % user_id,),
    )


def _org_with_deployment(db, label: str) -> dict:
    """A whole org — user, principal, membership, project, server, deployment —
    built inside the test's transaction so nothing collides with real data."""
    user_id = str(uuid.uuid4())
    suffix = user_id[:8]
    db.execute(
        "insert into auth.users (id, aud, role, email) values (%s, 'authenticated',"
        " 'authenticated', %s)",
        (user_id, f"{label}-{suffix}@test.invalid"),
    )
    # 086_unified_principals mints the principal from a trigger on auth.users.
    principal = db.execute(
        "select id from public.principals where auth_user_id = %s", (user_id,)
    ).fetchone()
    org = db.execute(
        "insert into public.organizations (name, shortname) values (%s, %s) returning id",
        (f"T-{label}", f"{label}-{suffix}"),
    ).fetchone()
    db.execute(
        "insert into public.organization_members (org_id, principal_id, status)"
        " values (%s, %s, 'active')",
        (org["id"], principal["id"]),
    )
    project = db.execute(
        "insert into public.projects (org_id, name, slug, repo_full_name)"
        " values (%s, %s, %s, 'o/r') returning id",
        (org["id"], f"P-{label}", f"{label}-{suffix}"),
    ).fetchone()
    server = db.execute(
        "insert into public.servers (org_id, name, host, username, auth_method)"
        " values (%s, %s, 'h', 'u', 'password') returning id",
        (org["id"], f"S-{label}"),
    ).fetchone()
    deployment = db.execute(
        "insert into public.deployments (org_id, project_id, server_id, name, branch,"
        " target_folder) values (%s, %s, %s, 'prod', 'main', '/srv') returning id",
        (org["id"], project["id"], server["id"]),
    ).fetchone()
    return {
        "user_id": user_id,
        "org_id": org["id"],
        "project_id": project["id"],
        "deployment_id": deployment["id"],
    }


# --- the report key ---------------------------------------------------------


def test_key_round_trip_and_what_is_stored(db):
    a = _org_with_deployment(db, "a")
    _set_auth(db, a["user_id"])

    key = db.execute(
        "select public.generate_deployment_report_key(%s) as k", (a["deployment_id"],)
    ).fetchone()["k"]
    assert key.startswith("sfr_")

    row = db.execute(
        "select issue_report_key_hash as h, issue_report_key_last4 as l4"
        " from public.deployments where id = %s",
        (a["deployment_id"],),
    ).fetchone()
    # The hash is what authenticates; the plaintext is only in Vault.
    assert row["h"] == hashlib.sha256(key.encode()).hexdigest()
    assert row["l4"] == key[-4:]

    revealed = db.execute(
        "select public.reveal_deployment_report_key(%s) as k", (a["deployment_id"],)
    ).fetchone()["k"]
    assert revealed == key


def test_rotation_invalidates_the_old_key_immediately(db):
    a = _org_with_deployment(db, "a")
    _set_auth(db, a["user_id"])
    first = db.execute(
        "select public.generate_deployment_report_key(%s) as k", (a["deployment_id"],)
    ).fetchone()["k"]
    old_hash = db.execute(
        "select issue_report_key_hash as h from public.deployments where id = %s",
        (a["deployment_id"],),
    ).fetchone()["h"]

    second = db.execute(
        "select public.generate_deployment_report_key(%s) as k", (a["deployment_id"],)
    ).fetchone()["k"]
    new_hash = db.execute(
        "select issue_report_key_hash as h from public.deployments where id = %s",
        (a["deployment_id"],),
    ).fetchone()["h"]

    assert second != first
    # No grace period: the old key stops authenticating the moment this returns.
    assert new_hash != old_hash
    revealed = db.execute(
        "select public.reveal_deployment_report_key(%s) as k", (a["deployment_id"],)
    ).fetchone()["k"]
    assert revealed == second


def test_reveal_before_a_key_exists_says_so(db):
    a = _org_with_deployment(db, "a")
    _set_auth(db, a["user_id"])
    with pytest.raises(psycopg.errors.RaiseException, match="no report key"):
        db.execute(
            "select public.reveal_deployment_report_key(%s)", (a["deployment_id"],)
        )
    db.rollback()


@pytest.mark.parametrize(
    "rpc", ["generate_deployment_report_key", "reveal_deployment_report_key"]
)
def test_neither_rpc_crosses_an_org_boundary(db, rpc):
    a = _org_with_deployment(db, "a")
    b = _org_with_deployment(db, "b")
    _set_auth(db, a["user_id"])
    with pytest.raises(psycopg.errors.RaiseException, match="not authorized"):
        db.execute(f"select public.{rpc}(%s)", (b["deployment_id"],))
    db.rollback()


# --- the inbox --------------------------------------------------------------


def _report(db, ctx, **overrides) -> str:
    values = {
        "source": "automated",
        "fingerprint": "fp-1",
        "title": "TypeError: boom",
        **overrides,
    }
    return db.execute(
        "insert into public.app_issues (org_id, project_id, deployment_id, source,"
        " fingerprint, title) values (%s, %s, %s, %s, %s, %s) returning id",
        (
            ctx["org_id"],
            ctx["project_id"],
            ctx["deployment_id"],
            values["source"],
            values["fingerprint"],
            values["title"],
        ),
    ).fetchone()["id"]


def test_a_session_can_read_and_triage_its_own_org_but_never_insert(db):
    a = _org_with_deployment(db, "a")
    b = _org_with_deployment(db, "b")
    _report(db, a)
    _report(db, b)

    _set_auth(db, a["user_id"])
    db.execute("set local role authenticated")
    try:
        visible = db.execute("select count(*) as n from public.app_issues").fetchone()
        assert visible["n"] == 1, "a member saw another org's reports"

        db.execute(
            "update public.app_issues set status = 'triaged' where org_id = %s",
            (a["org_id"],),
        )

        # There is no insert policy: a report is only ever written by api's
        # service-role connection after it has validated the key itself.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                "insert into public.app_issues (org_id, project_id, deployment_id,"
                " source, title) values (%s, %s, %s, 'user_report', 'forged')",
                (a["org_id"], a["project_id"], a["deployment_id"]),
            )
    finally:
        db.rollback()


_UPSERT = """
insert into public.app_issues
  (org_id, project_id, deployment_id, source, fingerprint, title)
values (%s, %s, %s, 'automated', %s, %s)
on conflict (deployment_id, fingerprint)
  where fingerprint is not null and status in ('new', 'triaged')
do update set
  occurrence_count = public.app_issues.occurrence_count + 1,
  last_seen_at = now()
returning id, (xmax <> 0) as deduped
"""


def test_a_repeat_increments_one_row_rather_than_opening_another(db):
    a = _org_with_deployment(db, "a")
    args = (a["org_id"], a["project_id"], a["deployment_id"])

    first = db.execute(_UPSERT, (*args, "fp-1", "TypeError: boom")).fetchone()
    assert first["deduped"] is False

    repeat = db.execute(_UPSERT, (*args, "fp-1", "TypeError: boom")).fetchone()
    assert repeat["id"] == first["id"]
    assert repeat["deduped"] is True
    assert (
        db.execute(
            "select occurrence_count as n from public.app_issues where id = %s",
            (first["id"],),
        ).fetchone()["n"]
        == 2
    )

    other = db.execute(_UPSERT, (*args, "fp-2", "RangeError: nope")).fetchone()
    assert other["id"] != first["id"]


def test_the_same_crash_after_it_is_closed_opens_a_fresh_row(db):
    """A regression is a new bug, not a counter ticking on a closed one."""
    a = _org_with_deployment(db, "a")
    args = (a["org_id"], a["project_id"], a["deployment_id"])
    first = db.execute(_UPSERT, (*args, "fp-1", "TypeError: boom")).fetchone()
    db.execute(
        "update public.app_issues set status = 'promoted' where id = %s", (first["id"],)
    )

    again = db.execute(_UPSERT, (*args, "fp-1", "TypeError: boom")).fetchone()
    assert again["id"] != first["id"]
    assert again["deduped"] is False


def test_an_automated_report_must_carry_a_fingerprint(db):
    a = _org_with_deployment(db, "a")
    with pytest.raises(psycopg.errors.CheckViolation):
        _report(db, a, fingerprint=None)
    db.rollback()


def test_a_user_report_needs_no_fingerprint_and_never_dedupes(db):
    a = _org_with_deployment(db, "a")
    one = _report(db, a, source="user_report", fingerprint=None, title="It broke")
    two = _report(db, a, source="user_report", fingerprint=None, title="It broke")
    assert one != two
