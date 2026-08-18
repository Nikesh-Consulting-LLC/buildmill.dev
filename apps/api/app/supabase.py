"""Thin PostgREST client (US-1.8/1.9).

The API holds no service key: every request forwards the caller's own
JWT, so Postgres RLS authorizes exactly what the user could do directly.
"""

import asyncio
from typing import Any

import httpx

from .config import Settings

# PostgREST's message + hint can run long (an ambiguous embed's hint lists
# every candidate relationship); enough to diagnose, not enough to flood.
MESSAGE_CAP = 600


class SupabaseUnreachable(Exception):
    """The database did not answer at the transport layer (US-79.5).

    Twice in prod (BUG-6) a moment of network trouble between the VM and
    Supabase surfaced as a naked 500 and a 200-line `httpcore.ConnectTimeout`
    traceback. This is that moment with a name: it carries the operation
    (verb + table/RPC name, never query values) so the report and the 504 can
    say what was being asked when nobody answered.
    """

    def __init__(self, operation: str, cause: str):
        super().__init__(f"the database did not answer during {operation} ({cause})")
        self.operation = operation
        self.cause = cause


# The transport failures that mean "nobody answered", as opposed to an answer
# we did not like (those are PostgrestQueryError's job).
_TRANSPORT_FAILURES = (
    httpx.ConnectTimeout,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    # us-117.1: these two were missing, and on 2026-08-17 they were 9 of the
    # 13 recorded faults — so the same outage produced some reports titled
    # `SupabaseUnreachable: … (ConnectTimeout)` with the operation attached and
    # others a bare `ReadError` traceback. A connection dropped mid-read is
    # still nobody answering.
    httpx.ReadError,
    httpx.RemoteProtocolError,
)

# One retry, reads only: a connect timeout is usually a blip, and a read is
# safe to ask twice. Writes and RPCs are never blind-retried — a timeout does
# not prove the write failed.
_RETRY_BACKOFF_SECONDS = 0.5


async def _send(
    operation: str, method: str, url: str, *, retry: bool = False, **kwargs
) -> httpx.Response:
    """The one door every PostgREST request leaves through."""
    attempts = 2 if retry else 1
    for attempt in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                return await client.request(method, url, **kwargs)
        except _TRANSPORT_FAILURES as exc:
            if attempt + 1 < attempts:
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            raise SupabaseUnreachable(operation, type(exc).__name__) from exc
    raise AssertionError("unreachable")  # pragma: no cover


def _headers(settings: Settings, user_token: str) -> dict[str, str]:
    return {
        "apikey": settings.supabase_publishable_key,
        "Authorization": f"Bearer {user_token}",
        "Content-Type": "application/json",
    }


class PostgrestError(Exception):
    """A PostgREST call that did not succeed, in PostgREST's own words.

    `code` is the part that says *why* — `PGRST201` for an ambiguous embed,
    `23503` for a foreign key — and the part BUG-1.1 cost a production crash
    report to learn we were throwing away.
    """

    def __init__(
        self, message: str, *, code: str | None = None, status_code: int | None = None
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code


def _describe(resp: httpx.Response) -> tuple[str, str | None]:
    """Pull message, hint and code out of a PostgREST error body."""
    try:
        body = resp.json()
    except ValueError:
        body = None
    fallback = (resp.text or "").strip() or f"HTTP {resp.status_code}"
    if not isinstance(body, dict):
        return fallback[:MESSAGE_CAP], None
    parts = [str(body.get("message") or fallback)]
    if body.get("hint"):
        # For PGRST201 the hint literally names the relationships to choose
        # between, which is the whole fix.
        parts.append(str(body["hint"]))
    code = body.get("code")
    return " — ".join(parts)[:MESSAGE_CAP], (str(code) if code else None)


class PostgrestQueryError(PostgrestError, httpx.HTTPStatusError):
    """A refused call made under the caller's own JWT.

    Also an `httpx.HTTPStatusError`, so the call sites that already key off a
    status code (the 409s in `servers`/`deployments`) keep working unchanged.
    """

    def __init__(self, resp: httpx.Response):
        message, code = _describe(resp)
        httpx.HTTPStatusError.__init__(
            self, message, request=resp.request, response=resp
        )
        self.message = message
        self.code = code
        self.status_code = resp.status_code


def raise_for_postgrest(resp: httpx.Response) -> None:
    """Anything that is not 2xx failed — redirects included.

    PostgREST answers an ambiguous embed with `300 Multiple Choices`
    (PGRST201). Both `raise_for_status()` and a `>= 400` check get that
    wrong in their own way: the first raised a bare httpx error nothing was
    prepared to read, the second would have handed the error body back as if
    it were rows. Neither told anyone which relationship was ambiguous.
    """
    if resp.is_success:
        return
    raise PostgrestQueryError(resp)


async def postgrest_get(
    settings: Settings, user_token: str, path: str, params: dict[str, str]
) -> Any:
    resp = await _send(
        f"GET {path}",
        "GET",
        f"{settings.rest_url}/{path}",
        retry=True,
        params=params,
        headers=_headers(settings, user_token),
    )
    raise_for_postgrest(resp)
    return resp.json()


async def postgrest_post(
    settings: Settings, user_token: str, path: str, body: dict[str, Any]
) -> Any:
    headers = _headers(settings, user_token)
    headers["Prefer"] = "return=representation"
    resp = await _send(
        f"POST {path}",
        "POST",
        f"{settings.rest_url}/{path}",
        json=body,
        headers=headers,
    )
    raise_for_postgrest(resp)
    return resp.json()


async def postgrest_delete(
    settings: Settings, user_token: str, path: str, params: dict[str, str]
) -> None:
    resp = await _send(
        f"DELETE {path}",
        "DELETE",
        f"{settings.rest_url}/{path}",
        params=params,
        headers=_headers(settings, user_token),
    )
    raise_for_postgrest(resp)


async def postgrest_patch(
    settings: Settings,
    user_token: str,
    path: str,
    params: dict[str, str],
    body: dict[str, Any],
) -> Any:
    headers = _headers(settings, user_token)
    headers["Prefer"] = "return=representation"
    resp = await _send(
        f"PATCH {path}",
        "PATCH",
        f"{settings.rest_url}/{path}",
        params=params,
        json=body,
        headers=headers,
    )
    raise_for_postgrest(resp)
    return resp.json()


async def postgrest_upsert(
    settings: Settings,
    user_token: str,
    path: str,
    body: dict[str, Any],
    on_conflict: str,
) -> Any:
    headers = _headers(settings, user_token)
    headers["Prefer"] = "return=representation,resolution=merge-duplicates"
    resp = await _send(
        f"UPSERT {path}",
        "POST",
        f"{settings.rest_url}/{path}",
        params={"on_conflict": on_conflict},
        json=body,
        headers=headers,
    )
    raise_for_postgrest(resp)
    return resp.json()


class RpcError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


async def rpc(
    settings: Settings, user_token: str, fn: str, args: dict[str, Any]
) -> Any:
    resp = await _send(
        f"RPC {fn}",
        "POST",
        f"{settings.rest_url}/rpc/{fn}",
        json=args,
        headers=_headers(settings, user_token),
    )
    if resp.status_code >= 400:
        try:
            message = resp.json().get("message", resp.text)
        except ValueError:
            message = resp.text
        raise RpcError(message)
    # void functions return an empty body
    return resp.json() if resp.text.strip() else None


def _service_headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


async def admin_get(settings: Settings, path: str, params: dict[str, str]) -> Any:
    resp = await _send(
        f"GET {path}",
        "GET",
        f"{settings.rest_url}/{path}",
        retry=True,
        params=params,
        headers=_service_headers(settings),
    )
    raise_for_postgrest(resp)
    return resp.json()


async def admin_post(settings: Settings, path: str, body: dict[str, Any]) -> Any:
    headers = _service_headers(settings)
    headers["Prefer"] = "return=representation"
    resp = await _send(
        f"POST {path}", "POST", f"{settings.rest_url}/{path}", json=body, headers=headers
    )
    raise_for_postgrest(resp)
    return resp.json()


async def admin_patch(
    settings: Settings, path: str, params: dict[str, str], body: dict[str, Any]
) -> Any:
    headers = _service_headers(settings)
    headers["Prefer"] = "return=representation"
    resp = await _send(
        f"PATCH {path}",
        "PATCH",
        f"{settings.rest_url}/{path}",
        params=params,
        json=body,
        headers=headers,
    )
    raise_for_postgrest(resp)
    return resp.json()


async def admin_upsert(
    settings: Settings, path: str, rows: list[dict[str, Any]], on_conflict: str
) -> Any:
    """Service-role bulk upsert (PostgREST merge-duplicates on a conflict key)."""
    headers = _service_headers(settings)
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    resp = await _send(
        f"UPSERT {path}",
        "POST",
        f"{settings.rest_url}/{path}",
        params={"on_conflict": on_conflict},
        json=rows,
        headers=headers,
    )
    raise_for_postgrest(resp)
    return resp.json()


async def admin_delete(settings: Settings, path: str, params: dict[str, str]) -> None:
    resp = await _send(
        f"DELETE {path}",
        "DELETE",
        f"{settings.rest_url}/{path}",
        params=params,
        headers=_service_headers(settings),
    )
    raise_for_postgrest(resp)


async def admin_rpc(settings: Settings, fn: str, args: dict[str, Any]) -> Any:
    """Service-role RPC call — for writes with no caller JWT to forward
    (e.g. the GitHub install callback, US-3.19), where the function itself
    is granted to service_role only, never authenticated/anon."""
    resp = await _send(
        f"RPC {fn}",
        "POST",
        f"{settings.rest_url}/rpc/{fn}",
        json=args,
        headers=_service_headers(settings),
    )
    raise_for_postgrest(resp)
    return resp.json() if resp.text.strip() else None
