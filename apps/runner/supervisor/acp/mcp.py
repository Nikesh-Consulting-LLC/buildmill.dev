"""The factory's MCP servers, in ACP's shape (US-78.4).

The other CLI modules hand their agent a config *file* and hope it is read —
`grok.py`'s own docstring calls its `.grok/config.toml` write "best-effort, not
a proven capability", because the subcommand that used to manage that file
disappeared between CLI generations. ACP passes servers as a parameter of
`session/new`, so this is a translation with a return value rather than a file
dropped on disk and a hope.

Shapes from the ACP schema:

    stdio (mandatory for every agent):
        {"name": "...", "command": "...", "args": [...],
         "env": [{"name": "...", "value": "..."}]}
    http:
        {"type": "http", "name": "...", "url": "...",
         "headers": [{"name": "...", "value": "..."}]}

The input is `mcpconfig.build()`'s body, so there is one description of the
factory's tool surface and this only re-shapes it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("supervisor.acp.mcp")


def _pairs(mapping: dict | None) -> list[dict[str, str]]:
    return [{"name": str(k), "value": str(v)} for k, v in (mapping or {}).items()]


def to_acp_servers(
    config: dict[str, Any], transports: dict[str, bool] | None = None
) -> tuple[list[dict], list[str]]:
    """(servers, notes) — the ACP `mcpServers[]`, and one line per server that
    could not be expressed.

    `transports` is what the agent declared in `initialize`. An HTTP server for
    an agent that did not declare `http` is NOT silently downgraded: there is no
    stdio equivalent of "this URL with this header", and a server quietly
    missing is how an agent ends up unable to read its own work item while
    looking like it started fine.
    """
    supported = transports or {"stdio": True, "http": True, "sse": False}
    servers: list[dict] = []
    notes: list[str] = []
    for name, entry in (config.get("mcpServers") or {}).items():
        if not isinstance(entry, dict):
            continue
        kind = str(entry.get("type") or "stdio")
        if kind == "stdio":
            command = str(entry.get("command") or "").strip()
            if not command:
                notes.append(f"{name}: no command, skipped")
                continue
            servers.append(
                {
                    "name": str(name),
                    "command": command,
                    "args": [str(a) for a in (entry.get("args") or [])],
                    "env": _pairs(entry.get("env")),
                }
            )
            continue
        if kind in ("http", "sse"):
            if not supported.get("http"):
                notes.append(
                    f"{name}: this agent did not declare HTTP MCP support, so "
                    "its tools are unavailable for this run"
                )
                continue
            url = str(entry.get("url") or "").strip()
            if not url:
                notes.append(f"{name}: no url, skipped")
                continue
            servers.append(
                {
                    "type": "http",
                    "name": str(name),
                    "url": url,
                    "headers": _pairs(entry.get("headers")),
                }
            )
            continue
        notes.append(f"{name}: unknown transport {kind!r}, skipped")
    return servers, notes


def servers_from_config_file(
    path: str | None, transports: dict[str, bool] | None = None
) -> tuple[list[dict], list[str]]:
    """Read the per-run config `mcpconfig.write()` produced and translate it.

    A missing or unreadable file is an empty list plus a note, never an
    exception: the caller decides whether a run without tools may proceed
    (US-78.4 says it may not, for the kinds that need them), and it needs the
    reason to say so.
    """
    if not path:
        return [], ["no MCP config was written for this run"]
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], [f"the MCP config could not be read: {e}"]
    if not isinstance(config, dict):
        return [], ["the MCP config was not an object"]
    return to_acp_servers(config, transports)
