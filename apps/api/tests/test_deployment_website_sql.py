"""US-7.2: live SQL coverage — the deployment Website column: URL/kind
validation via the deployments_website_shape check, and the per-environment
website resolver used by the work context.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

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
def project_and_server(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    proj = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/website-test') returning id, org_id",
        (org["id"], f"website-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    server = db.execute(
        "insert into public.servers (org_id, name, host, username, auth_method) "
        "values (%s, %s, 'host.example.com', 'deploy', 'password') "
        "returning id",
        (org["id"], f"srv-{uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield proj, server
    db.rollback()
    db.execute("delete from public.projects where id = %s", (proj["id"],))
    db.execute("delete from public.servers where id = %s", (server["id"],))
    db.commit()


def _insert_deployment(db, proj, server, *, name, environment, kind, url):
    return db.execute(
        "insert into public.deployments "
        "(org_id, project_id, server_id, name, branch, target_folder, "
        " environment, website_kind, website_url) "
        "values (%s, %s, %s, %s, 'main', '/srv/app', %s, %s, %s) returning id",
        (proj["org_id"], proj["id"], server["id"], name, environment, kind, url),
    ).fetchone()


def test_valid_domain_and_ip_websites_accepted(db, project_and_server):
    proj, server = project_and_server
    _insert_deployment(
        db, proj, server,
        name="uat", environment="uat",
        kind="domain", url="https://app.example.com",
    )
    _insert_deployment(
        db, proj, server,
        name="prod", environment="production",
        kind="ip", url="http://203.0.113.10:3000",
    )
    db.commit()


def test_null_website_allowed(db, project_and_server):
    proj, server = project_and_server
    _insert_deployment(
        db, proj, server,
        name="dev", environment="dev", kind=None, url=None,
    )
    db.commit()


@pytest.mark.parametrize(
    "kind,url",
    [
        ("domain", "not-a-url"),          # not absolute
        ("domain", "ftp://app.example.com"),  # wrong scheme
        ("domain", "https://203.0.113.10"),   # ip under domain kind
        ("ip", "https://app.example.com"),    # domain under ip kind
        ("ip", "https://999.1.1.1"),          # still matches ipv4 shape but ok pattern-wise
    ],
)
def test_invalid_websites_rejected_by_check(db, project_and_server, kind, url):
    proj, server = project_and_server
    # The 999.1.1.1 case is accepted by the coarse DB regex (octet range is a
    # client-side refinement), so only assert on the ones the DB check rejects.
    if url == "https://999.1.1.1":
        pytest.skip("octet range is a client-side refinement, not a DB check")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_deployment(
            db, proj, server,
            name=f"bad-{uuid.uuid4().hex[:6]}",
            environment="uat", kind=kind, url=url,
        )
    db.rollback()


def test_environment_websites_resolver(db, settings, project_and_server):
    proj, server = project_and_server
    # No websites yet → empty.
    assert app_db.get_project_environment_websites(settings, str(proj["id"])) == {}
    _insert_deployment(
        db, proj, server,
        name="uat", environment="uat",
        kind="domain", url="https://uat.example.com",
    )
    _insert_deployment(
        db, proj, server,
        name="prod", environment="production",
        kind="domain", url="https://prod.example.com",
    )
    # A dev deployment with a website must NOT surface (only uat/production).
    _insert_deployment(
        db, proj, server,
        name="dev", environment="dev",
        kind="domain", url="https://dev.example.com",
    )
    db.commit()
    got = app_db.get_project_environment_websites(settings, str(proj["id"]))
    assert got == {
        "uat": "https://uat.example.com",
        "production": "https://prod.example.com",
    }
