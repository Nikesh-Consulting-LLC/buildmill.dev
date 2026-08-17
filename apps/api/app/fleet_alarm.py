"""The fleet says when it goes dark (us-116.8).

On 2026-08-17, 11:49–12:57 UTC, every agent on Pod-001 was offline for 68
minutes — six grey pills for anyone who opened the roster, 8,023 crash reports
in the System issues inbox, and no notification, because no code looked for
"the whole fleet dropped and none came back".

One rule, on the liveness loop: per org that has active autonomous agents, if
the org HAD agents heartbeating and has had **none live for more than two
minutes**, open an episode — one notification to the org's managers, one
System issue for the platform admin — and close it (recording the return on
the same issue) when any agent comes back. Two minutes is above any deploy
bounce (measured ≤ 30 s reconnect) and above the 90 s presence window, so a
release never pages anyone. Partial loss is not this alarm: those agents read
Offline on the roster and their own incidents cover them.

The decision is a pure function over what the database says; the sweep is the
thin thing that reads, decides, and writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from . import app_issues, db
from .config import Settings

logger = logging.getLogger(__name__)

# Above the deploy bounce and above the presence window (db.PRESENCE_WINDOW_SECONDS).
FLEET_DARK_MINUTES = 2
# An org whose last heartbeat is older than this is dormant, not dark — its
# agents were removed or never ran; nothing to alarm about.
FLEET_DORMANT_DAYS = 7


@dataclass
class OrgPresence:
    org_id: str
    org_name: str
    agents: int
    live: int
    last_seen: datetime | None


@dataclass
class Decision:
    to_open: list[OrgPresence]
    to_close: list[dict[str, Any]]


def decide(
    presence: list[OrgPresence],
    open_episodes: dict[str, dict[str, Any]],
    now: datetime,
    *,
    minutes: int = FLEET_DARK_MINUTES,
    dormant_days: int = FLEET_DORMANT_DAYS,
) -> Decision:
    """Which orgs just went dark, and which open episodes just ended.

    Dark = the org has active autonomous agents, has heartbeated at all,
    within the dormancy window, and none of them is live now, and the last
    heartbeat is older than the window. Ended = an open episode whose org has
    any live agent again.
    """
    to_open: list[OrgPresence] = []
    to_close: list[dict[str, Any]] = []
    for row in presence:
        episode = open_episodes.get(row.org_id)
        if row.live > 0:
            if episode is not None:
                to_close.append(episode)
            continue
        if episode is not None or row.agents == 0 or row.last_seen is None:
            continue
        age = now - row.last_seen
        if age > timedelta(days=dormant_days):
            continue
        if age > timedelta(minutes=minutes):
            to_open.append(row)
    return Decision(to_open=to_open, to_close=to_close)


def _fmt(at: datetime) -> str:
    at = at.astimezone(timezone.utc) if at.tzinfo else at.replace(tzinfo=timezone.utc)
    return at.strftime("%H:%M UTC on %Y-%m-%d")


def dark_message(row: OrgPresence) -> str:
    when = _fmt(row.last_seen) if row.last_seen else "an unknown time"
    n = row.agents
    return (
        f"All {n} agent{'s' if n != 1 else ''} in {row.org_name} went offline at "
        f"{when} and none has reconnected."
    )


async def fleet_dark_sweep(settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Runs on the API's liveness loop (60 s). Reads presence per org, decides,
    and writes: an episode row + one notification + one System issue when an
    org goes dark; the episode's end (and a note on the issue) when it returns."""
    import asyncio

    if not getattr(settings, "database_url", ""):
        return {"opened": 0, "closed": 0}
    now = now or datetime.now(timezone.utc)
    presence = await asyncio.to_thread(db.fleet_presence_by_org, settings)
    open_by_org = await asyncio.to_thread(db.open_fleet_dark_episodes, settings)
    decision = decide(presence, open_by_org, now)

    opened = closed = 0
    for row in decision.to_open:
        started = row.last_seen or now
        episode_id = await asyncio.to_thread(
            db.open_fleet_dark_episode, settings, row.org_id, started, row.agents
        )
        message = dark_message(row)
        issue_id: str | None = None
        try:
            issue_id = await asyncio.to_thread(
                app_issues.report_fleet_dark, settings, row.org_id, row.org_name, message,
                {"org_id": row.org_id, "agents": row.agents, "since": started.isoformat(),
                 "episode_id": episode_id},
            )
        except Exception:  # noqa: BLE001 — the notification still goes
            logger.warning("fleet-dark: could not file the system issue", exc_info=True)
        try:
            await asyncio.to_thread(
                db.notify_org_managers, settings, row.org_id, "fleet_dark",
                {"message": message, "agents": row.agents, "since": started.isoformat()},
            )
        except Exception:  # noqa: BLE001
            logger.warning("fleet-dark: could not notify managers", exc_info=True)
        await asyncio.to_thread(db.mark_fleet_dark_notified, settings, episode_id, issue_id)
        logger.warning("fleet-dark: %s", message)
        opened += 1

    for episode in decision.to_close:
        await asyncio.to_thread(db.close_fleet_dark_episode, settings, str(episode["id"]), now)
        if episode.get("app_issue_id"):
            try:
                await asyncio.to_thread(
                    app_issues.note_returned, settings, str(episode["app_issue_id"]), now
                )
            except Exception:  # noqa: BLE001
                logger.warning("fleet-dark: could not note the return", exc_info=True)
        closed += 1
    return {"opened": opened, "closed": closed}
