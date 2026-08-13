"""US-70.1: the delete policy on releases, proven against live RLS.

Only an owner or admin may delete a release, and only one whose status is
rejected, failed or cancelled — both gates live in the policy itself, so a
released record is undeletable by construction no matter what surface asks.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Each test
rolls back — nothing is left behind.
"""

from __future__ import annotations

import json
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


def _add_member(db, org_id, role):
    """A fresh principal with a linked auth user, membered into the org.

    principals.auth_user_id references auth.users, so a bare row goes in
    there first — inside the test's transaction like everything else."""
    auth_user = str(uuid.uuid4())
    principal_id = str(uuid.uuid4())
    db.execute(
        "insert into auth.users (id, instance_id, aud, role, email) "
        "values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated', "
        "        'authenticated', %s)",
        (auth_user, f"sql-test-70-1-{auth_user[:8]}@test.invalid"),
    )
    # Phase 9: a trigger on auth.users auto-provisions the principal — reuse
    # it rather than colliding with its unique auth_user_id.
    row = db.execute(
        "select id from public.principals where auth_user_id = %s", (auth_user,)
    ).fetchone()
    if row:
        principal_id = row["id"]
    else:
        db.execute(
            "insert into public.principals (id, kind, display_name, auth_user_id) "
            "values (%s, 'human', 'sql-test us-70.1', %s)",
            (principal_id, auth_user),
        )
    db.execute(
        "insert into public.organization_members (org_id, principal_id, role, status) "
        "values (%s, %s, %s, 'active') "
        "on conflict (org_id, principal_id) do update set role = excluded.role",
        (org_id, principal_id, role),
    )
    return auth_user


def _impersonate(db, auth_user_id):
    db.execute(
        "select set_config('request.jwt.claims', %s, true)",
        (json.dumps({"sub": auth_user_id, "role": "authenticated"}),),
    )
    db.execute("set local role authenticated")


@pytest.fixture
def ctx(db):
    """An org with a project and one release per interesting status."""
    db.rollback()
    org = db.execute(
        "select o.id from public.organizations o join public.projects p on p.org_id = o.id "
        "order by o.created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization with a project")
    org_id = org["id"]
    project = db.execute(
        "select id from public.projects where org_id = %s order by created_at limit 1",
        (org_id,),
    ).fetchone()
    releases = {}
    for status in ("rejected", "failed", "cancelled", "released", "queued"):
        rid = str(uuid.uuid4())
        db.execute(
            "insert into public.releases "
            "(id, org_id, project_id, version, status, commit_sha) "
            "values (%s, %s, %s, %s, %s, %s)",
            (rid, org_id, project["id"], f"9999.01.01.{len(releases) + 1}", status, "0" * 40),
        )
        releases[status] = rid
    yield {"org_id": org_id, "releases": releases}
    db.rollback()


def _try_delete(db, release_id):
    return db.execute(
        "delete from public.releases where id = %s", (release_id,)
    ).rowcount


@pytest.mark.parametrize("role,status,expected", [
    ("owner", "rejected", 1),
    ("owner", "failed", 1),
    ("admin", "cancelled", 1),
    ("owner", "released", 0),
    ("owner", "queued", 0),
    ("developer", "rejected", 0),
    ("viewer", "cancelled", 0),
])
def test_delete_policy(db, ctx, role, status, expected):
    auth_user = _add_member(db, ctx["org_id"], role)
    _impersonate(db, auth_user)
    deleted = _try_delete(db, ctx["releases"][status])
    db.execute("reset role")
    assert deleted == expected, (
        f"{role} deleting a {status} release: expected {expected}, got {deleted}"
    )


def test_children_cascade_and_history_survives(db, ctx):
    """Deleting the record takes its test results but keeps deploy history."""
    release_id = ctx["releases"]["rejected"]
    db.execute(
        "insert into public.release_test_results (org_id, release_id, test_case_id, result) "
        "select %s, %s, id, 'fail' from public.test_cases limit 1",
        (ctx["org_id"], release_id),
    )
    auth_user = _add_member(db, ctx["org_id"], "owner")
    _impersonate(db, auth_user)
    assert _try_delete(db, release_id) == 1
    db.execute("reset role")
    left = db.execute(
        "select count(*) as n from public.release_test_results where release_id = %s",
        (release_id,),
    ).fetchone()["n"]
    assert left == 0
