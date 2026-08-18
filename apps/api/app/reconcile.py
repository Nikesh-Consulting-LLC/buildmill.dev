"""Push-detection hand-back (US-3.4).

Every push flows through the factory git remote, so the factory's own
push log is the source of truth — no GitHub webhook, no sync button.
A claim that expires WITH pushed work is auto-submitted through the
same path an explicit submit takes (verify branch, open/adopt PR, pull
diff, move to review); a claim that expires without pushes re-queues
via db.requeue_expired_claims as before.
"""

import asyncio
import logging
from typing import Any

from . import db
from .config import Settings

logger = logging.getLogger("uvicorn.error")


def _expired_pushed_runs(settings: Settings) -> list[dict[str, Any]]:
    with db._connect(settings) as conn:
        return conn.execute(
            """
            select id, issue_id, worker_id, branch_ref
            from public.runs
            where status = 'running' and worker_id is not null
              and claim_expires_at < now()
              and pushed_head_sha is not null
            """
        ).fetchall()


async def reconcile_pushed_expired_claims(settings: Settings) -> int:
    """Auto-submit expired claims that have pushed work — the human who
    pushed and closed the laptop still lands in review. Returns how many
    runs were handled (submitted, or re-queued as a fallback when the
    branch can no longer be verified upstream)."""
    from fastapi import HTTPException

    from .routers.worker import Submit, perform_submit  # late: avoids cycle

    handled = 0
    for run in await asyncio.to_thread(_expired_pushed_runs, settings):
        worker = await asyncio.to_thread(db.get_worker_row, settings, str(run["worker_id"]))
        if not worker:
            continue
        # US-7.3: the run stored its resolved branch when its context was
        # served; the push landed on it. Fall back to the legacy name only
        # for runs that predate branch_ref being stored.
        branch_ref = (run.get("branch_ref") or "").strip() or (
            f"factory/issue-{run['issue_id']}"
        )
        try:
            await perform_submit(
                settings,
                worker,
                str(run["id"]),
                Submit(branch_ref=branch_ref),
                trigger="lease-expiry",
            )
            handled += 1
        except HTTPException as e:
            logger.warning(
                "auto-submit of run %s failed (%s); returning it to the pool",
                run["id"],
                e.detail,
            )
            if await asyncio.to_thread(db.force_requeue_run, settings, str(run["id"])):
                handled += 1
    return handled
