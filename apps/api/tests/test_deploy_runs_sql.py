"""US-13.13: deploy runs — the rails (protected human-only, production
opt-in flag, single concurrency), the secret-free context shape, and
capability gating via the 13.10 predicate.

Runs against DATABASE_URL (apps/api/.env)."""

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
    """A project, a server, three deployments (uat, protected,
    production-without-flag), and a worker."""
    db.rollback()
    org = db.execute(
        "select org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org")
    org_id = org["org_id"]
    project = db.execute(
        """
        insert into public.projects (org_id, name, repo_full_name)
        values (%s, %s, 'acme/deployrun') returning id
        """,
        (org_id, f"deploy-run-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    server = db.execute(
        """
        insert into public.servers (org_id, name, host, username, auth_method)
        values (%s, 'dr-server', 'dr.example.com', 'deploy', 'password')
        returning id
        """,
        (org_id,),
    ).fetchone()

    def mk_dep(name, environment, protected=False, flag=False):
        return db.execute(
            """
            insert into public.deployments
              (org_id, project_id, server_id, name, branch, target_folder,
               environment, protected, agent_dispatch_allowed)
            values (%s, %s, %s, %s, 'main', '/srv/app', %s, %s, %s)
            returning id
            """,
            (
                org_id,
                project["id"],
                server["id"],
                name,
                environment,
                protected,
                flag,
            ),
        ).fetchone()["id"]

    uat = mk_dep("dr-uat", "uat")
    protected = mk_dep("dr-protected", "uat", protected=True)
    prod = mk_dep("dr-prod", "production")

    token = f"sfw_dp_{uuid.uuid4().hex}"
    worker = db.execute(
        """
        insert into public.workers (org_id, name, type, token_hash, token_last4)
        values (%s, 'deploy-worker', 'autonomous', %s, %s) returning id
        """,
        (org_id, hashlib.sha256(token.encode()).hexdigest(), token[-4:]),
    ).fetchone()
    db.commit()

    yield {
        "org_id": org_id,
        "project_id": project["id"],
        "uat": uat,
        "protected": protected,
        "prod": prod,
        "worker": {
            "id": worker["id"],
            "org_id": org_id,
            "name": "deploy-worker",
            "type": "autonomous",
        },
    }

    db.rollback()
    db.execute("delete from public.workers where id = %s", (worker["id"],))
    db.execute(
        "delete from public.deployments where project_id = %s", (project["id"],)
    )
    db.execute("delete from public.projects where id = %s", (project["id"],))
    db.execute("delete from public.servers where id = %s", (server["id"],))
    db.commit()


def test_protected_deployments_refuse_agent_dispatch(db, settings, ctx):
    out = app_db.dispatch_deploy_run(
        settings, str(ctx["protected"]), str(ctx["org_id"])
    )
    assert "human-only" in out["error"]
    # Defense in depth: the shared refusal helper says the same thing the
    # trigger tool re-checks with.
    bundle = app_db.get_deployment_for_agent(
        settings, str(ctx["protected"]), str(ctx["org_id"])
    )
    assert app_db.agent_deploy_refusal(bundle["deployment"]) is not None


def test_production_needs_the_flag(db, settings, ctx):
    out = app_db.dispatch_deploy_run(
        settings, str(ctx["prod"]), str(ctx["org_id"])
    )
    assert "agent may deploy" in out["error"]
    db.execute(
        "update public.deployments set agent_dispatch_allowed = true "
        "where id = %s",
        (ctx["prod"],),
    )
    db.commit()
    out = app_db.dispatch_deploy_run(
        settings, str(ctx["prod"]), str(ctx["org_id"]), auto_rollback=True
    )
    assert out.get("run_id"), out
    db.execute("delete from public.runs where id = %s", (out["run_id"],))
    db.commit()


def test_dispatch_context_is_secret_free_and_single_flight(db, settings, ctx):
    out = app_db.dispatch_deploy_run(
        settings,
        str(ctx["uat"]),
        str(ctx["org_id"]),
        ref="v1.2.3",
        auto_rollback=True,
    )
    assert out.get("run_id"), out
    run_id = out["run_id"]
    try:
        row = db.execute(
            "select kind, issue_id, deployment_id, input_context "
            "from public.runs where id = %s",
            (run_id,),
        ).fetchone()
        assert row["kind"] == "deploy" and row["issue_id"] is None
        assert str(row["deployment_id"]) == str(ctx["uat"])
        ic = row["input_context"]
        # AC: definition, ref and rollback authorization — provably no
        # credential or secret value.
        assert set(ic.keys()) == {
            "run_kind",
            "deployment_id",
            "deployment",
            "project_name",
            "repo_full_name",
            "ref",
            "auto_rollback",
            "release_branch",  # us-50.4: a branch name, not a secret
        }
        assert set(ic["deployment"].keys()) <= {
            "name",
            "environment",
            "server_name",
            "branch",
            "strategy",
            "website_url",
            "health_check_url",
            "kind",           # us-50.1: internal vs external
            "target_branch",  # us-50.2: an external deploy ships by merging
        }
        assert ic["ref"] == "v1.2.3" and ic["auto_rollback"] is True

        dup = app_db.dispatch_deploy_run(
            settings, str(ctx["uat"]), str(ctx["org_id"])
        )
        assert "already queued or running" in dup["error"]
    finally:
        db.execute("delete from public.runs where id = %s", (run_id,))
        db.commit()


def test_deploy_runs_gate_on_the_deploy_checkbox(db, settings, ctx):
    """US-55.1: access is the project half; whether this agent does 'deploy'
    work is its own enabled_kinds checkbox."""
    out = app_db.dispatch_deploy_run(
        settings, str(ctx["uat"]), str(ctx["org_id"])
    )
    run_id = out["run_id"]
    try:
        db.execute(
            """
            insert into public.worker_capabilities
              (worker_id, project_id, org_id, capability)
            values (%s, %s, %s, 'access')
            """,
            (ctx["worker"]["id"], ctx["project_id"], ctx["org_id"]),
        )
        db.execute(
            """
            insert into public.runner_config (worker_id, org_id, enabled_kinds)
            values (%s, %s, '["code"]'::jsonb)
            on conflict (worker_id)
              do update set enabled_kinds = excluded.enabled_kinds
            """,
            (ctx["worker"]["id"], ctx["org_id"]),
        )
        db.commit()
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert run_id not in pool_ids  # deploy unchecked
        db.execute(
            "update public.runner_config set enabled_kinds = "
            "'[\"code\",\"deploy\"]'::jsonb where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.commit()
        pool_ids = {
            str(r["id"]) for r in app_db.list_worker_pool(settings, ctx["worker"])
        }
        assert run_id in pool_ids
    finally:
        db.rollback()
        db.execute(
            "delete from public.runner_config where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.execute(
            "delete from public.worker_capabilities where worker_id = %s",
            (ctx["worker"]["id"],),
        )
        db.execute("delete from public.runs where id = %s", (run_id,))
        db.commit()


def test_flag_flip_is_audited(db, settings, ctx):
    db.execute(
        "update public.deployments set agent_dispatch_allowed = true "
        "where id = %s",
        (ctx["uat"],),
    )
    db.commit()
    event = db.execute(
        """
        select areas, detail from public.deployment_events
        where deployment_id = %s and event = 'updated'
        order by id desc limit 1
        """,
        (ctx["uat"],),
    ).fetchone()
    assert event is not None
    assert "agent-dispatch" in event["areas"]
    assert event["detail"]["agent_dispatch_allowed"] is True
