"""The MCP server catalog and the per-run proxy (US-34.1, US-34.2, US-34.4).

The catalog is org configuration: what a server is, where it is, and what
credential it needs. The credential is write-only in Vault — at most a last-four
ever comes back out of any endpoint here.

The proxy is the part that reaches an agent, and it exists for exactly one
reason: the invariant that an agent machine holds one kind of secret. A Supabase
service key in an `mcp.json` on an agent box would turn a compromised machine
from "N revocable tokens" into "the org's credentials". So the agent gets a
scoped key worth one run, and the factory resolves the real credential on the way
past.

It forwards; it does not interpret. Authenticate the key, check the run was
granted the server, resolve the credential, relay the JSON-RPC, record the call.
A proxy that tries to understand every server's semantics becomes a bug factory.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .. import db, mcp_tools
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, rpc

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp-catalog"])


async def _require_manage_org(
    org_id: str, user: AuthUser, settings: Settings
) -> None:
    """Registering a server whose credential the factory will hold on the org's
    behalf is org infrastructure, not project work — the same bar us-26.1 set for
    registering a machine."""
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_org"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403, detail="Only an owner can manage MCP servers"
        )


def _shape(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["id"] = str(out["id"])
    out["org_id"] = str(out["org_id"])
    # Belt and braces: the select never includes the secret, and this makes it
    # impossible for a future column to leak into a response by accident.
    for forbidden in ("vault_secret_id", "credential", "secret"):
        out.pop(forbidden, None)
    return out


class ServerBody(BaseModel):
    name: str
    slug: str | None = None
    description: str = ""
    transport: str
    endpoint: str | None = None
    command: str | None = None
    declared_tools: list[str] = []
    needs_credential: bool = False
    credential_header: str | None = None
    # Deliberately NO credential field. The browser writes it straight to Vault
    # through the membership-gated `set_mcp_server_key` RPC, exactly as
    # `set_llm_provider_key` has always been written. A secret that never enters
    # an API request body cannot appear in an API log or a traceback — a smaller
    # surface than one the API merely promises not to print.


async def _validate_live(
    settings: Settings, entry: dict[str, Any], credential: str | None
) -> tuple[bool, str | None]:
    """Reach the server, so a registration that cannot work says so now.

    us-27.13's rule: a check belongs where the value is entered, not where it
    eventually fails. A `stdio` entry cannot be checked from here — the command
    runs on the agent machine — so it is recorded as unchecked rather than
    claimed to be fine.
    """
    if entry["transport"] != "http":
        return True, None
    headers = {"content-type": "application/json"}
    if credential and entry.get("credential_header"):
        header = entry["credential_header"]
        headers[header] = (
            f"Bearer {credential}" if header.lower() == "authorization" else credential
        )
    probe = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "buildmill-factory", "version": "1"},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(entry["endpoint"], json=probe, headers=headers)
    except Exception as e:  # noqa: BLE001
        return False, f"could not reach {entry['endpoint']}: {e}"[:600]
    if resp.status_code in (401, 403):
        return False, (
            f"the server answered {resp.status_code} — its credential was rejected"
            + (" (none was supplied)" if not credential else "")
        )
    if resp.status_code >= 400:
        return False, f"the server answered {resp.status_code}"
    return True, None


@router.get("/orgs/{org_id}/mcp-servers")
async def list_servers(
    org_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    try:
        member = await rpc(settings, user.token, "is_org_member", {"org": org_id})
    except RpcError:
        member = False
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")
    return {"servers": [_shape(r) for r in db.list_mcp_servers(settings, org_id)]}


@router.post("/orgs/{org_id}/mcp-servers")
async def create_server(
    org_id: str,
    body: ServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_manage_org(org_id, user, settings)
    try:
        entry = mcp_tools.clean_entry(body.model_dump())
    except mcp_tools.CatalogInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    row = db.upsert_mcp_server(settings, org_id, entry)
    server_id = str(row["id"])
    if entry["needs_credential"]:
        # It cannot be validated until the credential is written, and the
        # credential is written next, by the browser. Disabled until then, so a
        # half-registered server can never be granted to a run.
        db.set_mcp_server_enabled(settings, server_id, False)
        db.record_mcp_check(
            settings, server_id, False, "waiting for its credential"
        )
        return {
            "server": _shape(db.get_mcp_server(settings, server_id)),
            "needs_credential": True,
            "check_ok": False,
            "check_error": "waiting for its credential",
        }
    ok, error = await _validate_live(settings, entry, None)
    db.record_mcp_check(settings, server_id, ok, error)
    if not ok:
        # Registered and disabled rather than discarded: the manager's typed
        # configuration survives, and nothing can be granted a server that does
        # not answer.
        db.set_mcp_server_enabled(settings, server_id, False)
    fresh = db.get_mcp_server(settings, server_id)
    return {"server": _shape(fresh), "check_ok": ok, "check_error": error}


@router.post("/mcp-servers/{server_id}/validate")
async def validate_server(
    server_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Probe a registered server, with whatever credential Vault holds for it.

    Separate from registration because the credential arrives separately — the
    browser writes it to Vault and then asks for the check. us-27.13's rule still
    holds: the answer lands on the row the value was entered on, not at the first
    run that needed it.
    """
    existing = db.get_mcp_server(settings, server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="server not found")
    await _require_manage_org(str(existing["org_id"]), user, settings)
    entry = {
        "transport": existing["transport"],
        "endpoint": existing["endpoint"],
        "credential_header": existing["credential_header"],
    }
    credential = (
        db.read_mcp_server_credential(settings, server_id)
        if existing["needs_credential"]
        else None
    )
    if existing["needs_credential"] and not credential:
        db.record_mcp_check(settings, server_id, False, "no credential is set")
        db.set_mcp_server_enabled(settings, server_id, False)
        return {"check_ok": False, "check_error": "no credential is set"}
    ok, error = await _validate_live(settings, entry, credential)
    db.record_mcp_check(settings, server_id, ok, error)
    db.set_mcp_server_enabled(settings, server_id, ok)
    return {"check_ok": ok, "check_error": error}


@router.patch("/mcp-servers/{server_id}")
async def update_server(
    server_id: str,
    body: ServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    existing = db.get_mcp_server(settings, server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="server not found")
    org_id = str(existing["org_id"])
    await _require_manage_org(org_id, user, settings)
    try:
        entry = mcp_tools.clean_entry(body.model_dump())
    except mcp_tools.CatalogInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.upsert_mcp_server(settings, org_id, entry, server_id=server_id)
    credential = (
        db.read_mcp_server_credential(settings, server_id)
        if entry["needs_credential"]
        else None
    )
    ok, error = await _validate_live(settings, entry, credential)
    db.record_mcp_check(settings, server_id, ok, error)
    db.set_mcp_server_enabled(settings, server_id, ok)
    return {
        "server": _shape(db.get_mcp_server(settings, server_id)),
        "check_ok": ok,
        "check_error": error,
    }


@router.delete("/mcp-servers/{server_id}")
async def delete_server(
    server_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    existing = db.get_mcp_server(settings, server_id)
    if not existing:
        raise HTTPException(status_code=404, detail="server not found")
    await _require_manage_org(str(existing["org_id"]), user, settings)
    return {"deleted": db.delete_mcp_server(settings, server_id)}


# ---------------------------------------------------------------------------
# US-34.2: the proxy
# ---------------------------------------------------------------------------


@router.post("/mcp-proxy/{slug}")
async def proxy_mcp(
    slug: str,
    request: Request,
    settings: Settings = Depends(get_settings),
    x_factory_mcp_key: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Relay one JSON-RPC message to a granted server, on the run's behalf.

    Authenticated ONLY by a scoped key that names a running run. The grant is
    checked against the surface recorded at claim (us-34.3), so the proxy adds no
    path to a server the run was not granted — and cross-run isolation is a
    property of the key, not of anything the agent sends.
    """
    scoped = x_factory_mcp_key or (
        authorization[7:] if authorization.lower().startswith("bearer ") else ""
    )
    claims = db.validate_mcp_key(settings, scoped)
    if not claims:
        raise HTTPException(
            status_code=401,
            detail="invalid or expired MCP key — a key is valid only while its "
            "run is running",
        )

    org_id = str(claims["org_id"])
    run_id = str(claims["run_id"])
    surface = claims.get("tool_surface") or {}
    granted = {g.get("slug"): g for g in (surface.get("granted") or [])}
    entry = granted.get(slug)
    if not entry:
        # Default deny, enforced at the only door: a server this run was not
        # granted is not reachable through the proxy, whatever the agent asks.
        raise HTTPException(
            status_code=403,
            detail=f"this run was not granted the '{slug}' tool server",
        )

    server = db.get_mcp_server(settings, str(entry["id"]))
    if not server or not server.get("enabled"):
        raise HTTPException(
            status_code=502,
            detail=f"the '{slug}' tool server is not currently available",
        )

    body = await request.body()
    tool, arguments = _describe(body)

    headers = {"content-type": "application/json", "accept": "application/json"}
    if server.get("needs_credential") and server.get("credential_header"):
        credential = db.read_mcp_server_credential(settings, str(server["id"]))
        if not credential:
            raise HTTPException(
                status_code=502,
                detail=f"the '{slug}' tool server has no credential configured",
            )
        header = server["credential_header"]
        headers[header] = (
            f"Bearer {credential}" if header.lower() == "authorization" else credential
        )

    started = time.monotonic()
    outcome, error, payload, status = "ok", None, b"", 502
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                str(server["endpoint"]), content=body, headers=headers
            )
        status = resp.status_code
        payload = resp.content
        if status >= 400:
            outcome, error = "error", f"the server answered {status}"
    except Exception as e:  # noqa: BLE001
        outcome, error = "error", str(e)[:300]
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32001, "message": f"tool server unreachable: {error}"},
            }
        ).encode()
        status = 502

    # US-34.4: the record. Never the credential, never the scoped key, never the
    # payload — the call, redacted.
    _audit(
        settings,
        {
            "org_id": org_id,
            "run_id": run_id,
            "worker_id": str(claims["worker_id"]),
            "server_id": str(server["id"]),
            "server_name": server.get("name"),
            "tool": tool,
            "arguments_redacted": arguments,
            "outcome": outcome,
            "error": error,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "response_bytes": len(payload),
        },
    )
    return Response(
        content=payload,
        status_code=status,
        media_type="application/json",
    )


def _describe(body: bytes) -> tuple[str, Any]:
    """The tool name and a redacted view of its arguments, for the record."""
    try:
        message = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "", None
    if not isinstance(message, dict):
        return "", None
    method = str(message.get("method") or "")
    params = message.get("params") or {}
    if method == "tools/call" and isinstance(params, dict):
        return (
            str(params.get("name") or "")[:120],
            mcp_tools.redact_arguments(params.get("arguments")),
        )
    return method[:120], mcp_tools.redact_arguments(params)


def _audit(settings: Settings, call: dict[str, Any]) -> None:
    if not db.record_tool_call(settings, call):
        db.count_dropped_tool_call(settings, call.get("run_id"))
