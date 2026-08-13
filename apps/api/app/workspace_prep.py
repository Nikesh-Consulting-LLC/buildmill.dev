"""Prepare Agent Workspace jobs (US-85.1).

The manager's Project access row asks a connected runner to make its
per-project workspace fully ready — directory, latest code, agent + MCP
config, tool servers, verification — before any run is dispatched. The
US-2.8.1 plan run spent 26 minutes and $3.92 discovering a broken shell and
an unreachable MCP endpoint one failed command at a time; this makes the same
discovery a named checklist step that costs seconds and no tokens.

The job row (`workspace_prep_jobs`, migration 246) is the contract with the
popup: `steps` is the checklist, streamed over Realtime as the runner sends
`prep.step` notifications on the control socket. The API owns steps 1 and 8
(reaching the runner; judging the agent's resolved settings); the runner owns
2–7 (everything that must be true on the machine itself).

Single-process like the socket registries it rides on: the orchestrator task
runs in the API process holding the runner's socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import psycopg

from . import db
from .config import Settings
from .pool import pool_for

logger = logging.getLogger("uvicorn.error")

# Keep strong references to in-flight orchestrator tasks (asyncio holds weak ones).
_TASKS: set[asyncio.Task] = set()

# The checklist, in order. Keys are the wire protocol with the runner —
# `prep.step` notifications name one of these — and the labels are what the
# popup shows, so a step the runner never reaches still renders, pending.
STEPS: tuple[tuple[str, str], ...] = (
    ("invoke", "Invoke agent"),
    ("workdir", "Prepare working directory"),
    ("fetch", "Fetch latest code"),
    ("configure", "Configure agent settings"),
    ("mcp", "Install / configure MCP servers"),
    ("tools", "Register tool servers"),
    ("checks", "Check configuration"),
    ("settings", "Check agent settings"),
)
STEP_KEYS = tuple(k for k, _ in STEPS)
STEP_STATUSES = ("pending", "running", "ok", "failed")

# Generous: a cold clone of a large repo on a slow box, plus checks. The
# runner streams progress the whole way, so a long wait is visible, not blank.
PREPARE_TIMEOUT_SECONDS = 900


class JobActive(RuntimeError):
    """A preparation for this (agent, project) pair is already live."""


def _connect(settings: Settings):
    """US-87.6: leased from the process-wide pool (app/pool.py), not a new
    connection per call. Shares one pool with db.py so the whole API has a
    single, bounded connection budget."""
    return pool_for(settings).connection()


def initial_steps() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "status": "pending", "detail": ""}
        for key, label in STEPS
    ]


# ---------------------------------------------------------------------------
# Job records
# ---------------------------------------------------------------------------


def create_job(
    settings: Settings,
    *,
    org_id: str,
    worker_id: str,
    project_id: str,
    by: str = "",
    by_email: str = "",
) -> dict[str, Any]:
    with _connect(settings) as conn:
        try:
            row = conn.execute(
                "insert into public.workspace_prep_jobs"
                " (org_id, worker_id, project_id, status, steps, started_by,"
                "  started_by_email, started_at)"
                " values (%s, %s, %s, 'running', %s, %s, %s, now()) returning *",
                (
                    org_id,
                    worker_id,
                    project_id,
                    json.dumps(initial_steps()),
                    by or None,
                    by_email,
                ),
            ).fetchone()
        except psycopg.errors.UniqueViolation:
            conn.rollback()
            raise JobActive(
                "A preparation for this agent and project is already running."
            )
        conn.commit()
        return row


def live_job(
    settings: Settings, worker_id: str, project_id: str
) -> dict[str, Any] | None:
    with _connect(settings) as conn:
        return conn.execute(
            "select * from public.workspace_prep_jobs"
            " where worker_id = %s and project_id = %s"
            "   and status in ('queued', 'running')"
            " order by created_at desc limit 1",
            (worker_id, project_id),
        ).fetchone()


def set_step(
    settings: Settings,
    job_id: str,
    key: str,
    status: str,
    detail: str = "",
    worker_id: str | None = None,
) -> None:
    """Update one checklist entry in place.

    `worker_id`, when given, scopes the write to a job that worker actually
    owns — a `prep.step` frame is untrusted socket input, and without the
    guard any connected runner could repaint another agent's checklist.
    """
    if key not in STEP_KEYS or status not in STEP_STATUSES:
        return
    guard = " and worker_id = %s" if worker_id else ""
    args: tuple[Any, ...] = (key, status, detail[:500], job_id)
    if worker_id:
        args = (*args, worker_id)
    with _connect(settings) as conn:
        conn.execute(
            "update public.workspace_prep_jobs set steps = ("
            "  select coalesce(jsonb_agg("
            "    case when elem->>'key' = %s"
            "         then elem || jsonb_build_object('status', %s::text, 'detail', %s::text)"
            "         else elem end), '[]'::jsonb)"
            "  from jsonb_array_elements(steps) elem)"
            f" where id = %s and status in ('queued', 'running'){guard}",
            args,
        )
        conn.commit()


def finish_job(
    settings: Settings,
    job_id: str,
    *,
    status: str,
    error: str | None = None,
    prepared_commit: str | None = None,
    workdir: str | None = None,
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "update public.workspace_prep_jobs"
            " set status = %s, error = %s, prepared_commit = %s, workdir = %s,"
            "     finished_at = now()"
            " where id = %s and status in ('queued', 'running')",
            (status, error, prepared_commit, workdir, job_id),
        )
        conn.commit()


def fail_step_and_job(
    settings: Settings, job_id: str, key: str, error: str
) -> None:
    set_step(settings, job_id, key, "failed", error)
    finish_job(settings, job_id, status="failed", error=error)


# ---------------------------------------------------------------------------
# What the runner is handed
# ---------------------------------------------------------------------------


def tool_server_bundle(
    settings: Settings, org_id: str
) -> list[dict[str, Any]]:
    """The org's enabled Tool servers (Settings → Tool servers), shaped for
    the runner's registration + reachability checks.

    No credential and no scoped key rides along — run-scoped keys are minted
    per run (US-34.2), so preparation checks the machine can *reach* a
    proxied server's proxy URL and can *find* a stdio server's command, which
    is exactly the part of readiness that lives on the machine.
    """
    servers = []
    for entry in db.list_mcp_servers(settings, org_id):
        if not entry.get("enabled"):
            continue
        slug = entry.get("slug")
        if not slug or slug == "factory":
            continue
        if entry.get("transport") == "stdio" and not entry.get("needs_credential"):
            servers.append(
                {
                    "slug": slug,
                    "name": entry.get("name"),
                    "transport": "stdio",
                    "command": entry.get("command"),
                }
            )
        else:
            base = (settings.api_base_url or "").rstrip("/")
            servers.append(
                {
                    "slug": slug,
                    "name": entry.get("name"),
                    "transport": "http",
                    "url": f"{base}/api/v1/mcp-proxy/{slug}",
                }
            )
    return servers


# ---------------------------------------------------------------------------
# Step 8: the agent's resolved settings, judged server-side
# ---------------------------------------------------------------------------


def _latest_module_settings(
    settings: Settings, worker_id: str
) -> list[dict[str, Any]]:
    """What the runner's modules declared at hello — the honest source for
    which knobs a module can actually be told (US-32.4)."""
    with _connect(settings) as conn:
        row = conn.execute(
            "select module_settings from public.runner_sessions"
            " where worker_id = %s order by connected_at desc limit 1",
            (worker_id,),
        ).fetchone()
    return (row or {}).get("module_settings") or []


def settings_check(settings: Settings, worker_id: str) -> tuple[bool, str]:
    """Step 8: does this agent resolve a workable run configuration?

    Reports the enabled modules, billing, per-kind model pins, and — the
    US-2.8.1 lesson — which resolvable knobs the module cannot be told, so an
    'effort' that will be silently undeliverable is named before a run pays
    for the discovery.
    """
    config = db.get_runner_config(settings, worker_id)
    modules = [m for m in (config.get("enabled_modules") or []) if m != "sim"]
    if not modules:
        return False, "no module is enabled on this agent — enable one in its settings"

    parts = [f"module: {', '.join(modules)}"]
    billing = config.get("claude_billing")
    if billing:
        parts.append(f"billing: {billing}")

    overrides = config.get("model_overrides") or {}
    routes = config.get("model_routes") or {}
    run_routes = config.get("run_routes") or {}
    pins = {}
    for kind in sorted(set(list(overrides) + list(routes) + list(run_routes))):
        model = (
            overrides.get(kind)
            or ((run_routes.get(kind) or {}).get("custom") or {}).get("model")
            or routes.get(kind)
        )
        if isinstance(model, str) and model.strip():
            pins[kind] = model
    if pins:
        parts.append(
            "models: " + ", ".join(f"{k}→{v}" for k, v in sorted(pins.items()))
        )
    else:
        parts.append("models: none pinned (runs use the org default preset)")

    declared = {
        e.get("module"): {k.get("name") for k in (e.get("settings") or [])}
        for e in _latest_module_settings(settings, worker_id)
    }
    resolvable = {"model", "effort", "max_turns"}
    for module in modules:
        knobs = declared.get(module)
        if knobs is None:
            continue
        missing = sorted(resolvable - knobs)
        if missing:
            parts.append(
                f"{module} cannot be told: {', '.join(missing)} "
                "(these settings will not reach a run)"
            )
    return True, " · ".join(parts)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_prep_job(
    settings: Settings,
    job_id: str,
    worker_id: str,
    payload: dict[str, Any],
) -> None:
    """Drive one preparation: hand the runner its instructions, wait for the
    final reply (progress streams separately as `prep.step` frames), then
    judge the agent's settings and close the job honestly."""
    from .routers import runner_socket  # late import — router imports us

    await asyncio.to_thread(
        set_step, settings, job_id, "invoke", "running",
        "asking the agent's runner over the control socket",
    )
    try:
        result = await runner_socket.request_from_worker(
            worker_id,
            "workspace.prepare",
            payload,
            timeout=PREPARE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await asyncio.to_thread(
            finish_job, settings, job_id,
            status="failed",
            error=f"the runner didn't finish within {PREPARE_TIMEOUT_SECONDS}s",
        )
        return
    except RuntimeError as e:
        await asyncio.to_thread(
            fail_step_and_job, settings, job_id, "invoke", str(e)[:500]
        )
        return

    if not (result or {}).get("ok"):
        error = str((result or {}).get("error") or "preparation failed")[:500]
        # The runner already marked the failing step; this records the outcome.
        await asyncio.to_thread(
            finish_job, settings, job_id, status="failed", error=error
        )
        return

    await asyncio.to_thread(
        set_step, settings, job_id, "settings", "running", ""
    )
    try:
        ok, detail = await asyncio.to_thread(settings_check, settings, worker_id)
    except Exception as e:  # noqa: BLE001 — an unreadable config is a finding
        ok, detail = False, f"could not read the agent's settings: {e}"
    await asyncio.to_thread(
        set_step, settings, job_id, "settings", "ok" if ok else "failed", detail
    )
    await asyncio.to_thread(
        finish_job, settings, job_id,
        status="succeeded" if ok else "failed",
        error=None if ok else detail[:500],
        prepared_commit=(result or {}).get("base_sha"),
        workdir=(result or {}).get("workdir"),
    )


def launch(
    settings: Settings, job_id: str, worker_id: str, payload: dict[str, Any]
) -> None:
    """Fire-and-forget the orchestrator on the running event loop."""
    task = asyncio.get_running_loop().create_task(
        run_prep_job(settings, job_id, worker_id, payload)
    )
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
