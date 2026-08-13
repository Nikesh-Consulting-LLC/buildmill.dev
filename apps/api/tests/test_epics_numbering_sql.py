"""US-7.10: live SQL coverage — epics as the numbering root. Covers epic
auto-seed on project insert, atomic item_no/sub_no assignment (top-level,
nested, standalone), mandatory epic linkage default-to-active, one-active-epic
enforcement, and the server-side start-new-epic gate + close/activate.

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
def project(db):
    db.rollback()
    org = db.execute(
        "select org_id as id from public.projects order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no org in database")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/epic-test') returning id, org_id",
        (org["id"], f"epic-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    yield row
    db.rollback()
    db.execute("delete from public.issues where project_id = %s", (row["id"],))
    db.execute("delete from public.epics where project_id = %s", (row["id"],))
    db.execute("delete from public.projects where id = %s", (row["id"],))
    db.commit()


def _active_epic(db, project):
    return db.execute(
        "select id, number from public.epics where project_id = %s and active",
        (project["id"],),
    ).fetchone()


def _new_issue(db, project, *, title, type_, parent_id=None):
    return db.execute(
        "insert into public.issues (org_id, project_id, title, type, parent_id) "
        "values (%s, %s, %s, %s, %s) returning id, epic_id, item_no, sub_no",
        (project["org_id"], project["id"], title, type_, parent_id),
    ).fetchone()


def test_new_project_seeds_active_epic_1(db, project):
    epic = _active_epic(db, project)
    assert epic is not None
    assert epic["number"] == 1


def test_toplevel_items_get_sequential_item_no(db, project):
    a = _new_issue(db, project, title="feat a", type_="feature")
    b = _new_issue(db, project, title="bug b", type_="bug")
    c = _new_issue(db, project, title="chore c", type_="chore")
    db.commit()
    assert (a["item_no"], a["sub_no"]) == (1, None)
    assert (b["item_no"], b["sub_no"]) == (2, None)
    assert (c["item_no"], c["sub_no"]) == (3, None)
    # all landed in the active epic
    epic = _active_epic(db, project)
    for r in (a, b, c):
        assert r["epic_id"] == epic["id"]


def test_stories_under_feature_get_nested_sub_no(db, project):
    feat = _new_issue(db, project, title="parent feature", type_="feature")
    s1 = _new_issue(db, project, title="story 1", type_="story", parent_id=feat["id"])
    s2 = _new_issue(db, project, title="story 2", type_="story", parent_id=feat["id"])
    db.commit()
    # children carry the feature's item_no plus their own sub sequence
    assert s1["item_no"] == feat["item_no"]
    assert s1["sub_no"] == 1
    assert s2["item_no"] == feat["item_no"]
    assert s2["sub_no"] == 2
    # standalone story after: next top-level item_no, no sub
    standalone = _new_issue(db, project, title="standalone", type_="story")
    db.commit()
    assert standalone["item_no"] == feat["item_no"] + 1
    assert standalone["sub_no"] is None


def test_mandatory_linkage_defaults_to_active_epic(db, project):
    epic = _active_epic(db, project)
    row = _new_issue(db, project, title="unlinked", type_="story")
    db.commit()
    assert row["epic_id"] == epic["id"]


def test_one_active_epic_enforced(db, project):
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "insert into public.epics (org_id, project_id, title, status, active) "
            "values (%s, %s, 'Epic X', 'open', true)",
            (project["org_id"], project["id"]),
        )
    db.rollback()


def test_start_new_epic_blocked_then_allowed(db, project):
    epic1 = _active_epic(db, project)
    # An open work item blocks the gate.
    issue = _new_issue(db, project, title="open work", type_="story")
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("select public.start_new_epic(%s)", (project["id"],))
    db.rollback()

    # Complete it → gate opens, epic 1 closes, epic 2 activates.
    db.execute(
        "update public.issues set status = 'done' where id = %s", (issue["id"],)
    )
    db.commit()
    db.execute("select public.start_new_epic(%s)", (project["id"],))
    db.commit()
    epic2 = _active_epic(db, project)
    assert epic2["number"] == epic1["number"] + 1
    closed = db.execute(
        "select status, active from public.epics where id = %s", (epic1["id"],)
    ).fetchone()
    assert closed["status"] == "completed"
    assert closed["active"] is False
    # A new item now lands in epic 2 with a fresh item_no sequence.
    fresh = _new_issue(db, project, title="in epic 2", type_="story")
    db.commit()
    assert fresh["epic_id"] == epic2["id"]
    assert fresh["item_no"] == 1


def test_abandoned_items_do_not_block_gate(db, project):
    _new_issue(db, project, title="abandoned", type_="story")
    db.execute(
        "update public.issues set abandoned_at = now() "
        "where project_id = %s and title = 'abandoned'",
        (project["id"],),
    )
    db.commit()
    # No non-abandoned open items → gate passes.
    db.execute("select public.start_new_epic(%s)", (project["id"],))
    db.commit()
    epic = _active_epic(db, project)
    assert epic["number"] == 2
