"""US-3.12 → US-13.10 → US-55.1: live SQL coverage — project access.

Migration 199 retired the per-project × kind matrix: a worker_capabilities
row now means ACCESS to the project (canonical capability = 'access'; the
seven historical kind values stay legal and mean the same access), and what
an agent may DO is its own runner_config.enabled_kinds checkboxes — null =
every kind, [] = benched. worker_has_grant answers both halves at once.
Preserved from us-31.3: fail-closed (zero rows = nothing, at the pool, the
claim gate and the git clone gate); cross-org isolation via composite FKs.

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

from app import db as app_db
from app.config import Settings


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
    """Two fresh projects in the org, one worker. Both projects are created
    here: US-86.1's serial law holds everything staged in the org's
    long-lived first project behind whatever that project has in flight."""
    db.rollback()
    org = db.execute(
        "select org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no project")

    project = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/main') returning id
        """,
        (org["org_id"], f"cap-test-main {uuid.uuid4().hex[:6]}"),
    ).fetchone()

    other = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/other') returning id
        """,
        (org["org_id"], f"cap-test-other {uuid.uuid4().hex[:6]}"),
    ).fetchone()

    token = f"sfw_cap_{uuid.uuid4().hex}"
    worker = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'cap-test-worker', 'autonomous', %s, %s) returning id
        """,
        (
            org["org_id"],
            hashlib.sha256(token.encode()).hexdigest(),
            token[-4:],
        ),
    ).fetchone()
    db.commit()

    yield {
        "org_id": org["org_id"],
        "project_a": project["id"],
        "project_b": other["id"],
        "worker": {
            "id": worker["id"],
            "org_id": org["org_id"],
            "name": "cap-test-worker",
            "type": "autonomous",
        },
    }

    db.rollback()
    db.execute(
        "delete from public.runner_config where worker_id = %s", (worker["id"],)
    )
    db.execute("delete from public.workers where id = %s", (worker["id"],))
    db.execute("delete from public.projects where id = %s", (other["id"],))
    db.execute("delete from public.projects where id = %s", (project["id"],))
    db.commit()


def _insert_issue_and_run(db, ctx, project_id, kind="code", issue_status="queued"):
    issue_id, run_id = uuid.uuid4(), uuid.uuid4()
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria, status)
        values (%s, %s, %s, 'story', %s, 'body', '["ok"]'::jsonb, %s)
        """,
        (issue_id, ctx["org_id"], project_id, f"cap-test {issue_id}", issue_status),
    )
    db.execute(
        """
        insert into public.runs
          (id, org_id, issue_id, provider, status, kind, input_context)
        values (%s, %s, %s, 'claude', 'queued', %s, '{}'::jsonb)
        """,
        (run_id, ctx["org_id"], issue_id, kind),
    )
    db.commit()
    return issue_id, run_id


def _cleanup_issue(db, issue_id):
    db.rollback()
    db.execute(
        "update public.issues set status = 'failed' where id = %s", (issue_id,)
    )
    db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def _grant_access(db, ctx, project_id, capability="access"):
    """One access row — the canonical shape since migration 199. A legacy
    kind value may be passed to prove old rows still mean access."""
    db.execute(
        """
        insert into public.worker_capabilities
          (worker_id, project_id, org_id, capability)
        values (%s, %s, %s, %s)
        on conflict (worker_id, project_id, capability) do nothing
        """,
        (ctx["worker"]["id"], project_id, ctx["org_id"], capability),
    )
    db.commit()


def _set_kinds(db, ctx, kinds):
    """The agent-level checkboxes; None = every kind (us-53.4)."""
    import json

    db.execute(
        """
        insert into public.runner_config (worker_id, org_id, enabled_kinds)
        values (%s, %s, %s::jsonb)
        on conflict (worker_id)
          do update set enabled_kinds = excluded.enabled_kinds
        """,
        (
            ctx["worker"]["id"],
            ctx["org_id"],
            json.dumps(kinds) if kinds is not None else None,
        ),
    )
    db.commit()


def test_zero_rows_means_no_work_and_no_clone(db, settings, ctx):
    """US-31.3: the inversion. Zero grant rows used to mean UNRESTRICTED — at
    the pool listing, the claim gate, and the git-proxy clone gate — so a
    freshly provisioned agent could claim work in and clone the repository of
    every project in the org. It now means nothing at all, at all three."""
    issue_id, run_id = _insert_issue_and_run(db, ctx, ctx["project_a"])
    try:
        pool = app_db.list_worker_pool(settings, ctx["worker"])
        assert str(run_id) not in {str(r["id"]) for r in pool}
        assert not app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), str(run_id)
        )
        # The clone gate is the one nobody was thinking about, and the reason
        # this was org-wide repository read access rather than misrouted work.
        assert not app_db.worker_allowed_for_project(
            settings, str(ctx["worker"]["id"]), str(ctx["project_a"])
        )
        # And the refusal names WHICH half is missing.
        reason = app_db.worker_run_refusal(
            settings, str(ctx["worker"]["id"]), str(run_id)
        )
        assert reason and "does not have access to that project" in reason
    finally:
        _cleanup_issue(db, issue_id)


def test_first_row_flips_to_allow_list_and_git_gate_is_project_level(
    db, settings, ctx
):
    issue_a, run_a = _insert_issue_and_run(db, ctx, ctx["project_a"])
    issue_b, run_b = _insert_issue_and_run(db, ctx, ctx["project_b"])
    try:
        _grant_access(db, ctx, ctx["project_b"])
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert str(run_b) in pool_ids
        assert str(run_a) not in pool_ids
        assert not app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), str(run_a)
        )
        # Clone/fetch stays project-level: ANY capability row on the
        # project grants it; none denies it.
        assert not app_db.worker_allowed_for_project(
            settings, str(ctx["worker"]["id"]), str(ctx["project_a"])
        )
        assert app_db.worker_allowed_for_project(
            settings, str(ctx["worker"]["id"]), str(ctx["project_b"])
        )
    finally:
        _cleanup_issue(db, issue_a)
        _cleanup_issue(db, issue_b)


def test_access_inherits_the_agents_checkboxes(db, settings, ctx):
    """US-55.1 headline: one access row + the agent-level checkboxes decide
    kinds everywhere. Null checkboxes = every kind; a list = exactly those;
    [] = benched — with the access row untouched throughout."""
    runs_by_kind = {}
    issues = []
    for kind in ("prd", "breakdown", "plan", "code"):
        # Issues parked at 'ready' (not yet dispatched, so no queue position):
        # US-86.1 offers only one QUEUED unit per project, and this test needs
        # all four runs simultaneously visible so the checkbox gating — its
        # actual subject — stays observable.
        issue_id, run_id = _insert_issue_and_run(
            db, ctx, ctx["project_a"], kind, issue_status="ready"
        )
        issues.append(issue_id)
        runs_by_kind[kind] = run_id
    try:
        _grant_access(db, ctx, ctx["project_a"])

        def offered():
            pool_ids = {
                str(r["id"])
                for r in app_db.list_worker_pool(settings, ctx["worker"])
            }
            return {k for k, r in runs_by_kind.items() if str(r) in pool_ids}

        assert offered() == {"prd", "breakdown", "plan", "code"}  # null = all

        _set_kinds(db, ctx, ["breakdown"])
        assert offered() == {"breakdown"}
        assert app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), str(runs_by_kind["breakdown"])
        )
        assert not app_db.worker_allowed_for_run(
            settings, str(ctx["worker"]["id"]), str(runs_by_kind["code"])
        )
        # The git gate is access-level and ignores the checkboxes.
        assert app_db.worker_allowed_for_project(
            settings, str(ctx["worker"]["id"]), str(ctx["project_a"])
        )

        _set_kinds(db, ctx, [])  # benched
        assert offered() == set()

        _set_kinds(db, ctx, None)  # back to every kind
        assert offered() == {"prd", "breakdown", "plan", "code"}
    finally:
        for issue_id in issues:
            _cleanup_issue(db, issue_id)


def test_a_legacy_kind_row_still_means_access(db, settings, ctx):
    """Rows written before migration 199 (or by an old template) carry a kind
    name — they grant the same access, and the kind value gates nothing."""
    issue_id, run_id = _insert_issue_and_run(db, ctx, ctx["project_a"], "code")
    try:
        _grant_access(db, ctx, ctx["project_a"], capability="deploy")
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert str(run_id) in pool_ids  # access, not a 'deploy' restriction
    finally:
        _cleanup_issue(db, issue_id)


def test_unknown_capability_is_rejected(db, settings, ctx):
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            """
            insert into public.worker_capabilities
              (worker_id, project_id, org_id, capability)
            values (%s, %s, %s, 'refactor')
            """,
            (ctx["worker"]["id"], ctx["project_a"], ctx["org_id"]),
        )
    db.rollback()


def test_capability_changes_are_audited_per_grant(db, settings, ctx):
    _grant_access(db, ctx, ctx["project_a"])
    _grant_access(db, ctx, ctx["project_b"])
    db.execute(
        "delete from public.worker_capabilities "
        "where worker_id = %s and project_id = %s",
        (ctx["worker"]["id"], ctx["project_a"]),
    )
    db.commit()
    events = db.execute(
        """
        select event, detail from public.worker_capability_events
        where worker_id = %s order by id
        """,
        (ctx["worker"]["id"],),
    ).fetchall()
    assert [e["event"] for e in events] == ["granted", "granted", "revoked"]
    assert events[0]["detail"]["capability"] == "access"
    assert events[0]["detail"]["project_id"] == str(ctx["project_a"])
    assert events[2]["detail"]["capability"] == "access"


def test_capability_row_cannot_reference_foreign_org(db, settings, ctx):
    foreign_org = db.execute(
        "select id from public.organizations where id <> %s order by id limit 1",
        (ctx["org_id"],),
    ).fetchone()
    if not foreign_org:
        pytest.skip("single-org database")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            """
            insert into public.worker_capabilities
              (worker_id, project_id, org_id, capability)
            values (%s, %s, %s, 'code')
            """,
            (ctx["worker"]["id"], ctx["project_a"], foreign_org["id"]),
        )
    db.rollback()
