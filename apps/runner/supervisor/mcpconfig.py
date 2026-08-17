"""Per-run MCP config for the agent's own CLI (US-31.9).

There was not one reference to MCP anywhere in `apps/runner`. The supervisor
cloned through the factory git remote, ran the CLI against a prompt assembled
by `cli_base._sections()`, then committed and pushed — so the agent never
learned the factory had a tool surface at all.

Meanwhile that surface exists, is complete, and is proven: `get_workspace`
delivers a tree with no git tooling, `submit_changeset` takes changed files and
the FACTORY does all the git, and `get_work_context`,
`get_project_guidelines`, `read_repo_file`, `report_progress`,
`request_clarification` and `report_test_results` are all sitting there unused
by the fleet. A manual end-to-end run shipped a merged PR through exactly this
path in July.

**No new secret reaches the machine.** The MCP endpoint authenticates with
`X-Worker-Token`, and that token is already in the slot's 0600 env file — it is
the one secret an agent box is allowed to hold. The config file is written 0600
for the run and removed after it, and the token never appears in a log, a
trace or an audit line.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import stat
from pathlib import Path
from typing import Any

logger = logging.getLogger("supervisor.mcpconfig")

CONFIG_NAME = ".factory-mcp.json"

# us-115.1: the suffix that marks a directory as one of ours — a per-run holder
# for a config a CLI does not read out of the checkout. `remove()` deletes such
# a directory once it is empty, so it never lingers in the workspace root
# looking like a workspace to `workspace.usage()` and `reclaim()`.
CONFIG_DIR_SUFFIX = ".mcp"


def endpoint(api_url: str, project_id: str | None = None) -> str:
    """The factory's MCP URL. A project-scoped url (US-3.14) when we know the
    project, so the agent's pool view matches the run it is doing.

    Trailing slash on purpose (2026-08-13): the slashless path 307-redirects
    at the mount, and newer MCP clients refuse redirected MCP servers — a
    release prep ran toolless on exactly that. The API also serves /mcp
    directly now; this makes new configs correct by construction."""
    base = (api_url or "").rstrip("/")
    return f"{base}/mcp/"


def build(
    api_url: str,
    token: str,
    project_id: str | None = None,
    tool_servers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """The mcp.json body: the factory server, plus whatever this run was granted.

    US-34.2: the factory server stays and the granted ones are added BESIDE it,
    so `--strict-mcp-config` continues to mean the agent's tool surface is
    exactly what the factory granted.

    A credentialed server points at the factory's proxy and carries the run's
    SCOPED key — never the server's own credential, which is the whole reason the
    proxy exists. A credential-free stdio server is configured directly, because a
    proxy hop for a local browser would add latency and a failure mode and protect
    nothing; it is still a catalog entry that had to be granted.

    US-89.1: when the loopback broker is up, the factory entry points at
    127.0.0.1 and carries only the machine-local key — the worker token never
    enters this body, so the file this becomes (and every translation of it:
    `.grok/config.toml`, the ACP server list) is worthless off the box. The
    token-in-config shape below survives only as the fallback for a machine
    where the broker could not bind.
    """
    from . import mcp_broker

    broker = mcp_broker.info()
    if broker is not None:
        factory_url, local_key = broker
        factory_entry: dict[str, Any] = {
            "type": "http",
            "url": factory_url,
            "headers": {"X-Factory-Local-Key": local_key},
        }
    else:
        factory_entry = {
            "type": "http",
            "url": endpoint(api_url, project_id),
            "headers": {"X-Worker-Token": token},
        }
    servers: dict[str, Any] = {"factory": factory_entry}
    for entry in tool_servers or []:
        slug = str(entry.get("slug") or "").strip()
        if not slug or slug == "factory":
            # Never let a catalog entry shadow the factory's own server: that is
            # how a run loses the ability to hand work back.
            continue
        if entry.get("transport") == "stdio":
            command = str(entry.get("command") or "").strip()
            if not command:
                continue
            parts = shlex.split(command, posix=(os.name != "nt"))
            servers[slug] = {
                "type": "stdio",
                "command": parts[0],
                "args": parts[1:],
            }
            continue
        url = str(entry.get("url") or "").strip()
        key = str(entry.get("key") or "").strip()
        if not url or not key:
            continue
        servers[slug] = {
            "type": "http",
            "url": url,
            "headers": {"X-Factory-MCP-Key": key},
        }
    return {"mcpServers": servers}


def write(
    workdir: Path,
    api_url: str,
    token: str,
    project_id: str | None = None,
    tool_servers: list[dict[str, Any]] | None = None,
) -> Path | None:
    """Write the per-run config 0600 inside the workspace.

    Returns the path, or None when there is nothing to point at (no api url or
    no token) — the caller then runs without MCP and says so rather than
    handing the CLI a config that cannot authenticate.
    """
    if not api_url or not token:
        return None
    path = workdir / CONFIG_NAME
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                build(api_url, token, project_id, tool_servers), indent=2
            ),
            encoding="utf-8",
        )
        # 0600: the file carries the worker token. On Windows (dev) chmod is
        # a no-op, which is why the real protection is the agent box's own
        # single-user layout — but on the Linux agents this is the guarantee.
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError as e:  # noqa: BLE001
        logger.warning("could not write MCP config in %s: %s", workdir, e)
        return None
    return path


def remove(workdir: Path) -> None:
    """Delete the config — called on success AND on failure, so a token-bearing
    file never outlives the run that needed it.

    A holder directory this module owns (`CONFIG_DIR_SUFFIX`) goes with it once
    empty. The name check is the guard: a real workspace is never removed here,
    whatever the caller passes.
    """
    try:
        (workdir / CONFIG_NAME).unlink(missing_ok=True)
    except OSError as e:  # noqa: BLE001
        logger.warning("could not remove MCP config in %s: %s", workdir, e)
        return
    if workdir.name.endswith(CONFIG_DIR_SUFFIX):
        try:
            workdir.rmdir()  # refuses a non-empty directory, which is the point
        except OSError:  # noqa: BLE001 — not empty, or already gone
            pass


# ---------------------------------------------------------------------------
# us-115.1: the same servers, in the shape a CLI reads from its OWN config.
# ---------------------------------------------------------------------------

# The env var the rendered config points at for the factory's credential. One
# name for both shapes — broker local key or worker token — because the config
# never says which it is holding, only where to read it.
FACTORY_KEY_ENV = "FACTORY_MCP_KEY"


def _toml_string(value: str) -> str:
    """A TOML basic string. Escapes are the four TOML requires for one-liners;
    a URL or a header name containing a newline is not a thing we render."""
    out = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"' + out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t") + '"'


def _toml_inline_table(pairs: dict[str, str]) -> str:
    body = ", ".join(f"{_toml_string(k)} = {_toml_string(v)}" for k, v in pairs.items())
    return "{ " + body + " }"


def _env_var_for(slug: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in slug).upper().strip("_")
    return f"FACTORY_MCP_KEY_{safe or 'SERVER'}"


def to_grok_toml(
    config: dict[str, Any],
    *,
    startup_timeout_sec: int = 60,
    tool_timeout_sec: int | None = None,
) -> tuple[str, dict[str, str]]:
    """Render `build()`'s body as grok's `[mcp_servers.*]` TOML.

    Returns `(toml_text, env)` — the text to write into the CLI's config file,
    and the environment the CLI must be spawned with for it to mean anything.

    **No credential is ever in the text.** The factory entry names an env var
    (`bearer_token_env_var`), a granted entry writes `${VAR}` in its header
    value; both values ride in the returned mapping and reach the CLI through
    its process environment. That is us-89.1's rule — files travel, secrets
    must not — applied to the agent's own home rather than the workspace.

    `bearer_token_env_var` rather than a header for the factory because it also
    buys the handshake: a server that already carries an `Authorization` header
    skips the CLI's OAuth discovery entirely (four `.well-known` probes under a
    5s budget, all of which our servers answer with 401/403/404). The broker and
    the factory MCP mount accept that bearer form as of this story, so the
    header is a real credential and not a decoration.
    """
    servers = config.get("mcpServers") or {}
    env: dict[str, str] = {}
    blocks: list[str] = []
    for name in sorted(servers):
        entry = servers[name]
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "stdio")
        lines = [f"[mcp_servers.{_toml_string(name)}]"]
        if kind == "stdio":
            command = str(entry.get("command") or "").strip()
            if not command:
                logger.warning("MCP server %r has no command; not rendered", name)
                continue
            lines.append(f"command = {_toml_string(command)}")
            args = [str(a) for a in (entry.get("args") or [])]
            if args:
                rendered = ", ".join(_toml_string(a) for a in args)
                lines.append(f"args = [{rendered}]")
        else:
            url = str(entry.get("url") or "").strip()
            if not url:
                logger.warning("MCP server %r has no url; not rendered", name)
                continue
            lines.append(f"url = {_toml_string(url)}")
            headers = {
                str(k): str(v) for k, v in (entry.get("headers") or {}).items()
            }
            if name == "factory" and len(headers) == 1:
                # The one credential this run holds for the factory, whichever
                # shape it took, delivered as a bearer token.
                env[FACTORY_KEY_ENV] = next(iter(headers.values()))
                lines.append(f"bearer_token_env_var = {_toml_string(FACTORY_KEY_ENV)}")
            elif headers:
                # A granted server's scoped key (us-34.2): the header name is
                # the factory proxy's own, so it stays a header — with the
                # value behind a placeholder the CLI expands from the env.
                var = _env_var_for(name)
                placeholder = {}
                for header, value in headers.items():
                    env[var] = value
                    placeholder[header] = "${" + var + "}"
                lines.append(f"headers = {_toml_inline_table(placeholder)}")
        lines.append("enabled = true")
        lines.append(f"startup_timeout_sec = {int(startup_timeout_sec)}")
        if tool_timeout_sec:
            lines.append(f"tool_timeout_sec = {int(tool_timeout_sec)}")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks), env


async def probe(url: str, headers: dict[str, str], timeout: float = 30) -> list[str]:
    """Speak MCP to `url` and return its tool names.

    us-115.1: the run's proof that the factory's tool surface is reachable
    BEFORE a model is paid to discover it isn't. Raises `RuntimeError` naming
    what went wrong — the caller turns that into a refusal, and nothing is
    spent.

    Deliberately hand-rolled rather than the MCP SDK: the runner does not
    depend on it, and three POSTs is the whole client we need against a
    stateless, JSON-mode server.
    """
    import httpx

    wire = {
        **headers,
        "Content-Type": "application/json",
        # Both, always: the server may answer either, and a client that offers
        # only one gets a 406 from a spec-following implementation.
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2025-06-18",
    }

    def _rpc(rid: int | None, method: str, params: dict) -> dict:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if rid is not None:
            body["id"] = rid
        return body

    def _result(resp: "httpx.Response", what: str) -> dict:
        if resp.status_code >= 400:
            raise RuntimeError(
                f"{what} answered HTTP {resp.status_code}: {resp.text[:200]}"
            )
        ctype = resp.headers.get("content-type", "")
        if ctype.startswith("text/event-stream"):
            # One JSON-RPC message per `data:` line; the first is the answer.
            for line in resp.text.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RuntimeError(f"{what} returned an empty event stream")
        return resp.json()

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        try:
            init = await client.post(
                url,
                headers=wire,
                json=_rpc(
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "buildmill-runner", "version": "1"},
                    },
                ),
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"could not reach the factory MCP at {url}: {e}") from e
        payload = _result(init, "initialize")
        if "error" in payload:
            raise RuntimeError(f"initialize was refused: {payload['error']}")
        session = init.headers.get("mcp-session-id")
        if session:
            wire["Mcp-Session-Id"] = session
        # Best-effort: a stateless server does not need it, and a 4xx here is
        # not a reason to refuse a run whose initialize just succeeded.
        try:
            await client.post(
                url, headers=wire, json=_rpc(None, "notifications/initialized", {})
            )
        except httpx.HTTPError:  # noqa: BLE001
            pass
        try:
            listed = await client.post(
                url, headers=wire, json=_rpc(2, "tools/list", {})
            )
        except httpx.HTTPError as e:
            raise RuntimeError(f"tools/list failed: {e}") from e
        payload = _result(listed, "tools/list")
        if "error" in payload:
            raise RuntimeError(f"tools/list was refused: {payload['error']}")
        tools = ((payload.get("result") or {}).get("tools")) or []
        names = [str(t.get("name")) for t in tools if isinstance(t, dict)]
        if not names:
            raise RuntimeError("the factory MCP answered with no tools")
        return names
