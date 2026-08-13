"""US-2.21: document endpoints and write-path replace semantics."""

import asyncio
import uuid

import pytest

from app import documents as documents_module

SECRET = {"X-Worker-Token": "sfw_testtoken"}

RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
PARENT_ID = str(uuid.uuid4())
PROJECT_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())
WORKER_ID = str(uuid.uuid4())

WORKER = {
    "id": WORKER_ID,
    "org_id": ORG_ID,
    "name": "doc-test-worker",
    "type": "autonomous",
    "status": "active",
}


@pytest.fixture(autouse=True)
def worker_auth(monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_by_token",
        lambda s, t: dict(WORKER) if t == "sfw_testtoken" else None,
    )


def _run(status="running", prd_issue_id=PARENT_ID):
    return {
        "id": RUN_ID,
        "org_id": ORG_ID,
        "issue_id": ISSUE_ID,
        "status": status,
        "project_id": PROJECT_ID,
        "prd_issue_id": prd_issue_id,
        "worker_id": WORKER_ID,
    }


def _doc(**over):
    doc = {
        "id": DOC_ID,
        "org_id": ORG_ID,
        "project_id": PROJECT_ID,
        "issue_id": ISSUE_ID,
        "test_case_id": None,
        "run_id": None,
        "name": "spec.svg",
        "mime_type": "image/svg+xml",
        "size_bytes": 5,
        "storage_path": f"{ORG_ID}/projects/{PROJECT_ID}/{DOC_ID}/spec.svg",
        "source": "user",
        "attached_to": "work-item",
        "created_by": None,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    doc.update(over)
    return doc


# ------------------------------------------------------- agent upload


def test_upload_requires_worker_token(client):
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/documents",
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 401


def test_upload_unknown_run_404(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_run_for_documents", lambda s, r: None
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/documents",
        headers=SECRET,
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 404


def test_upload_terminal_run_409(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_run_for_documents",
        lambda s, r: _run(status="succeeded"),
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/documents",
        headers=SECRET,
        files={"file": ("a.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 409


def test_upload_happy_path_attaches_to_run_issue(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_run_for_documents", lambda s, r: _run()
    )
    captured = {}

    async def fake_create(settings, **kwargs):
        captured.update(kwargs)
        return _doc(source="agent", run_id=RUN_ID)

    monkeypatch.setattr("app.routers.worker.documents.create_or_replace", fake_create)

    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/documents",
        headers=SECRET,
        files={"file": ("wireframe.svg", b"<svg/>", "image/svg+xml")},
    )
    assert resp.status_code == 201, resp.text
    assert captured["source"] == "agent"
    assert captured["attached_to"] == "work-item"
    assert captured["issue_id"] == ISSUE_ID
    assert captured["run_id"] == RUN_ID
    assert captured["content"] == b"<svg/>"
    assert resp.json()["document"]["id"] == DOC_ID


def test_upload_empty_file_422(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_run_for_documents", lambda s, r: _run()
    )
    resp = client.post(
        f"/api/v1/worker/runs/{RUN_ID}/documents",
        headers=SECRET,
        files={"file": ("a.txt", b"", "text/plain")},
    )
    assert resp.status_code == 422


# --------------------------------------------------------- byte fetch


def _wire_fetch(monkeypatch, doc, run=None, data=b"bytes"):
    monkeypatch.setattr(
        "app.routers.worker.db.get_run_for_documents", lambda s, r: run or _run()
    )
    monkeypatch.setattr(
        "app.routers.worker.documents.get_document", lambda s, d: doc
    )

    async def fake_read(settings, d):
        return data

    monkeypatch.setattr("app.routers.worker.documents.read_bytes", fake_read)


def test_fetch_work_item_document(client, monkeypatch):
    _wire_fetch(monkeypatch, _doc(), data=b"<svg/>")
    resp = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/documents/{DOC_ID}", headers=SECRET
    )
    assert resp.status_code == 200
    assert resp.content == b"<svg/>"
    assert resp.headers["content-type"].startswith("image/svg+xml")


def test_fetch_prd_document_of_parent_feature(client, monkeypatch):
    """US-2.22: a story's run can read its parent feature's PRD documents."""
    _wire_fetch(monkeypatch, _doc(issue_id=PARENT_ID, attached_to="prd"))
    resp = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/documents/{DOC_ID}", headers=SECRET
    )
    assert resp.status_code == 200


def test_fetch_unrelated_document_404(client, monkeypatch):
    _wire_fetch(monkeypatch, _doc(issue_id=str(uuid.uuid4())))
    resp = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/documents/{DOC_ID}", headers=SECRET
    )
    assert resp.status_code == 404


def test_fetch_prd_document_not_governing_404(client, monkeypatch):
    """A prd-linked doc on some other feature is invisible to this run."""
    _wire_fetch(monkeypatch, _doc(issue_id=str(uuid.uuid4()), attached_to="prd"))
    resp = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/documents/{DOC_ID}", headers=SECRET
    )
    assert resp.status_code == 404


def test_fetch_cross_org_document_404(client, monkeypatch):
    _wire_fetch(monkeypatch, _doc(org_id=str(uuid.uuid4())))
    resp = client.get(
        f"/api/v1/worker/runs/{RUN_ID}/documents/{DOC_ID}", headers=SECRET
    )
    assert resp.status_code == 404


# ------------------------------------- create_or_replace (write path)


class FakeResult:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class FakeConn:
    """Stands in for a psycopg connection: scripted fetchone results, and
    records every statement."""

    def __init__(self, results):
        self.results = list(results)
        self.statements = []
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params))
        return FakeResult(self.results.pop(0))

    def commit(self):
        self.committed = True


@pytest.fixture()
def fake_storage(monkeypatch):
    calls = {"put": [], "delete": []}

    async def fake_put(settings, path, content, content_type="x", bucket=""):
        calls["put"].append({"path": path, "bucket": bucket, "mime": content_type})

    async def fake_delete(settings, path, bucket=""):
        calls["delete"].append({"path": path, "bucket": bucket})

    monkeypatch.setattr(documents_module.storage, "put_object", fake_put)
    monkeypatch.setattr(documents_module.storage, "delete_object", fake_delete)
    return calls


def test_same_name_same_target_replaces_in_place(
    monkeypatch, fake_storage, settings_override
):
    """US-2.22: a factory re-draft with the same filename updates the
    existing document — same id, same object path — instead of duplicating."""
    existing = _doc(source="factory", attached_to="prd")
    updated = {**existing, "size_bytes": 10}
    conn = FakeConn([existing, updated])
    monkeypatch.setattr(documents_module, "_connect", lambda s: conn)

    row = asyncio.run(
        documents_module.create_or_replace(
            settings_override,
            org_id=ORG_ID,
            project_id=PROJECT_ID,
            name="spec.svg",
            content=b"0123456789",
            source="factory",
            attached_to="prd",
            issue_id=ISSUE_ID,
        )
    )
    assert row["id"] == DOC_ID
    assert len(fake_storage["put"]) == 1
    assert fake_storage["put"][0]["path"] == existing["storage_path"]
    assert fake_storage["put"][0]["bucket"] == "project-docs"
    assert "update public.documents" in conn.statements[1][0]
    assert conn.committed


def test_new_document_inserts_with_fresh_path(
    monkeypatch, fake_storage, settings_override
):
    inserted = _doc(name="notes.md")
    conn = FakeConn([None, inserted])
    monkeypatch.setattr(documents_module, "_connect", lambda s: conn)

    row = asyncio.run(
        documents_module.create_or_replace(
            settings_override,
            org_id=ORG_ID,
            project_id=PROJECT_ID,
            name="notes.md",
            content=b"# n",
            source="user",
            attached_to="work-item",
            issue_id=ISSUE_ID,
        )
    )
    assert row["name"] == "notes.md"
    path = fake_storage["put"][0]["path"]
    assert path.startswith(f"{ORG_ID}/projects/{PROJECT_ID}/")
    assert path.endswith("/notes.md")
    assert "insert into public.documents" in conn.statements[1][0]


def test_safe_name_strips_traversal_and_separators():
    assert documents_module.safe_name("../../evil.svg") == "evil.svg"
    assert documents_module.safe_name("a\\b\\c.txt") == "c.txt"
    assert documents_module.safe_name("  ") == "document"
    assert documents_module.safe_name("ok name (v2).png") == "ok name (v2).png"


def test_mime_inference():
    assert documents_module.mime_for("a.svg") == "image/svg+xml"
    assert documents_module.mime_for("a.mmd") == "text/plain"
    assert documents_module.mime_for("a.bin") == "application/octet-stream"
    assert documents_module.mime_for("a.bin", "application/zip") == "application/zip"
