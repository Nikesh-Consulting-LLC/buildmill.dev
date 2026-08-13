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
    file never outlives the run that needed it."""
    try:
        (workdir / CONFIG_NAME).unlink(missing_ok=True)
    except OSError as e:  # noqa: BLE001
        logger.warning("could not remove MCP config in %s: %s", workdir, e)
