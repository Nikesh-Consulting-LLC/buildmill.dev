"""Deployment notification delivery (US-1.44).

Fire-and-forget webhooks with a couple of retries. A failing webhook must
never fail, delay, or block a run — callers launch deliveries as detached
tasks and this module swallows every error, recording the last delivery
outcome on the endpoint row so a dead webhook stays noticeable.

Webhook URLs are secrets (Slack incoming-webhook URLs embed tokens): they
are read from the private data bucket per endpoint, never from the DB row.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx

from . import storage
from .config import Settings
from .pool import pool_for

RETRY_DELAYS_SECONDS = [2.0, 5.0]

# Events a deployment can emit (US-1.44; 'cancelled' fires once US-1.35
# lands, 'rolled_back' via US-1.39 rollbacks and US-1.40 auto-rollbacks).
DEPLOYMENT_EVENTS = ["started", "succeeded", "failed", "cancelled", "rolled_back"]
DEFAULT_EVENTS = ["failed", "rolled_back"]


def _connect(settings: Settings):
    """US-87.6: leased from the process-wide pool (app/pool.py), not a new
    connection per call. Shares one pool with db.py so the whole API has a
    single, bounded connection budget."""
    return pool_for(settings).connection()


def endpoint_url_path(org_id: str, endpoint_id: str) -> str:
    return f"{org_id}/notifications/{endpoint_id}/url"


def _org_endpoints(settings: Settings, org_id: str) -> list[dict[str, Any]]:
    with _connect(settings) as conn:
        return conn.execute(
            "select id, org_id, name, format from public.notification_endpoints"
            " where org_id = %s",
            (org_id,),
        ).fetchall()


def _deployment_events(settings: Settings, deployment_id: str) -> list[str]:
    with _connect(settings) as conn:
        row = conn.execute(
            "select events from public.deployment_notifications"
            " where deployment_id = %s",
            (deployment_id,),
        ).fetchone()
    if not row:
        return DEFAULT_EVENTS
    events = row["events"]
    return events if isinstance(events, list) else DEFAULT_EVENTS


def _record_delivery(
    settings: Settings, endpoint_id: str, ok: bool, error: str | None
) -> None:
    with _connect(settings) as conn:
        conn.execute(
            """
            update public.notification_endpoints
            set last_delivery_at = now(), last_delivery_ok = %s,
                last_delivery_error = %s
            where id = %s
            """,
            (ok, error, endpoint_id),
        )
        conn.commit()


def create_endpoint(
    settings: Settings, org_id: str, name: str, url_host: str, fmt: str
) -> dict[str, Any]:
    with _connect(settings) as conn:
        row = conn.execute(
            """
            insert into public.notification_endpoints (org_id, name, url_host, format)
            values (%s, %s, %s, %s)
            returning id, org_id, name, url_host, format
            """,
            (org_id, name, url_host, fmt),
        ).fetchone()
        conn.commit()
        return dict(row)


def delete_endpoint(settings: Settings, endpoint_id: str) -> None:
    with _connect(settings) as conn:
        conn.execute(
            "delete from public.notification_endpoints where id = %s",
            (endpoint_id,),
        )
        conn.commit()


def _slack_text(payload: dict[str, Any]) -> str:
    lines = [
        f"*{payload['project']} / {payload['deployment']}* — "
        f"{payload['event'].replace('_', ' ')}",
        f"source: {payload['source']} · by {payload['triggered_by']}",
    ]
    if payload.get("duration_seconds") is not None:
        lines.append(f"duration: {payload['duration_seconds']}s")
    if payload.get("log_url"):
        lines.append(payload["log_url"])
    return "\n".join(lines)


async def _deliver(
    settings: Settings, endpoint: dict[str, Any], payload: dict[str, Any]
) -> None:
    url_bytes = await storage.get_object(
        settings, endpoint_url_path(endpoint["org_id"], str(endpoint["id"]))
    )
    if url_bytes is None:
        await asyncio.to_thread(
            _record_delivery, settings, str(endpoint["id"]), False, "No URL stored"
        )
        return
    url = url_bytes.decode("utf-8")
    body = (
        {"text": _slack_text(payload)} if endpoint["format"] == "slack" else payload
    )

    error: str | None = None
    for attempt, delay in enumerate([0.0, *RETRY_DELAYS_SECONDS]):
        if delay:
            await asyncio.sleep(delay)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(url, json=body)
            if resp.status_code < 300:
                await asyncio.to_thread(
                    _record_delivery, settings, str(endpoint["id"]), True, None
                )
                return
            error = f"HTTP {resp.status_code}"
        except Exception as e:  # noqa: BLE001 — delivery must never raise
            error = str(e) or e.__class__.__name__
    await asyncio.to_thread(
        _record_delivery, settings, str(endpoint["id"]), False, error
    )


async def send_test(settings: Settings, endpoint: dict[str, Any]) -> dict[str, Any]:
    """Send a test payload synchronously; returns the outcome (US-1.44)."""
    payload = {
        "project": "Build Mill",
        "deployment": "test",
        "run_id": "00000000-0000-0000-0000-000000000000",
        "event": "test",
        "status": "succeeded",
        "source": "test notification",
        "triggered_by": "settings",
        "duration_seconds": 0,
        "log_url": settings.web_base_url,
    }
    await _deliver(settings, endpoint, payload)
    with _connect(settings) as conn:
        row = conn.execute(
            "select last_delivery_ok, last_delivery_error"
            " from public.notification_endpoints where id = %s",
            (str(endpoint["id"]),),
        ).fetchone()
    return {
        "ok": bool(row and row["last_delivery_ok"]),
        "error": row["last_delivery_error"] if row else None,
    }


def notify_deployment_event(
    settings: Settings,
    *,
    org_id: str,
    deployment_id: str,
    deployment_name: str,
    project_name: str,
    project_id: str,
    run_id: str,
    event: str,
    status: str,
    source: str,
    triggered_by: str,
    duration_seconds: int | None,
) -> None:
    """Entry point for the pipeline: fire-and-forget on the running loop."""

    async def _run() -> None:
        try:
            endpoints = await asyncio.to_thread(_org_endpoints, settings, org_id)
            if not endpoints:
                return
            wanted = await asyncio.to_thread(
                _deployment_events, settings, deployment_id
            )
            if event not in wanted:
                return
            payload = {
                "project": project_name,
                "deployment": deployment_name,
                "run_id": run_id,
                "event": event,
                "status": status,
                "source": source,
                "triggered_by": triggered_by,
                "duration_seconds": duration_seconds,
                # US-2.16: deep-link to this specific run's log view
                "log_url": f"{settings.web_base_url}/projects/{project_id}"
                f"/deployments/{deployment_id}?run={run_id}",
            }
            await asyncio.gather(
                *(_deliver(settings, ep, payload) for ep in endpoints)
            )
        except Exception:  # noqa: BLE001 — never let notifications hurt a run
            pass

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    task = loop.create_task(_run())
    # Keep a strong reference alongside the pipeline's tasks.
    from . import deploy

    deploy._TASKS.add(task)
    task.add_done_callback(deploy._TASKS.discard)


def payload_source(run_like: dict[str, Any]) -> str:
    if run_like.get("source") == "zip":
        return f"zip {run_like.get('zip_filename') or ''}".strip()
    sha = run_like.get("commit_sha") or ""
    return f"branch {run_like.get('branch')}" + (f" @ {sha[:7]}" if sha else "")


def _json_default(value: Any) -> str:
    return str(value)


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=_json_default)
