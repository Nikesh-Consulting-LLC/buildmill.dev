"""Registered deployment servers: registry, SSH terminal, file manager.

Covers US-1.28 (server registry + write-only credentials + test connection +
host-key TOFU), US-1.29 (WebSocket SSH terminal bridge), US-1.30 (SFTP file
manager) and US-1.46 (text editor). `api` is the only component that can read
the credentials — they live in the private `data` bucket and never cross to
the browser in any form; the client only ever exchanges terminal I/O and file
bytes over these endpoints.
"""

from __future__ import annotations

import asyncio
import json
import posixpath
import shlex
from typing import Literal
from urllib.parse import quote

import httpx
import jwt
import paramiko
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .. import agent_provision
from .. import llm as llm_service
from .. import sftp as sftp_ops
from .. import ssh, storage
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..errors import safe_accept
from ..supabase import (
    RpcError,
    postgrest_delete,
    postgrest_get,
    postgrest_patch,
    postgrest_post,
    rpc,
)

router = APIRouter(prefix="/servers", tags=["servers"])

IDLE_TIMEOUT = 900  # seconds a terminal may sit with no I/O before api tears it down
CHUNK = 65536


def _pty_dim(value: object, default: int) -> int:
    """Coerce a browser-supplied cols/rows to a sane PTY dimension.

    A still-opening pop-out window can send null/0/huge sizes; clamp them so
    the PTY is always valid and never allocated at an absurd size.
    """
    try:
        n = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(1, min(n, 1000))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def get_server_for_user(settings: Settings, token: str, server_id: str) -> dict:
    """Fetch a server row under the caller's own JWT.

    RLS means a server_id from another org simply returns no row — this is
    the single cross-org isolation gate every endpoint below relies on.
    """
    rows = await postgrest_get(
        settings, token, "servers", {"select": "*", "id": f"eq.{server_id}", "limit": "1"}
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    return rows[0]


async def resolve_credentials(settings: Settings, server: dict) -> ssh.Credentials:
    prefix = storage.server_prefix(server["org_id"], server["id"])
    if server["auth_method"] == "password":
        pw = await storage.get_object(settings, f"{prefix}/password")
        if pw is None:
            raise HTTPException(status_code=400, detail="This server has no stored password. Edit it to set one.")
        return ssh.Credentials(password=pw.decode("utf-8"))
    key = await storage.get_object(settings, f"{prefix}/ssh_key")
    if key is None:
        raise HTTPException(status_code=400, detail="This server has no stored SSH key. Edit it to set one.")
    passphrase = await storage.get_object(settings, f"{prefix}/ssh_key_passphrase")
    return ssh.Credentials(
        private_key=key.decode("utf-8"),
        passphrase=passphrase.decode("utf-8") if passphrase else None,
    )


async def connect_server(settings: Settings, token: str, server: dict) -> ssh.Connection:
    """Open an authenticated SSH transport, enforcing host-key trust (TOFU)."""
    creds = await resolve_credentials(settings, server)
    expected = server.get("host_key_fingerprint")
    try:
        conn = await asyncio.to_thread(
            ssh.open_connection,
            host=server["host"],
            port=server["port"],
            username=server["username"],
            auth_method=server["auth_method"],
            creds=creds,
            expected_host_fingerprint=expected,
        )
    except ssh.HostKeyChanged as e:
        raise HTTPException(status_code=409, detail=e.message)
    except ssh.SSHError as e:
        raise HTTPException(status_code=502, detail=e.message)

    if not expected:
        # Trust-on-first-use: record the host key so a later change is caught.
        try:
            await postgrest_patch(
                settings, token, "servers", {"id": f"eq.{server['id']}"},
                {"host_key_fingerprint": conn.host_key_fingerprint},
            )
        except Exception:
            pass
    return conn


async def resolve_claude_terminal_slot(
    settings: Settings, user: AuthUser, server: dict, agent_slot_id: str
) -> dict:
    """US-55.6: look up + authorize the agent slot a Claude terminal targets.

    Only valid for a `claude_billing='subscription'` slot: an API-billed agent
    has a metered gateway key that outranks any OAuth login, so there is no
    interactive session to hand over. Gated on `manage_org` — this is a
    power-user escape hatch, not a routine action.
    """
    await _require_manage_org(str(server["org_id"]), user, settings)

    slots = await postgrest_get(
        settings, user.token, "agent_slots",
        {
            "select": "slot_index,worker_id,workspace_path,status,agent_servers(server_id,shared,workdir)",
            "id": f"eq.{agent_slot_id}",
            "limit": "1",
        },
    )
    if not slots:
        raise HTTPException(status_code=404, detail="Agent slot not found")
    slot = slots[0]
    host = slot.get("agent_servers") or {}
    if host.get("server_id") != server["id"] or slot.get("status") != "active":
        raise HTTPException(status_code=404, detail="Agent slot not found")
    if not slot.get("worker_id"):
        raise HTTPException(status_code=400, detail="This agent has no worker identity yet.")

    configs = await postgrest_get(
        settings, user.token, "runner_config",
        {"select": "claude_billing", "worker_id": f"eq.{slot['worker_id']}", "limit": "1"},
    )
    billing = configs[0]["claude_billing"] if configs else "api"
    if billing != "subscription":
        raise HTTPException(
            status_code=400,
            detail="This agent isn't Claude-subscription billed — there's no login session to open.",
        )

    as_user = (
        agent_provision.slot_unix_user(int(slot["slot_index"]))
        if host.get("shared")
        else agent_provision.SERVICE_USER
    )
    return {
        "as_user": as_user,
        "workspace": slot["workspace_path"],
        "workdir": host.get("workdir"),
        "slot_index": int(slot["slot_index"]),
    }


def _exec_and_capture(
    transport: paramiko.Transport, command: str, stdin_data: bytes | None = None
) -> tuple[str, int]:
    """Run a one-shot (non-interactive) remote command and collect output.

    Blocking — call via `asyncio.to_thread`. Reading via `.read()` on the
    channel's file objects blocks until the remote process exits and closes
    its streams, which is exactly the "wait for this command to finish" we
    want here (no PTY, no long-running process). Mirrors `deploy._exec`'s
    stdin-feeding shape (`chan.sendall` + `shutdown_write`), needed here to
    hand `sudo -S` a password without it ever appearing on the command line.
    """
    chan = transport.open_session()
    chan.exec_command(command)
    if stdin_data is not None:
        chan.sendall(stdin_data)
    chan.shutdown_write()
    out = chan.makefile("rb").read()
    chan.makefile_stderr("rb").read()
    status = chan.recv_exit_status()
    chan.close()
    return out.decode("utf-8", "replace"), status


def _sudo_exec(transport: paramiko.Transport, command: str, password: str | None) -> tuple[str, int]:
    """`agent_provision.sudo_wrap`'s exact rule, reused here: a stored
    password goes over stdin to `sudo -S -p ''` (never on the command line,
    never echoed); key-auth logins fall back to `-n` and need passwordless
    sudo already configured on the box, same as provisioning requires."""
    wrapped = agent_provision.sudo_wrap(command, have_password=bool(password))
    stdin = f"{password}\n".encode() if password else None
    return _exec_and_capture(transport, wrapped, stdin)


def _write_session_secret_file(
    conn: ssh.Connection, remote_path: str, content: str, slot: dict, password: str | None
) -> str | None:
    """SFTP-write a small 0600 file, then chown it to the slot's own OS user.

    Shared by the MCP config and the subscription-token file below — both are
    session-scoped secrets that must be readable by exactly the account the
    launch command sudos into, and nothing else. Returns None (best-effort,
    never raises) on any failure, including a failed chown, in which case the
    half-written file is removed rather than left owned by the admin login.
    """
    try:
        sftp = paramiko.SFTPClient.from_transport(conn.transport)
        try:
            with sftp.open(remote_path, "w") as f:
                f.write(content)
            sftp.chmod(remote_path, 0o600)
        finally:
            sftp.close()

        chown_cmd = (
            f"chown {shlex.quote(slot['as_user'])}:{shlex.quote(slot['as_user'])} "
            f"{shlex.quote(remote_path)}"
        )
        _, chown_status = _sudo_exec(conn.transport, chown_cmd, password)
        if chown_status != 0:
            _sudo_exec(conn.transport, f"rm -f {shlex.quote(remote_path)}", password)
            return None
        return remote_path
    except Exception:
        return None


def _provision_claude_askpass(conn: ssh.Connection, password: str, agent_slot_id: str) -> str | None:
    """A `SUDO_ASKPASS` helper for the interactive claude launch, so `sudo -A`
    can escalate on a password-auth server without ever prompting on — or
    echoing into — the pty the human is watching.

    Written over SFTP, a channel entirely separate from the visible
    `invoke_shell()` stream: nothing about this file's contents (or even its
    creation) ever appears in the terminal transcript, unlike the earlier
    `sudo -S` approach that typed the password as a second input line and
    got it echoed straight back (the actual bug this replaces — see
    `build_claude_launch_command`'s docstring). Owned by the connecting
    admin login itself (no chown needed — that account already holds this
    password), executable only by that account, removed when the session
    ends.
    """
    remote_path = f"/tmp/.claude-terminal-askpass-{agent_slot_id}"
    script = f"#!/bin/sh\nprintf '%s\\n' {shlex.quote(password)}\n"
    try:
        sftp = paramiko.SFTPClient.from_transport(conn.transport)
        try:
            with sftp.open(remote_path, "w") as f:
                f.write(script)
            sftp.chmod(remote_path, 0o700)
        finally:
            sftp.close()
        return remote_path
    except Exception:
        return None


def _provision_claude_token_file(
    conn: ssh.Connection, token: str, slot: dict, agent_slot_id: str, password: str | None
) -> str | None:
    """US-52.2's precedence, reproduced for a human session: a factory-held
    (Vault) subscription token, when the org has one, wins over whatever
    machine-level credential the "Connect Claude" script may have left in the
    slot's env file — `subscription_env()` gives the automated agent the
    exact same override by injecting it over `os.environ`. The launch shell
    sources the machine env file first, then this file second, so an `export`
    here shadows a stale/absent machine token the same way.
    """
    content = f"export CLAUDE_CODE_OAUTH_TOKEN={shlex.quote(token)}\n"
    remote_path = f"/tmp/.claude-terminal-token-{agent_slot_id}"
    return _write_session_secret_file(conn, remote_path, content, slot, password)


def _provision_claude_mcp_config(
    conn: ssh.Connection, api_base_url: str, slot: dict, agent_slot_id: str, password: str | None
) -> str | None:
    """US-55.6: give a human-driven `claude` session the same Factory MCP
    access the agent's own automated turns get.

    The supervisor never persists this on the box — `mcpconfig.write()`
    generates `{workdir}/.factory-mcp.json` fresh per run and deletes it in a
    `finally` (apps/runner/supervisor/mcpconfig.py), because the file carries
    the worker's bearer token. A bare `claude` in this slot's shell therefore
    has no MCP access at all; this reproduces the same `--mcp-config
    ... --strict-mcp-config` shape for the duration of just this terminal
    session, written 0600 and owned by the slot's own OS user, and removed
    when the session ends (see the `finally` in `terminal()`).

    Best-effort: returns None (never raises) if the slot's env file can't be
    read — the caller falls back to a plain `claude` with no MCP tools rather
    than failing the whole terminal open over it.
    """
    workdir = slot.get("workdir")
    if not workdir:
        return None
    env_path = posixpath.join(workdir, "env", f"{slot['slot_index']}.env")
    try:
        out, status = _sudo_exec(conn.transport, f"cat {shlex.quote(env_path)}", password)
        if status != 0:
            return None
        env: dict[str, str] = {}
        for line in out.splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
        token = env.get("FACTORY_WORKER_TOKEN")
        api_url = env.get("FACTORY_API_URL") or api_base_url
        if not token or not api_url:
            return None

        config = json.dumps({
            "mcpServers": {
                "factory": {
                    "type": "http",
                    "url": f"{api_url.rstrip('/')}/mcp",
                    "headers": {"X-Worker-Token": token},
                }
            }
        })
        remote_path = f"/tmp/.claude-terminal-mcp-{agent_slot_id}.json"
        return _write_session_secret_file(conn, remote_path, config, slot, password)
    except Exception:
        return None


def build_claude_launch_command(
    slot: dict, mcp_path: str | None, token_path: str | None, askpass_path: str | None
) -> str:
    """The command typed into the shell right after `invoke_shell()`.

    Deliberately `sudo -u <user> bash -lc <inner>` — *not* `-i`. `-i` makes
    sudo exec the target's *passwd* shell as the wrapper, and every agent
    slot's passwd shell is `/usr/sbin/nologin` (agent_provision.py) so it
    silently refuses and the session falls back to the admin login,
    unnoticed. `-H` sets `$HOME` to the target's home directory without
    going through that shell substitution.

    Escalation on a password-auth server goes through `sudo -A` plus
    `askpass_path` — `_provision_claude_askpass()`'s `SUDO_ASKPASS` helper —
    never `-n` alone. Live UAT first caught a real credential leak: typing
    `sudo -S -p '' ...` followed by the password as a second line into this
    *interactive, human-visible* channel got the password echoed straight
    into the terminal transcript (the pty's own line discipline, not
    something `-S`'s empty prompt suppresses the way a direct /dev/tty grab
    would). Falling back to bare `-n` afterward then just failed outright on
    a box with no passwordless sudo configured — `sudo -A` is the actual fix
    for both: it authenticates via the out-of-band askpass program (an SFTP
    write, never typed, never echoed) instead of either the pty or a
    preconfigured NOPASSWD rule. `askpass_path` is None for a key-auth login
    with no stored password, in which case `-n` is the only option and
    passwordless sudo must already be configured, same prerequisite
    provisioning itself has.

    A subscription-billed agent's own turns can authenticate two ways, in a
    fixed order (`subscription_env()`, US-52.2): a factory-held (Vault) token
    for the org, when one exists, always wins; otherwise the machine's own
    per-slot `CLAUDE_CODE_OAUTH_TOKEN` — written into this same slot's
    `{workdir}/env/{index}.env` by the "Connect Claude" script — is the
    fallback. Neither is sourced by a plain login shell on its own (that env
    file isn't `.bashrc`/`.profile`, and the factory token never touches disk
    at all), so without help a human session hits Claude Code's first-run/
    auth wizard instead of the agent's own connected session. This sources
    the machine env file first (best-effort — the target user already owns
    it) and, when the caller resolved a factory token, `token_path` second so
    it overrides — the same precedence `subscription_env()` gives the
    automated run by injecting over `os.environ`.
    """
    mcp_flags = f" --mcp-config {shlex.quote(mcp_path)} --strict-mcp-config" if mcp_path else ""
    warn = "" if mcp_path else "echo 'Factory MCP unavailable for this session.'; "
    source_env = ""
    if slot.get("workdir"):
        env_path = posixpath.join(slot["workdir"], "env", f"{slot['slot_index']}.env")
        source_env += f"set -a; . {shlex.quote(env_path)} 2>/dev/null; set +a; "
    source_env += (
        f"echo '[terminal] factory credential: {'found' if token_path else 'none'}'; "
    )
    if token_path:
        source_env += f". {shlex.quote(token_path)} 2>/dev/null; "
    # Diagnostic, not a guess: report what claude will actually see, since
    # three prior fixes each looked right in isolation and still weren't —
    # this answers "is there a credential at all" directly from the box
    # instead of theorizing about where it should have come from.
    source_env += (
        'if [ -n "$CLAUDE_CODE_OAUTH_TOKEN" ]; then '
        'echo "[terminal] CLAUDE_CODE_OAUTH_TOKEN is set (${#CLAUDE_CODE_OAUTH_TOKEN} chars)"; '
        'else echo "[terminal] CLAUDE_CODE_OAUTH_TOKEN is NOT set - no subscription '
        'credential reached this shell"; fi; '
    )
    inner = f"{warn}{source_env}cd {shlex.quote(slot['workspace'])} && exec claude{mcp_flags}"
    sudo_flags = "-A" if askpass_path else "-n"
    prefix = f"SUDO_ASKPASS={shlex.quote(askpass_path)} " if askpass_path else ""
    return (
        f"{prefix}sudo {sudo_flags} -H -u {shlex.quote(slot['as_user'])} "
        f"bash -lc {shlex.quote(inner)}"
    )


async def _require_manage_org(org_id: str, user: AuthUser, settings: Settings) -> None:
    try:
        ok = await rpc(
            settings, user.token, "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_org"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(status_code=403, detail="Only an owner can open an agent's Claude terminal.")


# ---------------------------------------------------------------------------
# Registry CRUD + credentials (US-1.28)
# ---------------------------------------------------------------------------


class CreateServerBody(BaseModel):
    org_id: str
    name: str
    host: str
    port: int = 22
    username: str
    auth_method: Literal["password", "ssh_key"]
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


@router.post("")
async def create_server(
    body: CreateServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    if not body.name.strip() or not body.host.strip() or not body.username.strip():
        raise HTTPException(status_code=400, detail="Name, host, and username are required.")
    if body.auth_method == "password" and not body.password:
        raise HTTPException(status_code=400, detail="Enter a password.")
    if body.auth_method == "ssh_key" and not body.private_key:
        raise HTTPException(status_code=400, detail="Paste a private key.")

    # Validate an ssh key up front (also yields the fingerprint) before insert.
    fingerprint: str | None = None
    if body.auth_method == "ssh_key":
        try:
            fingerprint = await asyncio.to_thread(
                ssh.public_key_fingerprint, body.private_key, body.passphrase
            )
        except ssh.SSHError as e:
            raise HTTPException(status_code=400, detail=e.message)

    try:
        rows = await postgrest_post(
            settings, user.token, "servers",
            {
                "org_id": body.org_id,
                "name": body.name.strip(),
                "host": body.host.strip(),
                "port": body.port,
                "username": body.username.strip(),
                "auth_method": body.auth_method,
                "key_fingerprint": fingerprint,
            },
        )
    except Exception as e:
        msg = str(e)
        if "duplicate key" in msg or "servers_org_id_name_key" in msg:
            raise HTTPException(status_code=409, detail="A server with that name already exists.")
        if "row-level security" in msg or "42501" in msg:
            raise HTTPException(status_code=403, detail="Not a member of that organization.")
        raise HTTPException(status_code=400, detail="Could not create server.")

    server = rows[0]
    prefix = storage.server_prefix(body.org_id, server["id"])
    try:
        if body.auth_method == "password":
            await storage.put_object(settings, f"{prefix}/password", body.password.encode("utf-8"))
        else:
            await storage.put_object(settings, f"{prefix}/ssh_key", body.private_key.encode("utf-8"))
            if body.passphrase:
                await storage.put_object(
                    settings, f"{prefix}/ssh_key_passphrase", body.passphrase.encode("utf-8")
                )
    except storage.StorageError:
        # Roll back the row so we never leave a credential-less server behind.
        await postgrest_delete(settings, user.token, "servers", {"id": f"eq.{server['id']}"})
        raise HTTPException(status_code=502, detail="Could not store the credential. Try again.")

    return server


class UpdateServerBody(BaseModel):
    name: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    auth_method: Literal["password", "ssh_key"] | None = None
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None


@router.patch("/{server_id}")
async def update_server(
    server_id: str,
    body: UpdateServerBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    prefix = storage.server_prefix(server["org_id"], server_id)
    new_auth = body.auth_method or server["auth_method"]

    patch: dict = {"auth_method": new_auth}
    if body.name is not None:
        patch["name"] = body.name.strip()
    if body.host is not None:
        patch["host"] = body.host.strip()
    if body.port is not None:
        patch["port"] = body.port
    if body.username is not None:
        patch["username"] = body.username.strip()

    # Credential replacement (only when new material is supplied, or when the
    # auth method is switching and therefore needs a credential of that kind).
    try:
        if new_auth == "password":
            if body.password:
                await storage.put_object(
                    settings, f"{prefix}/password", body.password.encode("utf-8")
                )
                await storage.delete_prefix_keys(settings, prefix, ["ssh_key", "ssh_key_passphrase"])
                patch["key_fingerprint"] = None
            elif server["auth_method"] != "password":
                raise HTTPException(status_code=400, detail="Enter a password for this server.")
        else:  # ssh_key
            if body.private_key:
                try:
                    fingerprint = await asyncio.to_thread(
                        ssh.public_key_fingerprint, body.private_key, body.passphrase
                    )
                except ssh.SSHError as e:
                    raise HTTPException(status_code=400, detail=e.message)
                await storage.put_object(
                    settings, f"{prefix}/ssh_key", body.private_key.encode("utf-8")
                )
                if body.passphrase:
                    await storage.put_object(
                        settings, f"{prefix}/ssh_key_passphrase", body.passphrase.encode("utf-8")
                    )
                else:
                    await storage.delete_prefix_keys(settings, prefix, ["ssh_key_passphrase"])
                await storage.delete_prefix_keys(settings, prefix, ["password"])
                patch["key_fingerprint"] = fingerprint
            elif server["auth_method"] != "ssh_key":
                raise HTTPException(status_code=400, detail="Paste a private key for this server.")
    except storage.StorageError:
        raise HTTPException(status_code=502, detail="Could not store the credential. Try again.")

    try:
        rows = await postgrest_patch(
            settings, user.token, "servers", {"id": f"eq.{server_id}"}, patch
        )
    except Exception as e:
        msg = str(e)
        if "duplicate key" in msg:
            raise HTTPException(status_code=409, detail="A server with that name already exists.")
        raise HTTPException(status_code=400, detail="Could not update server.")
    if not rows:
        raise HTTPException(status_code=404, detail="Server not found")
    return rows[0]


@router.delete("/{server_id}")
async def delete_server(
    server_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    prefix = storage.server_prefix(server["org_id"], server_id)
    try:
        await postgrest_delete(settings, user.token, "servers", {"id": f"eq.{server_id}"})
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 409:
            raise
        # FK on delete restrict: deployments still point here (US-1.31).
        deployments = await postgrest_get(
            settings,
            user.token,
            "deployments",
            {"select": "name", "server_id": f"eq.{server_id}", "order": "name"},
        )
        names = ", ".join(d["name"] for d in deployments) or "unknown"
        raise HTTPException(
            status_code=409,
            detail=f"Server is used by deployment(s): {names}. Delete or repoint those deployments first.",
        )
    try:
        await storage.delete_prefix(settings, prefix)
    except storage.StorageError:
        pass  # row is already gone; orphaned objects are unreachable but harmless
    return {"ok": True}


@router.post("/{server_id}/test")
async def test_connection(
    server_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    conn.close()
    return {"ok": True, "host_key_fingerprint": conn.host_key_fingerprint}


class TestConnectionBody(BaseModel):
    """US-20.4: a dry-run connection from form values — nothing is stored."""

    host: str
    port: int = 22
    username: str
    auth_method: Literal["password", "ssh_key"]
    password: str | None = None
    private_key: str | None = None
    passphrase: str | None = None
    # Optional: an existing server whose trusted host key must still be
    # honoured while its credential is being replaced in the edit form.
    server_id: str | None = None


@router.post("/test-connection")
async def test_connection_dry_run(
    body: TestConnectionBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """US-20.4: try these credentials without creating anything.

    Writes NOTHING — no `servers` row, no object in the private data
    bucket, and no host-key capture. Trust-on-first-use still belongs to
    the first real connection after the server exists; recording a
    fingerprint here would mean writing a server the manager never asked
    for. The credential is never echoed back and never logged.
    """
    if not body.host.strip() or not body.username.strip():
        raise HTTPException(status_code=400, detail="Host and username are required.")
    if not 1 <= body.port <= 65535:
        raise HTTPException(status_code=400, detail="Port must be between 1 and 65535.")

    # An existing server's trusted host key is enforced even on a dry run, so
    # a changed key surfaces here exactly as it does everywhere else. RLS on
    # the lookup is the cross-org gate, as for every other endpoint here.
    expected: str | None = None
    server: dict | None = None
    if body.server_id:
        server = await get_server_for_user(settings, user.token, body.server_id)
        expected = server.get("host_key_fingerprint")

    if body.auth_method == "password":
        if body.password:
            creds = ssh.Credentials(password=body.password)
        elif server is not None:
            creds = await resolve_credentials(settings, server)
        else:
            raise HTTPException(status_code=400, detail="Enter a password.")
    else:
        if body.private_key and body.private_key.strip():
            try:
                await asyncio.to_thread(
                    ssh.public_key_fingerprint, body.private_key, body.passphrase
                )
            except ssh.SSHError as e:
                raise HTTPException(status_code=400, detail=e.message)
            creds = ssh.Credentials(
                private_key=body.private_key, passphrase=body.passphrase
            )
        elif server is not None:
            creds = await resolve_credentials(settings, server)
        else:
            raise HTTPException(status_code=400, detail="Paste or upload a private key.")

    try:
        conn = await asyncio.to_thread(
            ssh.open_connection,
            host=body.host.strip(),
            port=body.port,
            username=body.username.strip(),
            auth_method=body.auth_method,
            creds=creds,
            expected_host_fingerprint=expected,
        )
    except ssh.HostKeyChanged as e:
        raise HTTPException(status_code=409, detail=e.message)
    except ssh.SSHError as e:
        raise HTTPException(status_code=502, detail=e.message)

    fingerprint = conn.host_key_fingerprint
    conn.close()
    return {"ok": True, "host_key_fingerprint": fingerprint}


@router.post("/{server_id}/trust-host-key")
async def trust_host_key(
    server_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Reset the trusted host key so the next connect re-captures it (US-1.28)."""
    server = await get_server_for_user(settings, user.token, server_id)
    await postgrest_patch(
        settings, user.token, "servers", {"id": f"eq.{server_id}"},
        {"host_key_fingerprint": None},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# File manager over SFTP (US-1.30) + text editor (US-1.46)
# ---------------------------------------------------------------------------


@router.get("/{server_id}/files")
async def list_files(
    server_id: str,
    path: str = Query("."),
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        target = path if path and path != "~" else await asyncio.to_thread(
            sftp_ops.home_dir, conn.transport
        )
        return await asyncio.to_thread(sftp_ops.list_dir, conn.transport, target)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    except IOError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        conn.close()


@router.post("/{server_id}/files/upload")
async def upload_file(
    server_id: str,
    path: str = Query(...),
    file: UploadFile = File(...),
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    remote = posixpath.join(path, file.filename)

    def do_upload() -> None:
        client = paramiko.SFTPClient.from_transport(conn.transport)
        if client is None:
            raise sftp_ops.SftpError("Could not open an SFTP channel.")
        with client.open(remote, "wb") as rf:
            rf.set_pipelined(True)
            while True:
                chunk = file.file.read(CHUNK)
                if not chunk:
                    break
                rf.write(chunk)

    try:
        await asyncio.to_thread(do_upload)
    except (sftp_ops.SftpError, IOError) as e:
        raise HTTPException(status_code=400, detail=getattr(e, "message", None) or str(e))
    finally:
        conn.close()
    return {"ok": True, "name": file.filename}


@router.get("/{server_id}/files/download")
async def download_file(
    server_id: str,
    path: str = Query(...),
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        client = await asyncio.to_thread(paramiko.SFTPClient.from_transport, conn.transport)
        st = await asyncio.to_thread(client.stat, path)
        handle = await asyncio.to_thread(client.open, path, "rb")
    except IOError:
        conn.close()
        raise HTTPException(status_code=404, detail="File not found")

    handle.prefetch(st.st_size or 0)
    filename = posixpath.basename(path) or "download"
    # Header values must stay ASCII and quote-safe; remote filenames aren't.
    ascii_name = (
        filename.encode("ascii", "replace").decode().replace('"', "'").replace("\r", " ").replace("\n", " ")
    )
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"

    async def stream():
        try:
            while True:
                chunk = await asyncio.to_thread(handle.read, CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)
            conn.close()

    return StreamingResponse(
        stream(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(st.st_size or 0),
        },
    )


class PathBody(BaseModel):
    path: str


@router.post("/{server_id}/files/mkdir")
async def make_folder(
    server_id: str,
    body: PathBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        await asyncio.to_thread(sftp_ops.make_dir, conn.transport, body.path)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()
    return {"ok": True}


class DeleteBody(BaseModel):
    path: str
    recursive: bool = False


@router.post("/{server_id}/files/delete")
async def delete_path(
    server_id: str,
    body: DeleteBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        await asyncio.to_thread(sftp_ops.remove, conn.transport, body.path, body.recursive)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()
    return {"ok": True}


class ExtractBody(BaseModel):
    path: str


@router.post("/{server_id}/files/extract")
async def extract_archive(
    server_id: str,
    body: ExtractBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    dest = posixpath.dirname(body.path) or "."
    try:
        await asyncio.to_thread(sftp_ops.extract_zip, conn.transport, body.path, dest)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()
    return {"ok": True}


@router.get("/{server_id}/files/read")
async def read_file(
    server_id: str,
    path: str = Query(...),
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        return await asyncio.to_thread(sftp_ops.read_text, conn.transport, path)
    except sftp_ops.NotEditable as e:
        raise HTTPException(status_code=422, detail=e.message)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()


class WriteBody(BaseModel):
    path: str
    content: str
    eol: Literal["lf", "crlf"] = "lf"
    expected_mtime: int | None = None
    expected_size: int | None = None
    force: bool = False


@router.post("/{server_id}/files/write")
async def write_file(
    server_id: str,
    body: WriteBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        return await asyncio.to_thread(
            sftp_ops.write_text,
            conn.transport,
            body.path,
            body.content,
            body.eol,
            body.expected_mtime,
            body.expected_size,
            body.force,
        )
    except sftp_ops.SftpConflict as e:
        raise HTTPException(status_code=409, detail=e.message)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()


@router.post("/{server_id}/files/new")
async def new_file(
    server_id: str,
    body: PathBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    server = await get_server_for_user(settings, user.token, server_id)
    conn = await connect_server(settings, user.token, server)
    try:
        await asyncio.to_thread(sftp_ops.create_file, conn.transport, body.path)
    except sftp_ops.SftpError as e:
        raise HTTPException(status_code=400, detail=e.message)
    finally:
        conn.close()
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket SSH terminal (US-1.29)
# ---------------------------------------------------------------------------


async def _authenticate_ws(settings: Settings, token: str) -> AuthUser:
    from ..auth import get_signing_key

    try:
        signing_key = get_signing_key(token, settings)
        claims = jwt.decode(
            token, signing_key, algorithms=["ES256", "RS256"], audience="authenticated"
        )
    except Exception:
        raise ValueError("invalid token")
    return AuthUser(id=claims["sub"], email=claims.get("email", ""), token=token)


@router.websocket("/{server_id}/terminal")
async def terminal(
    websocket: WebSocket,
    server_id: str,
    settings: Settings = Depends(get_settings),
):
    if not await safe_accept(websocket):
        return

    # First frame must be an auth handshake — browsers can't set WS headers,
    # so credentials never appear in the URL.
    try:
        first = await asyncio.wait_for(websocket.receive_text(), timeout=15)
        hello = json.loads(first)
        token = hello.get("token", "")
        cols = _pty_dim(hello.get("cols"), 80)
        rows = _pty_dim(hello.get("rows"), 24)
        agent_slot_id = hello.get("agentSlotId") or None
    except Exception:
        await websocket.close(code=4401)
        return

    try:
        user = await _authenticate_ws(settings, token)
    except ValueError:
        await websocket.send_json({"type": "error", "message": "Authentication failed."})
        await websocket.close(code=4401)
        return

    try:
        server = await get_server_for_user(settings, user.token, server_id)
    except HTTPException:
        await websocket.send_json({"type": "error", "message": "Server not found."})
        await websocket.close(code=4404)
        return

    claude_slot: dict | None = None
    if agent_slot_id:
        try:
            claude_slot = await resolve_claude_terminal_slot(settings, user, server, agent_slot_id)
        except HTTPException as e:
            await websocket.send_json({"type": "error", "message": str(e.detail)})
            await websocket.close(code=4403 if e.status_code in (400, 403, 404) else 1011)
            return

    try:
        conn = await connect_server(settings, user.token, server)
    except HTTPException as e:
        code = "host_key_changed" if e.status_code == 409 else "connect_failed"
        await websocket.send_json({"type": "error", "code": code, "message": str(e.detail)})
        await websocket.close(code=1011)
        return

    claude_exec: str | None = None
    claude_mcp_path: str | None = None
    claude_token_path: str | None = None
    claude_askpass_path: str | None = None
    if claude_slot:
        # sudo_wrap's password-over-stdin rule is safe for the one-shot
        # background calls below (a plain exec_command whose output is never
        # relayed to the browser) but NOT for the interactive launch itself —
        # see build_claude_launch_command's docstring for the leak that
        # taught us this the hard way. That launch escalates via a
        # SUDO_ASKPASS helper (_provision_claude_askpass) instead.
        sudo_password = (await resolve_credentials(settings, server)).password
        claude_mcp_path = await asyncio.to_thread(
            _provision_claude_mcp_config,
            conn, settings.api_base_url, claude_slot, agent_slot_id, sudo_password,
        )
        # US-52.2's precedence: a factory-held (Vault) subscription token for
        # this org, when one exists, is what the automated agent actually
        # authenticates with — not necessarily the machine's own per-slot
        # token. Read the same way runner_socket.py does for a live run.
        factory_token = await asyncio.to_thread(
            llm_service.read_claude_subscription_token, settings, server["org_id"]
        )
        if factory_token:
            claude_token_path = await asyncio.to_thread(
                _provision_claude_token_file,
                conn, factory_token, claude_slot, agent_slot_id, sudo_password,
            )
        if sudo_password:
            claude_askpass_path = await asyncio.to_thread(
                _provision_claude_askpass, conn, sudo_password, agent_slot_id
            )
        claude_exec = build_claude_launch_command(
            claude_slot, claude_mcp_path, claude_token_path, claude_askpass_path
        )

    chan = conn.transport.open_session()
    chan.get_pty(term="xterm-256color", width=cols, height=rows)
    chan.invoke_shell()
    if claude_exec:
        chan.send((claude_exec + "\n").encode("utf-8"))
    await websocket.send_json({"type": "ready"})

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue = asyncio.Queue()
    last_activity = [loop.time()]

    def reader() -> None:
        try:
            while True:
                data = chan.recv(4096)
                if not data:
                    break
                loop.call_soon_threadsafe(out_queue.put_nowait, data)
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(out_queue.put_nowait, None)

    import threading

    threading.Thread(target=reader, daemon=True).start()

    async def pump_out() -> None:
        while True:
            data = await out_queue.get()
            if data is None:
                return
            last_activity[0] = loop.time()
            await websocket.send_bytes(data)

    async def pump_in() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            last_activity[0] = loop.time()
            text = message.get("text")
            if text is None:
                raw = message.get("bytes")
                if raw:
                    chan.send(raw)
                continue
            try:
                obj = json.loads(text)
            except ValueError:
                continue
            kind = obj.get("type")
            if kind == "input":
                chan.send(obj.get("data", "").encode("utf-8"))
            elif kind == "resize":
                chan.resize_pty(
                    width=_pty_dim(obj.get("cols"), 80),
                    height=_pty_dim(obj.get("rows"), 24),
                )
            elif kind == "disconnect":
                return

    async def watchdog() -> None:
        while True:
            await asyncio.sleep(30)
            if loop.time() - last_activity[0] > IDLE_TIMEOUT:
                try:
                    await websocket.send_json({"type": "closed", "reason": "Idle timeout."})
                except Exception:
                    pass
                return

    tasks = [asyncio.create_task(pump_out()), asyncio.create_task(pump_in()), asyncio.create_task(watchdog())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    finally:
        try:
            chan.close()
        except Exception:
            pass
        # All three carry a live secret — never outlive the session they
        # were minted for, same rule the runner itself follows for the
        # automated equivalent (mcpconfig.remove()).
        for secret_path in (claude_mcp_path, claude_token_path):
            if not secret_path:
                continue
            try:
                await asyncio.to_thread(
                    _sudo_exec, conn.transport, f"rm -f {shlex.quote(secret_path)}", sudo_password
                )
            except Exception:
                pass
        if claude_askpass_path:
            # Owned by the connecting admin login itself — no sudo needed.
            try:
                await asyncio.to_thread(
                    _exec_and_capture, conn.transport, f"rm -f {shlex.quote(claude_askpass_path)}"
                )
            except Exception:
                pass
        conn.close()
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Claude subscription connect (US-52.3)
# ---------------------------------------------------------------------------
# The app hosts the flow; the machine keeps the secret. `prepare` checks the
# claude CLI is present and installs a small interactive script; the manager
# runs it in the in-app terminal (Claude Code itself performs the OAuth in
# their browser); `verify` records only that slot envs now carry the token
# (a presence grep - values are never read); `disconnect` strips it again.
# Nothing here reads, stores or logs the token.


def connect_claude_script(workdir: str) -> str:
    """The interactive connect script installed at {workdir}/bin/connect-claude.

    Runs on the machine, in the manager's own terminal session. The token is
    pasted into `read -rs` (hidden, no shell history) and written via a
    printf | sudo tee pipe (a builtin, so it never appears on an argv that
    `ps` or an audit line could see).
    """
    return f"""#!/usr/bin/env bash
# Build Mill (US-52.3): connect this machine's Claude subscription.
# The token is minted by Claude Code itself and never leaves this machine.
set -u
umask 077
WORKDIR={workdir!r}
echo "Step 1/2 - Claude login. Open the URL it prints in YOUR browser,"
echo "sign in as yourself, and follow the flow to the end."
echo
claude setup-token
echo
echo "Step 2/2 - paste the sk-ant-oat... token it printed (input is hidden):"
IFS= read -rs TOKEN
echo
case "$TOKEN" in
  sk-ant-oat*) ;;
  *) echo "That does not look like a subscription token (sk-ant-oat...). Nothing changed."; exit 1 ;;
esac
changed=0
for f in "$WORKDIR"/env/*.env; do
  [ -e "$f" ] || continue
  sudo sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d' "$f"
  printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\\n' "$TOKEN" | sudo tee -a "$f" >/dev/null
  changed=$((changed+1))
  i=$(basename "$f" .env)
  sudo systemctl restart "buildmill-agent@$i" && echo "restarted buildmill-agent@$i"
done
if [ "$changed" -eq 0 ]; then
  echo "No agent slot env files under $WORKDIR/env - is this machine provisioned?"
  exit 1
fi
echo
echo "Token installed into $changed slot(s). Agents were restarted; a run that"
echo "was in flight here will fail and be retried by the factory."
echo "Back in Build Mill, press 'Verify connection' to record it."
"""


async def _agent_host_for(settings: Settings, token: str, server_id: str) -> dict:
    """The agent host on this machine, or the refusal that explains."""
    rows = await postgrest_get(
        settings,
        token,
        "agent_servers",
        {
            "select": "id,workdir,status",
            "server_id": f"eq.{server_id}",
            "status": "neq.removed",
            "limit": "1",
        },
    )
    if not rows:
        raise HTTPException(
            status_code=409,
            detail=(
                "This machine is not provisioned to run agents - there is no "
                "runner environment to hold a token. Set up coding agents on "
                "it first."
            ),
        )
    return rows[0]


async def _sudo_password(settings: Settings, server: dict) -> str | None:
    creds = await resolve_credentials(settings, server)
    return creds.password


@router.post("/{server_id}/claude-subscription/prepare")
async def claude_subscription_prepare(
    server_id: str,
    settings: Settings = Depends(get_settings),
    user: AuthUser = Depends(verify_token),
):
    """Check the CLI, install the connect script, and hand back the command."""
    from .. import agent_provision

    server = await get_server_for_user(settings, user.token, server_id)
    host = await _agent_host_for(settings, user.token, server_id)
    workdir = str(host["workdir"]).rstrip("/")
    conn = await connect_server(settings, user.token, server)
    try:
        status, _ = await asyncio.to_thread(
            agent_provision.quiet,
            conn.transport,
            "command -v claude >/dev/null 2>&1",
        )
        if status != 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    "The claude CLI is not installed on this machine, so there "
                    "is nothing to connect. Run Update on its agents first - "
                    "provisioning installs the CLIs."
                ),
            )
        script = connect_claude_script(workdir)
        password = await _sudo_password(settings, server)
        install = (
            f"mkdir -p {workdir}/bin"
            f" && cat > {workdir}/bin/connect-claude << 'BM_EOF'\n{script}\nBM_EOF\n"
            f"chmod 755 {workdir}/bin/connect-claude"
        )
        rc, lines = await asyncio.to_thread(
            lambda: agent_provision.run(
                conn.transport, install, sudo=True, password=password, echo=False
            )
        )
        if rc != 0:
            tail = "; ".join(lines[-3:]) or f"exit {rc}"
            raise HTTPException(
                status_code=502, detail=f"could not install the connect script: {tail}"
            )
        return {"command": f"{workdir}/bin/connect-claude"}
    finally:
        conn.close()


@router.post("/{server_id}/claude-subscription/verify")
async def claude_subscription_verify(
    server_id: str,
    settings: Settings = Depends(get_settings),
    user: AuthUser = Depends(verify_token),
):
    """Record whether the slot envs carry the token - presence only, never values."""
    from .. import agent_provision

    server = await get_server_for_user(settings, user.token, server_id)
    host = await _agent_host_for(settings, user.token, server_id)
    workdir = str(host["workdir"]).rstrip("/")
    conn = await connect_server(settings, user.token, server)
    try:
        password = await _sudo_password(settings, server)
        rc, lines = await asyncio.to_thread(
            lambda: agent_provision.run(
                conn.transport,
                f"grep -ls '^CLAUDE_CODE_OAUTH_TOKEN=' {workdir}/env/*.env 2>/dev/null | wc -l",
                sudo=True,
                password=password,
                echo=False,
            )
        )
    finally:
        conn.close()
    try:
        slots = int((lines[-1] if lines else "0").strip())
    except ValueError:
        slots = 0
    connected = rc == 0 and slots > 0
    with agent_provision._connect(settings) as pg:
        if connected:
            pg.execute(
                "update public.agent_servers set claude_connected_at = now()"
                " where id = %s",
                (host["id"],),
            )
        else:
            pg.execute(
                "update public.agent_servers set claude_connected_at = null"
                " where id = %s",
                (host["id"],),
            )
        pg.commit()
    return {"connected": connected, "slots": slots}


@router.post("/{server_id}/claude-subscription/disconnect")
async def claude_subscription_disconnect(
    server_id: str,
    settings: Settings = Depends(get_settings),
    user: AuthUser = Depends(verify_token),
):
    """Strip the token from every slot env, restart the agents, clear the marker."""
    from .. import agent_provision

    server = await get_server_for_user(settings, user.token, server_id)
    host = await _agent_host_for(settings, user.token, server_id)
    workdir = str(host["workdir"]).rstrip("/")
    conn = await connect_server(settings, user.token, server)
    try:
        password = await _sudo_password(settings, server)
        strip = (
            f'for f in {workdir}/env/*.env; do'
            f' [ -e "$f" ] || continue;'
            f" sed -i '/^CLAUDE_CODE_OAUTH_TOKEN=/d' \"$f\";"
            f' i=$(basename "$f" .env);'
            f' systemctl restart "buildmill-agent@$i" || true;'
            f" done"
        )
        rc, lines = await asyncio.to_thread(
            lambda: agent_provision.run(
                conn.transport, strip, sudo=True, password=password, echo=False
            )
        )
        if rc != 0:
            tail = "; ".join(lines[-3:]) or f"exit {rc}"
            raise HTTPException(
                status_code=502, detail=f"could not remove the token: {tail}"
            )
    finally:
        conn.close()
    with agent_provision._connect(settings) as pg:
        pg.execute(
            "update public.agent_servers set claude_connected_at = null where id = %s",
            (host["id"],),
        )
        pg.commit()
    return {"connected": False}
