"""us-119.2: the lease sweeps, on their own clock.

Three sweeps keep the factory honest about work nobody is doing any more:

* ``db.requeue_expired_claims`` — an expired claim with nothing pushed goes
  back to the pool (US-3.2), and the stale-heartbeat sweep rides inside it
  (US-31.2);
* ``reconcile.reconcile_pushed_expired_claims`` — an expired claim *with*
  pushed work auto-submits, so a human who pushed and closed the laptop
  still lands in review (US-3.4);
* ``db.reap_expired_release_preps`` — an abandoned release prep fails
  itself and frees the project's one in-flight release slot (US-103.1).

Until this story each of them also ran *lazily*: the first two before every
``GET /worker/pool``, the third before every ``GET /worker/release-prep`` —
"self-healing without a background scheduler", as the comments put it. The
scheduler existed anyway (US-13.6's liveness loop, every 60 s); the lazy
copies just meant every one of the ~80,000 polls a day paid two or three
extra leased connections to find, almost always, nothing.

So the sweeps run here, every ``LEASE_SWEEP_INTERVAL_S``, from one lifespan
task — and the poll handlers only list. Single-flight by construction (the
loop is sequential), time-boxed per tick (a stuck GitHub call inside the
reconciler must not stop the next tick), and a failing tick logs and yields
to the next. Per process, best effort: an API restart skips one tick; when
the API is down there are no polls either, so nothing is lost relative to
the lazy sweeps.

Every database call here goes through ``asyncio.to_thread`` — this module
is on ``test_loop_never_blocks.COVERED``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from . import db, reconcile
from .config import Settings

logger = logging.getLogger("uvicorn.error")

# How often the sweeps run. Half the old liveness cadence: an expired claim
# now returns to the pool within ~30 s of expiring instead of at the next
# poll, and nothing downstream is timed finer than that.
LEASE_SWEEP_INTERVAL_S = 30.0
# The box around one tick. The reconciler can call GitHub for each run it
# hands back; a hung call must not stop every later tick.
LEASE_SWEEP_TICK_TIMEOUT_S = 60.0


async def lease_sweep_tick(settings: Settings) -> dict[str, int]:
    """One pass of the three sweeps. Returns what it did, for the log."""
    out: dict[str, int] = {}
    swept = await asyncio.to_thread(db.requeue_expired_claims, settings)
    if swept:
        logger.warning("Requeued %d expired claim(s) from the sweep", swept)
    out["requeued"] = int(swept or 0)
    handled = await reconcile.reconcile_pushed_expired_claims(settings)
    if handled:
        logger.warning("Auto-submitted %d pushed run(s) with expired claims", handled)
    out["reconciled"] = int(handled or 0)
    reaped = await asyncio.to_thread(db.reap_expired_release_preps, settings)
    for r in reaped:
        logger.warning(
            "Reaped abandoned release prep for %s (held by %s for %s min)",
            r["version"],
            r["worker"],
            r["held_minutes"],
        )
    out["reaped"] = len(reaped)
    return out


async def run_lease_sweep(
    settings_factory: Callable[[], Settings],
    *,
    interval_s: float = LEASE_SWEEP_INTERVAL_S,
    tick_timeout_s: float = LEASE_SWEEP_TICK_TIMEOUT_S,
    tick: Callable[[Settings], Awaitable[Any]] = lease_sweep_tick,
    max_ticks: int | None = None,
) -> int:
    """Tick, wait, repeat — forever, or ``max_ticks`` times (tests).

    The first tick runs immediately, so a restart sweeps at once (the
    startup sweeps `main.py` used to run by hand). Single-flight: a tick
    never overlaps the next, because this is one sequential loop. A tick
    that raises, or exceeds ``tick_timeout_s``, is logged and the loop
    carries on to the next; only cancellation stops it. Returns the number
    of ticks started.
    """
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        try:
            await asyncio.wait_for(tick(settings_factory()), tick_timeout_s)
        except asyncio.CancelledError:
            raise
        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "Lease sweep tick exceeded %ss and was abandoned; the next runs on schedule",
                tick_timeout_s,
            )
        except Exception as e:  # noqa: BLE001 — the sweep must survive
            logger.warning("Lease sweep tick skipped: %s", e)
        if max_ticks is not None and ticks >= max_ticks:
            break
        await asyncio.sleep(interval_s)
    return ticks
