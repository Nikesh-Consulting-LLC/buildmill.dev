"""Manager-triggered workspace preparation.

Two shapes on one `workspace.prepare` method:

- **Quick** (no `job_id` in params): the original "prepare codebase" test —
  clone/fetch and reply. Kept verbatim for older servers and the runner-page
  button.
- **Full** (US-85.1, `job_id` present): the whole checklist — directory,
  latest code, agent + MCP config, tool servers, machine verification — with
  each step streamed back as a `prep.step` notification so the manager's
  popup watches it move. Runs as its own task so the control socket keeps
  heartbeating under a long clone.

The full path exists because of the US-2.8.1 plan run: 26 minutes of an agent
discovering, one failed tool call at a time, that its shell was broken and
its MCP endpoint unreachable. Every one of those discoveries is a named step
here, checked in seconds, before any run pays for it.

Security posture unchanged: config files carrying the worker token
(`.factory-mcp.json`, `.grok/config.toml`) are written to VERIFY the machine
can materialize and use them, then removed — a run rewrites them with its own
run-scoped grants anyway (US-31.9), so readiness loses nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
from pathlib import Path
from typing import Any

import httpx

from . import gitwork, mcpconfig, workspace
from .primitives import LocalPrimitives

logger = logging.getLogger("supervisor.workspace_prepare")

# Keep strong references to in-flight full-prepare tasks.
_TASKS: set[asyncio.Task] = set()

MCP_CHECK_TIMEOUT = 15
TOOL_CHECK_TIMEOUT = 10


async def handle(connection: Any, msg: dict[str, Any]) -> None:
    """The connection's on_message hook — ignores every method but its own,
    so it composes with whatever else gets wired in later."""
    if msg.get("method") != "workspace.prepare":
        return
    req_id = msg.get("id")
    params = msg.get("params") or {}
    project_id = params.get("project_id")
    remote = params.get("remote")
    if not project_id or not remote:
        if req_id is not None:
            await connection.reply(
                req_id, error="project_id and remote are required"
            )
        return

    if params.get("job_id"):
        # US-85.1: the full checklist, off the socket loop — a cold clone can
        # take minutes and heartbeats must keep flowing under it.
        task = asyncio.get_running_loop().create_task(
            _full_prepare(connection, req_id, params)
        )
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return

    # US-89.1: clean remote; the credential helper authenticates.
    prim = LocalPrimitives()
    try:
        workdir = await gitwork.prepare_checkout(
            prim, remote, str(project_id), str(project_id)
        )
        head = await gitwork.git(prim, ["rev-parse", "HEAD"], cwd=str(workdir))
        base_sha = head.strip()
        size = workspace.dir_size_bytes(workdir)
        workspace.write_state(workdir, base_sha=base_sha, prepared_by="manager")
        result: dict[str, Any] = {
            "ok": True,
            "workdir": str(workdir),
            "base_sha": base_sha,
            "bytes": size,
        }
    except Exception as e:  # noqa: BLE001 — report the failure, never crash the socket
        logger.warning(
            "workspace.prepare failed for project %s: %s", project_id, e
        )
        result = {"ok": False, "error": str(e)[:500]}
    if req_id is not None:
        await connection.reply(req_id, result=result)


# ---------------------------------------------------------------------------
# The full checklist (US-85.1)
# ---------------------------------------------------------------------------


async def _full_prepare(
    connection: Any, req_id: Any, params: dict[str, Any]
) -> None:
    job_id = str(params["job_id"])
    project_id = str(params["project_id"])
    remote = str(params["remote"])
    tool_servers = params.get("tool_servers") or []

    async def step(key: str, status: str, detail: str = "") -> None:
        try:
            await connection.notify(
                "prep.step",
                {
                    "job_id": job_id,
                    "step": key,
                    "status": status,
                    "detail": detail[:500],
                },
            )
        except Exception:  # noqa: BLE001 — progress must never kill the prepare
            pass

    async def fail(key: str, error: str) -> None:
        error = error[:500]
        await step(key, "failed", error)
        # US-85.1 follow-up (2026-08-13): a failure of the machine-level
        # steps is fleet news, not just a popup line — it rides the same
        # incident channel a refused hand-back uses (US-31.1), landing in
        # runner_incidents and notifying the managers. Environment steps
        # only: a bad fetch is the project's problem, not the machine's.
        if key in ("tools", "checks"):
            try:
                await connection.notify(
                    "runner.incident",
                    {
                        "kind": "workspace-prepare",
                        "message": (
                            f"workspace preparation failed at '{key}': {error}"
                        ),
                    },
                )
            except Exception:  # noqa: BLE001 — reporting must not mask the failure
                pass
        if req_id is not None:
            await connection.reply(
                req_id, result={"ok": False, "error": error, "failed_step": key}
            )

    await step("invoke", "ok", "runner connected — request accepted")

    prim = LocalPrimitives()
    grok_toml: Path | None = None
    workdir: Path | None = None
    try:
        # -- working directory ------------------------------------------------
        await step("workdir", "running")
        try:
            workdir = workspace.ensure(
                workspace.workspace_for(project_id, project_id)
            )
        except OSError as e:
            await fail("workdir", f"could not create the workspace: {e}")
            return
        await step("workdir", "ok", str(workdir))

        # -- latest code ------------------------------------------------------
        await step("fetch", "running")
        try:
            # US-89.1: clean remote; the credential helper authenticates.
            workdir = await gitwork.prepare_checkout(
                prim, remote, project_id, project_id
            )
            head = await gitwork.git(
                prim, ["rev-parse", "HEAD"], cwd=str(workdir)
            )
            base_sha = head.strip()
        except Exception as e:  # noqa: BLE001
            await fail("fetch", f"could not fetch the project: {str(e)[:400]}")
            return
        await step("fetch", "ok", f"default branch at {base_sha[:10]}")

        # -- agent settings on disk ------------------------------------------
        await step("configure", "running")
        workspace.write_state(
            workdir, base_sha=base_sha, prepared_by="manager"
        )
        mcp_path = mcpconfig.write(
            workdir,
            connection.api_url,
            connection.token,
            project_id,
            tool_servers=tool_servers,
        )
        if mcp_path is None:
            await fail(
                "configure",
                "could not write the factory MCP config (no API url or token)",
            )
            return
        await step(
            "configure", "ok", "workspace state + factory MCP config written"
        )

        # -- per-module MCP materialization -----------------------------------
        await step("mcp", "running")
        enabled = [
            m
            for m in ((connection.config or {}).get("enabled_modules") or [])
            if m != "sim"
        ]
        details = []
        if "grok" in enabled or "interactive" in enabled:
            try:
                import json as _json

                from .modules.grok import _servers_to_toml

                servers = _json.loads(mcp_path.read_text(encoding="utf-8")).get(
                    "mcpServers"
                ) or {}
                grok_dir = workdir / ".grok"
                grok_dir.mkdir(parents=True, exist_ok=True)
                grok_toml = grok_dir / "config.toml"
                grok_toml.write_text(
                    _servers_to_toml(servers), encoding="utf-8"
                )
                details.append("grok config.toml materialized")
            except Exception as e:  # noqa: BLE001
                await fail("mcp", f"could not write the grok MCP config: {e}")
                return
        if any(m in enabled for m in ("claude", "buildmill", "opencode")):
            details.append(
                "claude/opencode read .factory-mcp.json via argv at run time"
            )
        await step(
            "mcp",
            "ok",
            "; ".join(details) or "no CLI module enabled to configure",
        )

        # -- tool servers ------------------------------------------------------
        await step("tools", "running")
        ok_names: list[str] = []
        failures: list[str] = []
        for entry in tool_servers:
            name = str(entry.get("name") or entry.get("slug") or "?")
            if entry.get("transport") == "stdio":
                parts = shlex.split(
                    str(entry.get("command") or ""), posix=(os.name != "nt")
                )
                if parts and shutil.which(parts[0]):
                    ok_names.append(name)
                else:
                    failures.append(
                        f"{name}: command '{parts[0] if parts else ''}' not "
                        "found on this machine"
                    )
            else:
                url = str(entry.get("url") or "")
                reachable, why = await _http_reachable(url)
                if reachable:
                    ok_names.append(name)
                else:
                    failures.append(f"{name}: {why}")
        if failures:
            await fail("tools", "; ".join(failures))
            return
        await step(
            "tools",
            "ok",
            f"{len(ok_names)} registered"
            + (f" ({', '.join(ok_names)})" if ok_names else "")
            + (
                " — proxied servers get run-scoped keys at run time"
                if any(e.get("transport") != "stdio" for e in tool_servers)
                else ""
            ),
        )

        # -- machine verification ---------------------------------------------
        await step("checks", "running")
        problems: list[str] = []
        checks: list[str] = []

        shell_argv = (
            ["cmd", "/c", "echo prep-ok"]
            if os.name == "nt"
            else ["sh", "-c", "echo prep-ok"]
        )
        try:
            res = await prim.run_shell(shell_argv, timeout=30)
            if res.exit_code == 0:
                checks.append("shell ok")
            else:
                problems.append(f"shell exited {res.exit_code}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"shell unusable: {e}")

        if os.name != "nt":
            # The US-2.8.1 defect verbatim: agent CLIs invoke bash, and a
            # machine without it strands every session in a probe loop.
            if not shutil.which("bash"):
                problems.append(
                    "bash not found on PATH (agent CLIs invoke it)"
                )
            elif not os.path.exists("/usr/bin/bash"):
                problems.append(
                    "bash exists but not at /usr/bin/bash — some CLI shell "
                    "wrappers hardcode that path"
                )
            else:
                # 2026-08-13: probe the EXACT invocation the CLIs' terminal
                # wrapper produces — a login shell, as this process's own
                # user. Binary-exists checks passed for two days while the
                # real invocation path was broken one layer up.
                try:
                    res = await prim.run_shell(
                        ["/usr/bin/bash", "-lc", "echo prep-ok"], timeout=30
                    )
                    if res.exit_code == 0:
                        checks.append("bash ok")
                    else:
                        problems.append(
                            f"bash -lc exited {res.exit_code}: "
                            f"{res.stdout.strip()[:150]}"
                        )
                except Exception as e:  # noqa: BLE001
                    problems.append(f"bash -lc unusable: {e}")

        try:
            await gitwork.git(prim, ["status", "--porcelain"], cwd=str(workdir))
            checks.append("git ok")
        except Exception as e:  # noqa: BLE001
            problems.append(f"git broken in the workspace: {str(e)[:200]}")

        mcp_ok, mcp_why = await _factory_mcp_answers(
            connection.api_url, connection.token
        )
        if mcp_ok:
            checks.append("factory MCP ok")
        else:
            problems.append(mcp_why)

        if problems:
            await fail("checks", "; ".join(problems))
            return
        await step("checks", "ok", " · ".join(checks))
    finally:
        # Token-bearing files never outlive the preparation that verified
        # them (the mcpconfig rule); a run rewrites both with its own grants.
        if workdir is not None:
            mcpconfig.remove(workdir)
        if grok_toml is not None:
            try:
                grok_toml.unlink(missing_ok=True)
            except OSError:
                pass

    if req_id is not None:
        await connection.reply(
            req_id,
            result={
                "ok": True,
                "workdir": str(workdir),
                "base_sha": base_sha,
                "bytes": workspace.dir_size_bytes(workdir),
            },
        )


async def _http_reachable(url: str) -> tuple[bool, str]:
    """Whether this machine can reach the URL at all. Any HTTP answer counts —
    401 from a proxy that wants a run-scoped key is still proof of reach."""
    if not url:
        return False, "no url configured"
    try:
        async with httpx.AsyncClient(timeout=TOOL_CHECK_TIMEOUT) as client:
            await client.get(url)
        return True, ""
    except httpx.HTTPError as e:
        return False, f"unreachable from this machine ({e.__class__.__name__})"


async def _factory_mcp_answers(api_url: str, token: str) -> tuple[bool, str]:
    """An authenticated MCP initialize against the factory — the round-trip a
    run's tools depend on, made before a run depends on it."""
    url = mcpconfig.endpoint(api_url)
    try:
        async with httpx.AsyncClient(timeout=MCP_CHECK_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={
                    "X-Worker-Token": token,
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "workspace-prepare", "version": "1"},
                    },
                },
            )
    except httpx.HTTPError as e:
        return False, f"factory MCP unreachable ({e.__class__.__name__})"
    if resp.status_code in (401, 403):
        return False, f"factory MCP rejected the worker token ({resp.status_code})"
    if resp.status_code >= 500:
        return False, f"factory MCP answered {resp.status_code}"
    return True, ""
