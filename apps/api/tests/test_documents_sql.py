"""US-2.21/2.22: live SQL coverage — dispatch context documents key,
link CHECK, and the detach-normalize trigger.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
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


@pytest.fixture
def project(db):
    db.rollback()
    row = db.execute(
        "select id, org_id from public.projects order by created_at limit 1"
    ).fetchone()
    if not row:
        pytest.skip("no project in database")
    return row


def _insert_issue(db, project, **extra):
    issue_id = uuid.uuid4()
    cols = {
        "id": issue_id,
        "org_id": project["org_id"],
        "project_id": project["id"],
        "type": "story",
        "title": f"doc-sql-test {issue_id}",
        "body": "body",
        "acceptance_criteria": json.dumps(["ok"]),
        "status": "draft",
        "parent_id": None,
    }
    cols.update(extra)
    db.execute(
        """
        insert into public.issues
          (id, org_id, project_id, type, title, body, acceptance_criteria,
           status, parent_id)
        values (%(id)s, %(org_id)s, %(project_id)s, %(type)s, %(title)s,
                %(body)s, %(acceptance_criteria)s::jsonb, %(status)s, %(parent_id)s)
        """,
        cols,
    )
    db.commit()
    return issue_id


def _insert_document(db, project, *, name, attached_to, issue_id=None):
    doc_id = uuid.uuid4()
    db.execute(
        """
        insert into public.documents
          (id, org_id, project_id, issue_id, name, mime_type, size_bytes,
           storage_path, source, attached_to)
        values (%s, %s, %s, %s, %s, 'image/svg+xml', 5, %s, 'user', %s)
        """,
        (
            doc_id,
            project["org_id"],
            project["id"],
            issue_id,
            name,
            f"{project['org_id']}/projects/{project['id']}/{doc_id}/{name}",
            attached_to,
        ),
    )
    db.commit()
    return doc_id


def _cleanup(db, issue_ids=(), doc_ids=()):
    db.rollback()
    for doc_id in doc_ids:
        db.execute("delete from public.documents where id = %s", (doc_id,))
    for issue_id in issue_ids:
        db.execute(
            """
            update public.issues set status = 'draft'
            where id = %s and status in ('queued', 'running', 'planning')
            """,
            (issue_id,),
        )
        db.execute("delete from public.runs where issue_id = %s", (issue_id,))
        db.execute("delete from public.issues where id = %s", (issue_id,))
    db.commit()


def test_dispatch_context_includes_work_item_documents(db, project):
    issue_id = _insert_issue(db, project, status="draft")
    doc_id = _insert_document(
        db, project, name="notes.svg", attached_to="work-item", issue_id=issue_id
    )
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        docs = ctx.get("documents")
        assert isinstance(docs, list) and len(docs) == 1
        assert docs[0]["name"] == "notes.svg"
        assert docs[0]["attached_to"] == "work-item"
        assert docs[0]["id"] == str(doc_id)
        assert "mime_type" in docs[0] and "size_bytes" in docs[0]
    finally:
        _cleanup(db, issue_ids=[issue_id], doc_ids=[doc_id])


def test_dispatch_context_includes_parent_prd_documents(db, project):
    """US-2.22: a story with a parent feature receives the feature's
    PRD-linked documents, marked with their link kind."""
    feature_id = _insert_issue(
        db, project, type="feature", status="ready", acceptance_criteria="[]"
    )
    story_id = _insert_issue(db, project, status="draft", parent_id=feature_id)
    doc_id = _insert_document(
        db, project, name="wireframe.svg", attached_to="prd", issue_id=feature_id
    )
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (story_id,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        docs = ctx.get("documents")
        assert [d["attached_to"] for d in docs] == ["prd"]
        assert docs[0]["name"] == "wireframe.svg"
    finally:
        _cleanup(db, issue_ids=[story_id, feature_id], doc_ids=[doc_id])


def test_dispatch_context_empty_documents_array(db, project):
    issue_id = _insert_issue(db, project, status="draft")
    try:
        run_id = db.execute(
            "select public.dispatch_issue(%s) as id", (issue_id,)
        ).fetchone()["id"]
        db.commit()
        ctx = db.execute(
            "select input_context from public.runs where id = %s", (run_id,)
        ).fetchone()["input_context"]
        assert ctx.get("documents") == []
    finally:
        _cleanup(db, issue_ids=[issue_id])


def test_link_check_rejects_mismatched_refs(db, project):
    """US-2.22: attached_to must agree with which refs are set."""
    db.rollback()
    doc_id = uuid.uuid4()
    with pytest.raises(Exception) as exc:
        db.execute(
            """
            insert into public.documents
              (id, org_id, project_id, issue_id, name, mime_type, size_bytes,
               storage_path, source, attached_to)
            values (%s, %s, %s, null, 'x.svg', 'image/svg+xml', 1, %s, 'user',
                    'work-item')
            """,
            (doc_id, project["org_id"], project["id"], f"t/{doc_id}/x.svg"),
        )
        db.commit()
    db.rollback()
    assert "documents_link_refs" in str(exc.value)


def test_work_item_delete_detaches_document_to_project(db, project):
    """US-2.21: deleting a work item detaches its documents (trigger flips
    attached_to back to 'project' so the link CHECK holds)."""
    issue_id = _insert_issue(db, project, status="draft")
    doc_id = _insert_document(
        db, project, name="detach-me.svg", attached_to="work-item", issue_id=issue_id
    )
    try:
        db.execute("delete from public.issues where id = %s", (issue_id,))
        db.commit()
        row = db.execute(
            "select issue_id, attached_to from public.documents where id = %s",
            (doc_id,),
        ).fetchone()
        assert row["issue_id"] is None
        assert row["attached_to"] == "project"
    finally:
        _cleanup(db, doc_ids=[doc_id])
