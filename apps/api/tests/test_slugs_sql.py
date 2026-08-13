"""US-3.13: live SQL coverage — slugify, shortname/slug generation,
uniqueness, insert triggers, stability on rename.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
All writes roll back — nothing persists.
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


@pytest.fixture(autouse=True)
def rollback(db):
    db.rollback()
    yield
    db.rollback()


def _slugify(db, text, max_len=None):
    return db.execute(
        "select public.slugify(%s, %s) as s", (text, max_len)
    ).fetchone()["s"]


def test_slugify_sanitization(db):
    assert _slugify(db, "Nexdb.io APP") == "nexdb-io-app"
    assert _slugify(db, "  Nikesh, LLC!  ") == "nikesh-llc"
    assert _slugify(db, "already-a-slug") == "already-a-slug"
    assert _slugify(db, "UPPER_case & symbols#") == "upper-case-symbols"
    assert _slugify(db, "---") == "x"  # never empty
    assert _slugify(db, None) == "x"


def test_slugify_truncates_without_trailing_hyphen(db):
    out = _slugify(db, "A Very Long Organization Name Indeed", 24)
    assert len(out) <= 24
    assert not out.endswith("-")


def test_org_shortname_trigger_and_collision_suffix(db):
    name = f"Slug Test Org {uuid.uuid4().hex[:6]}"
    a = db.execute(
        "insert into public.organizations (name) values (%s) returning shortname",
        (name,),
    ).fetchone()["shortname"]
    b = db.execute(
        "insert into public.organizations (name) values (%s) returning shortname",
        (name,),
    ).fetchone()["shortname"]
    assert a == _slugify(db, name, 24)
    assert b == f"{a}-2"
    assert len(b) <= 24


def test_shortname_unique_across_system(db):
    name = f"Unique Org {uuid.uuid4().hex[:6]}"
    row = db.execute(
        "insert into public.organizations (name) values (%s) returning id, shortname",
        (name,),
    ).fetchone()
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "insert into public.organizations (name, shortname) values (%s, %s)",
            (f"{name} clone", row["shortname"]),
        )


def test_project_slug_trigger_scoped_per_org(db):
    org_a = db.execute(
        "insert into public.organizations (name) values ('proj-slug-a') returning id"
    ).fetchone()["id"]
    org_b = db.execute(
        "insert into public.organizations (name) values ('proj-slug-b') returning id"
    ).fetchone()["id"]

    def insert(org, name):
        return db.execute(
            "insert into public.projects (org_id, name, repo_full_name) "
            "values (%s, %s, 'acme/webshop') returning slug",
            (org, name),
        ).fetchone()["slug"]

    assert insert(org_a, "My Shop App") == "my-shop-app"
    assert insert(org_a, "My Shop App") == "my-shop-app-2"  # collision suffix
    # same name in another org: no suffix — uniqueness is per org
    assert insert(org_b, "My Shop App") == "my-shop-app"


def test_slugs_stable_on_rename(db):
    org = db.execute(
        "insert into public.organizations (name) values ('rename-me') "
        "returning id, shortname"
    ).fetchone()
    project = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, 'Original Name', 'acme/webshop') returning id, slug",
        (org["id"],),
    ).fetchone()

    db.execute(
        "update public.organizations set name = 'Renamed Org' where id = %s",
        (org["id"],),
    )
    db.execute(
        "update public.projects set name = 'Renamed Project' where id = %s",
        (project["id"],),
    )
    after_org = db.execute(
        "select shortname from public.organizations where id = %s", (org["id"],)
    ).fetchone()
    after_project = db.execute(
        "select slug from public.projects where id = %s", (project["id"],)
    ).fetchone()
    assert after_org["shortname"] == org["shortname"]
    assert after_project["slug"] == project["slug"]


def test_backfill_left_no_nulls(db):
    assert (
        db.execute(
            "select count(*) as n from public.organizations where shortname is null"
        ).fetchone()["n"]
        == 0
    )
    assert (
        db.execute(
            "select count(*) as n from public.projects where slug is null"
        ).fetchone()["n"]
        == 0
    )
