"""US-27.10: cancel a run without calling it a failure.

On 2026-07-26 six plan runs were dispatched by mistake against stories that
already held approved plans. Retiring them meant writing `failed` with an
error string explaining that they had never run. These tests cover the three
shapes cancel has to get right — queued, running, and a run covering several
stories — plus the invariant that keeps a new terminal status from leaking
into the pool.
"""

from __future__ import annotations

import re
from pathlib import Path

from app import db


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class CancelConn:
    def __init__(self, run, members=(), issue_ids=()):
        self.run = run
        self.members = list(members)
        self.issue_ids = list(issue_ids)
        self.calls: list[tuple[str, tuple]] = []
        self.committed = False

    def execute(self, query, params=None):
        q = " ".join(query.split())
        self.calls.append((q, params or ()))
        if "from public.runs where id = %s and status in" in q:
            return FakeCursor(self.run)
        if "from public.run_items" in q:
            return FakeCursor(rows=self.members)
        if "run_issue_ids" in q and q.startswith("select"):
            return FakeCursor(rows=[{"issue_id": i} for i in self.issue_ids])
        return FakeCursor()

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _queued_run(**over):
    run = {
        "id": "11111111-1111-4111-8111-111111111111",
        "org_id": "org-1",
        "issue_id": "issue-1",
        "kind": "plan",
        "status": "queued",
        "prev_issue_status": "planned",
    }
    run.update(over)
    return run


def _install(monkeypatch, conn):
    monkeypatch.setattr(db, "_connect", lambda settings: conn)


def _updates(conn):
    return [
        (p[0], p[1])
        for q, p in conn.calls
        if q.startswith("update public.issues set status = %s")
    ]


def test_a_queued_run_is_cancelled_outright(monkeypatch):
    conn = CancelConn(_queued_run(), issue_ids=["issue-1"])
    _install(monkeypatch, conn)

    out = db.cancel_run(
        object(), _queued_run()["id"], "dispatched by mistake", actor="kaushlesh"
    )

    assert out["status"] == "cancelled"
    assert out["restored_to_status"] == "planned"
    assert any(
        "set status = 'cancelled'" in q for q, _ in conn.calls
    ), "the run itself must land cancelled"
    assert ("planned", "issue-1") in _updates(conn)


def test_a_reason_is_required(monkeypatch):
    conn = CancelConn(_queued_run())
    _install(monkeypatch, conn)
    assert db.cancel_run(object(), _queued_run()["id"], "   ") is None
    assert not conn.calls, "nothing is read, let alone written, without one"


def test_a_running_run_is_asked_to_stop_not_killed(monkeypatch):
    conn = CancelConn(_queued_run(status="running"), issue_ids=["issue-1"])
    _install(monkeypatch, conn)

    out = db.cancel_run(object(), _queued_run()["id"], "wrong branch")

    assert out == {
        "status": "running",
        "stop_requested": True,
        "kind": "plan",
        "reason": "wrong branch",
    }
    assert any("stop_requested_at" in q for q, _ in conn.calls)
    assert not any("set status = 'cancelled'" in q for q, _ in conn.calls)


def test_the_worker_handing_back_lands_it_cancelled(monkeypatch):
    """acknowledge_stop closes the loop the cooperative stop opened."""
    run = {
        "id": "11111111-1111-4111-8111-111111111111",
        "org_id": "org-1",
        "issue_id": "issue-1",
        "kind": "code",
        "prev_issue_status": "planned",
        "cancel_reason": "wrong branch",
    }

    class AckConn(CancelConn):
        def execute(self, query, params=None):
            q = " ".join(query.split())
            self.calls.append((q, params or ()))
            if "stop_requested_at is not null" in q:
                return FakeCursor(run)
            if "from public.run_items" in q:
                return FakeCursor(rows=[])
            if "run_issue_ids" in q and q.startswith("select"):
                return FakeCursor(rows=[{"issue_id": "issue-1"}])
            return FakeCursor()

    conn = AckConn(run)
    _install(monkeypatch, conn)

    out = db.acknowledge_stop(object(), run["id"], "worker-1", note="undone")

    assert out["cancelled"] is True
    assert out["status"] == "cancelled"
    # it must NOT go back to the pool — that is the same mis-dispatch waiting
    # to be claimed again
    assert not any("set status = 'queued'" in q for q, _ in conn.calls)


def test_cancelling_a_multi_story_run_restores_every_member(monkeypatch):
    members = [
        {
            "issue_id": f"story-{i}",
            "position": i,
            "prev_issue_status": "needs-fixes",
            "commits": 0,
        }
        for i in range(1, 7)
    ]
    conn = CancelConn(
        _queued_run(kind="code", issue_id="feature-1"),
        members=members,
        issue_ids=[f"story-{i}" for i in range(1, 7)],
    )
    _install(monkeypatch, conn)

    db.cancel_run(object(), _queued_run()["id"], "re-planned by mistake")

    restored = _updates(conn)
    for i in range(1, 7):
        assert ("needs-fixes", f"story-{i}") in restored
    assert len([q for q, _ in conn.calls if "'run-cancelled'" in q]) == 6


def test_no_query_selects_runs_by_excluding_terminal_statuses():
    """The invariant behind "cancelled never appears in the pool".

    Every predicate that means "this run is still live" is an ALLOW-list
    (`status in ('queued', 'running')`). A deny-list (`status <> 'succeeded'`)
    would have silently admitted `cancelled` the day it was added — and would
    admit the next status the same way."""
    src = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    # US-75.1: match the SQL column, not any identifier ending in "status".
    # The bare substring "status not in" started matching
    # `destination_status not in ("draft", "ready")` — a Python tuple check
    # about reset stages, nothing to do with selecting runs — so this guard
    # spent its time reporting a predicate that was never there. A word
    # boundary in front keeps `r.status` / `runs.status` / a bare `status`
    # in scope while `destination_status` falls out.
    banned = re.compile(r"(?<![\w.])(?:\w+\.)?status\s*(?:<>|!=|not\s+in)", re.I)
    hits = [
        line.strip()
        for line in src.splitlines()
        if banned.search(line)
    ]
    assert not hits, (
        "db.py selects runs with a deny-list; a new terminal status will leak "
        f"into whatever that query feeds:\n  " + "\n  ".join(hits)
    )
