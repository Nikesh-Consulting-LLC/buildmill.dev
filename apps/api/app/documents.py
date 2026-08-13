"""Project document server-side write/read path (US-2.21/2.22).

Browser CRUD goes straight to Supabase under RLS ("build less API") —
this module exists only for the writers with no user JWT or a non-user
source: factory-generated documents (PRD wireframes), agent uploads
mid-run, and the runner byte-fetch. Objects live in the `project-docs`
bucket at `<org_id>/projects/<project_id>/<document_id>/<filename>`;
metadata in public.documents.

A write with the same name to the same link target replaces the
document in place — same id, links intact — so regenerated wireframes
converge instead of piling up (US-2.22).
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from . import storage
from .config import Settings
from .db import _connect

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024  # mirrors the bucket's file_size_limit

_MIME_BY_EXT = {
    "svg": "image/svg+xml",
    "html": "text/html",
    "htm": "text/html",
    "mmd": "text/plain",
    "md": "text/markdown",
    "txt": "text/plain",
    "json": "application/json",
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


def safe_name(name: str | None) -> str:
    """A bare filename usable as the last storage-path segment: no
    separators (path traversal) and no characters Storage rejects."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._ ()-]", "_", base).strip(". ")
    return base or "document"


def mime_for(name: str, declared: str | None = None) -> str:
    if declared and declared != "application/octet-stream":
        return declared
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


def object_path(org_id: str, project_id: str, document_id: str, name: str) -> str:
    return f"{org_id}/projects/{project_id}/{document_id}/{name}"


_ROW_COLS = (
    "id, org_id, project_id, issue_id, test_case_id, run_id, name, mime_type, "
    "size_bytes, storage_path, source, attached_to, created_by, created_at, updated_at"
)


def _row_dict(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in row.items()}


def get_document(settings: Settings, document_id: str) -> dict[str, Any] | None:
    try:
        uuid.UUID(document_id)
    except ValueError:
        return None
    with _connect(settings) as conn:
        row = conn.execute(
            f"select {_ROW_COLS} from public.documents where id = %s",
            (document_id,),
        ).fetchone()
    return _row_dict(row) if row else None


def _find_existing(
    conn,
    *,
    project_id: str,
    name: str,
    attached_to: str,
    issue_id: str | None,
    test_case_id: str | None,
) -> dict[str, Any] | None:
    return conn.execute(
        f"""
        select {_ROW_COLS} from public.documents
        where project_id = %s and name = %s and attached_to = %s
          and issue_id is not distinct from %s
          and test_case_id is not distinct from %s
        """,
        (project_id, name, attached_to, issue_id, test_case_id),
    ).fetchone()


async def create_or_replace(
    settings: Settings,
    *,
    org_id: str,
    project_id: str,
    name: str,
    content: bytes,
    source: str,
    attached_to: str = "work-item",
    mime_type: str | None = None,
    issue_id: str | None = None,
    test_case_id: str | None = None,
    run_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Stage the object and upsert its metadata row; returns the row."""
    filename = safe_name(name)
    mime = mime_for(filename, mime_type)

    with _connect(settings) as conn:
        existing = _find_existing(
            conn,
            project_id=project_id,
            name=filename,
            attached_to=attached_to,
            issue_id=issue_id,
            test_case_id=test_case_id,
        )
        if existing:
            await storage.put_object(
                settings,
                existing["storage_path"],
                content,
                content_type=mime,
                bucket=storage.DOCS_BUCKET,
            )
            row = conn.execute(
                f"""
                update public.documents
                set mime_type = %s, size_bytes = %s, source = %s,
                    run_id = coalesce(%s, run_id), created_by = %s
                where id = %s
                returning {_ROW_COLS}
                """,
                (mime, len(content), source, run_id, created_by, existing["id"]),
            ).fetchone()
            conn.commit()
            return _row_dict(row)

        doc_id = str(uuid.uuid4())
        path = object_path(org_id, project_id, doc_id, filename)
        await storage.put_object(
            settings, path, content, content_type=mime, bucket=storage.DOCS_BUCKET
        )
        try:
            row = conn.execute(
                f"""
                insert into public.documents
                  (id, org_id, project_id, issue_id, test_case_id, run_id, name,
                   mime_type, size_bytes, storage_path, source, attached_to, created_by)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                returning {_ROW_COLS}
                """,
                (
                    doc_id,
                    org_id,
                    project_id,
                    issue_id,
                    test_case_id,
                    run_id,
                    filename,
                    mime,
                    len(content),
                    path,
                    source,
                    attached_to,
                    created_by,
                ),
            ).fetchone()
            conn.commit()
        except Exception:
            # Don't strand the staged object behind a failed row insert.
            await storage.delete_object(settings, path, bucket=storage.DOCS_BUCKET)
            raise
        return _row_dict(row)


async def read_bytes(settings: Settings, doc: dict[str, Any]) -> bytes | None:
    return await storage.get_object(
        settings, doc["storage_path"], bucket=storage.DOCS_BUCKET
    )
