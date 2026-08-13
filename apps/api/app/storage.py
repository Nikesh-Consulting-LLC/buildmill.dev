"""Private `data` Storage bucket access (US-1.28) — service role only.

Server credentials (SSH passwords / private keys / passphrases) live under
``<org_id>/servers/<server_id>/`` in the private ``data`` bucket. Only
``api``, with the service-role key, can touch them; the bucket has no
client ``storage.objects`` policies, so RLS default-deny blocks every
browser. Nothing in this module ever returns secret material to a client
response — callers use the read helpers only to feed an SSH handshake.
"""

import httpx

from .config import Settings

DATA_BUCKET = "data"
# US-2.21: project documents. Unlike `data`, this bucket has org-scoped
# client policies (migration 037) — it holds org documents, not secrets.
DOCS_BUCKET = "project-docs"


class StorageError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _base(settings: Settings) -> str:
    return f"{settings.supabase_url}/storage/v1"


def _headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }


def server_prefix(org_id: str, server_id: str) -> str:
    return f"{org_id}/servers/{server_id}"


def deployment_prefix(org_id: str, deployment_id: str) -> str:
    return f"{org_id}/deployments/{deployment_id}"


def build_config_prefix(org_id: str, project_id: str) -> str:
    """US-7.9: write-only build/test config values for a project's code runs."""
    return f"{org_id}/projects/{project_id}/build-config"


def _is_not_found(resp: httpx.Response) -> bool:
    """Supabase Storage reports a missing object as HTTP 400 with a body of
    ``{"statusCode":"404","error":"not_found",...}`` (not a literal 404), so
    a plain status check misses it. Treat any of those signals as absent."""
    if resp.status_code == 404:
        return True
    try:
        body = resp.json()
    except ValueError:
        return False
    return (
        str(body.get("statusCode")) == "404"
        or body.get("error") == "not_found"
        or "not found" in str(body.get("message", "")).lower()
    )


async def put_object(
    settings: Settings,
    path: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    bucket: str = DATA_BUCKET,
) -> None:
    """Create-or-replace an object (x-upsert). Service role only."""
    headers = _headers(settings)
    headers["Content-Type"] = content_type
    headers["x-upsert"] = "true"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_base(settings)}/object/{bucket}/{path}",
            content=content,
            headers=headers,
        )
    if resp.status_code >= 400:
        raise StorageError(f"Storage write failed ({resp.status_code}): {resp.text}")


async def get_object(
    settings: Settings, path: str, bucket: str = DATA_BUCKET
) -> bytes | None:
    """Read an object's bytes, or None if it does not exist."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{_base(settings)}/object/authenticated/{bucket}/{path}",
            headers=_headers(settings),
        )
    if _is_not_found(resp):
        return None
    if resp.status_code >= 400:
        raise StorageError(f"Storage read failed ({resp.status_code}): {resp.text}")
    return resp.content


async def list_objects(settings: Settings, prefix: str) -> list[str]:
    """List object names directly under ``prefix`` (non-recursive)."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_base(settings)}/object/list/{DATA_BUCKET}",
            json={"prefix": f"{prefix}/", "limit": 1000},
            headers={**_headers(settings), "Content-Type": "application/json"},
        )
    if resp.status_code >= 400:
        raise StorageError(f"Storage list failed ({resp.status_code}): {resp.text}")
    return [row["name"] for row in resp.json() if row.get("name")]


async def delete_object(
    settings: Settings, path: str, bucket: str = DATA_BUCKET
) -> None:
    """Delete a single object; a missing object is not an error."""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{_base(settings)}/object/{bucket}/{path}",
            headers=_headers(settings),
        )
    if resp.status_code in (200, 204) or _is_not_found(resp):
        return
    raise StorageError(f"Storage delete failed ({resp.status_code}): {resp.text}")


async def delete_prefix_keys(settings: Settings, prefix: str, names: list[str]) -> None:
    """Delete named objects directly under ``prefix`` (missing ones ignored)."""
    for name in names:
        await delete_object(settings, f"{prefix}/{name}")


async def delete_prefix(settings: Settings, prefix: str) -> None:
    """Delete every object under ``prefix`` (used when a server is removed)."""
    names = await list_objects(settings, prefix)
    if not names:
        return
    full_paths = [f"{prefix}/{name}" for name in names]
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            "DELETE",
            f"{_base(settings)}/object/{DATA_BUCKET}",
            json={"prefixes": full_paths},
            headers={**_headers(settings), "Content-Type": "application/json"},
        )
    if resp.status_code >= 400:
        raise StorageError(f"Storage delete failed ({resp.status_code}): {resp.text}")
