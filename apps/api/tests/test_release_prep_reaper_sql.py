"""US-103.1: an abandoned release prep fails itself, proven against Postgres.

Release prep was the one claimed job in the factory with no lease reaper.
`requeue_expired_claims` sweeps `runs`; nothing read
`release_prep_runs.claim_expires_at`. Release 2026.08.16.3 therefore sat
`running` for two and a half hours with an expired lease and a healthy worker
online, blocking every future cut for the project through migration 215's
in-flight uniqueness index, until it was cleared by editing production.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Each test
cleans up after itself — nothing is left behind.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

from app import db as app_db
from app.config import Settings

# Every in-flight status per migration 215 — the set that blocks a fresh cut.
IN_FLIGHT = (
    "queued",
    "running",
    "notes-ready",
    "deploying",
    "uat-deployed",
    "uat-deploy-failed",
    "uat-signed-off",
    "promoting",
)


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
    conn.rollback()
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
    """A project, a worker, and a release with one prep — torn down after.

    Committed rather than rolled back: the reaper opens its own connection
    through the pool, so it cannot see an uncommitted transaction.
    """
    db.rollback()
    project = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not project:
        pytest.skip("no project")
    org_id, project_id = project["org_id"], project["id"]

    token = f"sfw_reap_{uuid.uuid4().hex}"
    worker = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'reaper-test-worker', 'autonomous', %s, %s) returning id
        """,
        (org_id, hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()

    release_id = str(uuid.uuid4())
    db.execute(
        """
        insert into public.releases (id, org_id, project_id, version, status, commit_sha)
        values (%s, %s, %s, %s, 'running', %s)
        """,
        (release_id, org_id, project_id, f"9998.01.01.{uuid.uuid4().hex[:6]}", "0" * 40),
    )
    db.commit()
    yield {
        "org_id": org_id,
        "project_id": project_id,
        "worker_id": worker["id"],
        "release_id": release_id,
    }
    db.rollback()
    db.execute("delete from public.releases where id = %s", (release_id,))
    db.execute("delete from public.workers where id = %s", (worker["id"],))
    db.commit()


def _prep(db, ctx, *, status="running", lease="-30 minutes", held="2 hours"):
    prep_id = str(uuid.uuid4())
    db.execute(
        """
        insert into public.release_prep_runs
          (id, org_id, project_id, release_id, status, worker_id,
           claimed_at, claim_expires_at)
        values (%s, %s, %s, %s, %s, %s, now() - %s::interval, now() + %s::interval)
        """,
        (
            prep_id,
            ctx["org_id"],
            ctx["project_id"],
            ctx["release_id"],
            status,
            ctx["worker_id"],
            held,
            lease,
        ),
    )
    db.commit()
    return prep_id


def _row(db, table, row_id):
    db.rollback()  # see the reaper's committed work, not our snapshot
    return db.execute(
        f"select * from public.{table} where id = %s", (row_id,)
    ).fetchone()


def test_an_expired_prep_is_reaped_and_the_release_fails(db, settings, ctx):
    """The 2026.08.16.3 shape exactly: lease 30 minutes gone, held 2 hours."""
    prep_id = _prep(db, ctx)

    reaped = app_db.reap_expired_release_preps(settings)

    assert prep_id in [r["prep_id"] for r in reaped]
    prep = _row(db, "release_prep_runs", prep_id)
    assert prep["status"] == "failed"
    assert prep["finished_at"] is not None
    # AC3: the honest sentence — not a fabricated agent failure.
    assert "stopped reporting" in prep["error"]
    assert "reaper-test-worker" in prep["error"]

    release = _row(db, "releases", ctx["release_id"])
    assert release["status"] == "failed"
    assert "No notes were written" in release["failure_reason"]


def test_a_live_prep_is_never_reaped(db, settings, ctx):
    """A prep whose heartbeat is current survives every sweep."""
    prep_id = _prep(db, ctx, lease="+2 hours", held="10 minutes")

    app_db.reap_expired_release_preps(settings)

    prep = _row(db, "release_prep_runs", prep_id)
    assert prep["status"] == "running"
    assert prep["error"] is None
    assert _row(db, "releases", ctx["release_id"])["status"] == "running"


def test_a_heartbeat_saves_a_prep_from_the_sweep(db, settings, ctx):
    """The lease is re-armed by heartbeating — us-103.2's restarted runner
    stays out of the reaper by doing exactly this."""
    prep_id = _prep(db, ctx)
    assert app_db.heartbeat_release_prep(settings, prep_id, str(ctx["worker_id"]))

    app_db.reap_expired_release_preps(settings)

    assert _row(db, "release_prep_runs", prep_id)["status"] == "running"


@pytest.mark.parametrize("status", ["succeeded", "failed", "cancelled", "queued"])
def test_only_running_preps_are_reaped(db, settings, ctx, status):
    prep_id = _prep(db, ctx, status=status)

    app_db.reap_expired_release_preps(settings)

    assert _row(db, "release_prep_runs", prep_id)["status"] == status


def test_the_reaped_release_is_out_of_the_in_flight_set(db, settings, ctx):
    """AC5: the point of the whole story. While it counted as in flight, the
    uniqueness index blocked every future cut for the project."""
    _prep(db, ctx)

    app_db.reap_expired_release_preps(settings)

    db.rollback()
    blocking = db.execute(
        "select count(*) as n from public.releases "
        "where project_id = %s and status = any(%s)",
        (ctx["project_id"], list(IN_FLIGHT)),
    ).fetchone()
    assert blocking["n"] == 0
    # And the index itself now permits a new in-flight release.
    fresh = str(uuid.uuid4())
    db.execute(
        "insert into public.releases (id, org_id, project_id, version, status, commit_sha) "
        "values (%s, %s, %s, %s, 'queued', %s)",
        (fresh, ctx["org_id"], ctx["project_id"], f"9997.01.01.{uuid.uuid4().hex[:6]}", "0" * 40),
    )
    db.execute("delete from public.releases where id = %s", (fresh,))
    db.commit()


def test_the_reaped_release_satisfies_retrys_guards(db, settings, ctx):
    """`/releases/{id}/retry` accepts `failed` with notes never written, and
    dispatch_release_prep_for refuses while any prep is queued or running —
    so the reap has to leave BOTH true for one click to be the way out."""
    _prep(db, ctx)

    app_db.reap_expired_release_preps(settings)

    release = _row(db, "releases", ctx["release_id"])
    assert release["status"] == "failed"
    assert release["notes_written_at"] is None
    assert release["promoted_at"] is None and release["released_at"] is None
    live = db.execute(
        "select count(*) as n from public.release_prep_runs "
        "where release_id = %s and status in ('queued', 'running')",
        (ctx["release_id"],),
    ).fetchone()
    assert live["n"] == 0


def test_the_sweep_is_idempotent(db, settings, ctx):
    """It runs at startup, every 60 seconds, and on every pool listing."""
    _prep(db, ctx)

    first = app_db.reap_expired_release_preps(settings)
    second = app_db.reap_expired_release_preps(settings)

    assert len(first) >= 1
    assert second == []


def test_held_preps_are_visible_to_their_own_worker_only(db, settings, ctx):
    """US-103.2: the query the runner could not ask."""
    prep_id = _prep(db, ctx, lease="+2 hours", held="10 minutes")

    mine = app_db.list_held_release_preps(
        settings, str(ctx["worker_id"]), str(ctx["org_id"])
    )
    assert prep_id in [str(r["id"]) for r in mine]

    someone_else = app_db.list_held_release_preps(
        settings, str(uuid.uuid4()), str(ctx["org_id"])
    )
    assert someone_else == []


def test_a_reaped_prep_is_no_longer_held(db, settings, ctx):
    """AC4 of us-103.2: re-adoption never resurrects a prep the sweep ended."""
    _prep(db, ctx)

    app_db.reap_expired_release_preps(settings)

    assert (
        app_db.list_held_release_preps(
            settings, str(ctx["worker_id"]), str(ctx["org_id"])
        )
        == []
    )
