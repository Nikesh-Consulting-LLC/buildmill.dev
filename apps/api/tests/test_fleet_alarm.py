"""us-116.8: the fleet says when it goes dark, and says a standing fault once.

The 68-minute outage of 2026-08-17 (migration 279 before its hotfix) produced
8,023 crash reports and no notification. And `raise_service_incident` re-raised
a revoked-token alarm on the hour, every hour — 429 rows in fourteen days.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app import agent_provision, app_issues, db, fleet_alarm
from app.fleet_alarm import OrgPresence, decide

NOW = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)


def _org(org_id="org-1", *, agents=6, live=0, last_seen_ago_s=None, name="Sandy's Workspace"):
    return OrgPresence(
        org_id=org_id, org_name=name, agents=agents, live=live,
        last_seen=None if last_seen_ago_s is None else NOW - timedelta(seconds=last_seen_ago_s),
    )


# ------------------------------------------------------------ the decision


def test_an_org_dark_past_the_window_opens_an_episode():
    d = decide([_org(last_seen_ago_s=180)], {}, NOW)
    assert [o.org_id for o in d.to_open] == ["org-1"]
    assert d.to_close == []


def test_a_deploy_bounce_inside_the_window_is_quiet():
    """Every agent offline for 60 s and back — nothing."""
    d = decide([_org(last_seen_ago_s=60)], {}, NOW)
    assert d.to_open == [] and d.to_close == []


def test_partial_loss_is_not_this_alarm():
    d = decide([_org(agents=6, live=1, last_seen_ago_s=0)], {}, NOW)
    assert d.to_open == []


def test_it_fires_once_an_open_episode_suppresses_a_second():
    d = decide([_org(last_seen_ago_s=600)], {"org-1": {"id": "ep-1", "org_id": "org-1"}}, NOW)
    assert d.to_open == [] and d.to_close == []


def test_a_return_closes_the_open_episode():
    d = decide([_org(live=3, last_seen_ago_s=0)], {"org-1": {"id": "ep-1", "org_id": "org-1"}}, NOW)
    assert d.to_open == []
    assert [e["id"] for e in d.to_close] == ["ep-1"]


def test_an_org_that_never_had_agents_heartbeat_or_has_none_is_not_dark():
    assert decide([_org(last_seen_ago_s=None)], {}, NOW).to_open == []
    assert decide([_org(agents=0, last_seen_ago_s=999)], {}, NOW).to_open == []


def test_a_dormant_org_is_not_dark():
    """Agents removed weeks ago: nothing to alarm about."""
    d = decide([_org(last_seen_ago_s=8 * 86400)], {}, NOW)
    assert d.to_open == []


def test_the_message_names_the_count_the_org_and_the_time():
    row = _org(last_seen_ago_s=180)
    msg = fleet_alarm.dark_message(row)
    assert msg == (
        "All 6 agents in Sandy's Workspace went offline at 11:57 UTC on 2026-08-17 "
        "and none has reconnected."
    )


# ------------------------------------------------------------ the sweep


def test_the_sweep_opens_once_and_closes_on_return(monkeypatch):
    """Two ticks dark → one notification, one issue; then a return closes it
    and notes the time on the same issue — never a second notification."""
    state = {"presence": [_org(last_seen_ago_s=180)], "open": {}, "notified": [], "issues": [],
             "closed": [], "returned": [], "episodes": []}

    monkeypatch.setattr(db, "fleet_presence_by_org", lambda s: state["presence"])
    monkeypatch.setattr(db, "open_fleet_dark_episodes", lambda s: dict(state["open"]))

    def open_ep(s, org_id, started_at, agent_count):
        state["episodes"].append((org_id, started_at, agent_count))
        state["open"][org_id] = {"id": "ep-1", "org_id": org_id, "app_issue_id": None}
        return "ep-1"

    monkeypatch.setattr(db, "open_fleet_dark_episode", open_ep)
    monkeypatch.setattr(db, "mark_fleet_dark_notified",
                        lambda s, ep, issue: state["open"]["org-1"].update(app_issue_id=issue))
    monkeypatch.setattr(db, "close_fleet_dark_episode",
                        lambda s, ep, at: state["closed"].append((ep, at)) or state["open"].clear())
    monkeypatch.setattr(db, "notify_org_managers",
                        lambda s, org, t, payload: state["notified"].append((org, t, payload)) or 2)
    monkeypatch.setattr(app_issues, "report_fleet_dark",
                        lambda s, org, name, msg, ctx: state["issues"].append((org, msg)) or "issue-1")
    monkeypatch.setattr(app_issues, "note_returned",
                        lambda s, issue, at: state["returned"].append((issue, at)))

    class S:
        database_url = "postgresql://x"

    # tick 1: dark → opened once
    out = asyncio.run(fleet_alarm.fleet_dark_sweep(S(), now=NOW))
    assert out == {"opened": 1, "closed": 0}
    assert len(state["notified"]) == 1 and state["notified"][0][1] == "fleet_dark"
    assert "All 6 agents" in state["notified"][0][2]["message"]
    assert len(state["issues"]) == 1
    # tick 2: still dark → nothing more
    out = asyncio.run(fleet_alarm.fleet_dark_sweep(S(), now=NOW + timedelta(minutes=5)))
    assert out == {"opened": 0, "closed": 0}
    assert len(state["notified"]) == 1 and len(state["issues"]) == 1
    # tick 3: back → closed, return noted on the same issue, no notification
    state["presence"] = [_org(live=6, last_seen_ago_s=0)]
    out = asyncio.run(fleet_alarm.fleet_dark_sweep(S(), now=NOW + timedelta(minutes=9)))
    assert out == {"opened": 0, "closed": 1}
    assert state["closed"] == [("ep-1", NOW + timedelta(minutes=9))]
    assert state["returned"] == [("issue-1", NOW + timedelta(minutes=9))]
    assert len(state["notified"]) == 1


# ------------------------------------------------------------ incident episodes


class _Cursor:
    def __init__(self, row=None, rows=None):
        self._row, self._rows = row, rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, open_one=None):
        self.calls = []
        self.open_one = open_one

    def execute(self, sql, params=None):
        q = " ".join(sql.split())
        self.calls.append((q, params))
        if "cleared_at is null limit 1" in q:
            return _Cursor(row=self.open_one)
        if "returning id" in q:
            return _Cursor(rows=[{"id": "i1"}, {"id": "i2"}])
        return _Cursor()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_standing_fault_is_raised_once_per_episode(monkeypatch):
    conn = _Conn(open_one=None)
    monkeypatch.setattr(agent_provision, "_connect", lambda s: conn)
    assert agent_provision.raise_service_incident(object(), "org", "w", "revoked", "agent-token") is True
    assert any("insert into public.runner_incidents" in q for q, _ in conn.calls)
    # the dedupe is on an OPEN incident, not on the hour
    q, _ = conn.calls[0]
    assert "cleared_at is null" in q and "interval '1 hour'" not in q

    still_open = _Conn(open_one={"?column?": 1})
    monkeypatch.setattr(agent_provision, "_connect", lambda s: still_open)
    assert agent_provision.raise_service_incident(object(), "org", "w", "revoked", "agent-token") is False
    assert not any("insert into" in q for q, _ in still_open.calls)


def test_clearing_ends_the_episode_so_the_next_is_a_new_incident(monkeypatch):
    conn = _Conn()
    monkeypatch.setattr(agent_provision, "_connect", lambda s: conn)
    assert agent_provision.clear_service_incidents(object(), "w", "agent-service") == 2
    q, params = conn.calls[0]
    assert "set cleared_at = now()" in q and "cleared_at is null" in q
    assert params == ("w", "agent-service")
