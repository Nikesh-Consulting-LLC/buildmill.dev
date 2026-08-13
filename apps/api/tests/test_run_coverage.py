"""US-27.1: a run moves the stories it has commits for, and nothing else.

Run 11c564b0 (2026-07-26) built six stories, landed one of three hand-backs,
and moved all six to `in-review`. The fan-out asked the run's status what
happened instead of asking the record. These tests replay that exact shape
against the fan-out with no database: six members, four with a landed commit.
"""

from __future__ import annotations

from app import db


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FanOutConn:
    """Answers the two queries the fan-out asks, and records the rest."""

    def __init__(self, members):
        self.members = members
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        self.calls.append((" ".join(query.split()), params or ()))
        if "from public.run_items" in query:
            return FakeCursor(rows=self.members)
        return FakeCursor()

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _members(landed_positions: set[int], total: int = 6):
    return [
        {
            "issue_id": f"story-{i}",
            "position": i,
            "prev_issue_status": "planned",
            "commits": 1 if i in landed_positions else 0,
        }
        for i in range(1, total + 1)
    ]


RUN = {"id": "run-1", "org_id": "org-1", "issue_id": "feature-1", "kind": "code"}


def _status_updates(conn):
    """(status, target) for every issue-status update the fan-out made."""
    out = []
    for query, params in conn.calls:
        if query.startswith("update public.issues set status = %s"):
            out.append((params[0], params[1]))
    return out


def test_only_stories_with_a_landed_commit_reach_review():
    """The 2026-07-26 shape: four of six stories have a commit."""
    conn = FanOutConn(_members({1, 2, 3, 4}))
    db._fan_out_issue_status(conn, RUN, "in-review")

    updates = _status_updates(conn)
    reviewed = [t for status, t in updates if status == "in-review"]
    assert reviewed[0] == ["story-1", "story-2", "story-3", "story-4"]
    # the two with no commit go back to where they were, not to review
    returned = [t for status, t in updates if status == "planned"]
    assert returned == ["story-5", "story-6"]


def test_an_unlanded_story_is_told_why_it_went_back():
    conn = FanOutConn(_members({1}))
    db._fan_out_issue_status(conn, RUN, "in-review")

    events = [
        params
        for query, params in conn.calls
        if "'returned-to-pool'" in query
    ]
    assert len(events) == 5
    assert "without a commit covering this story" in events[0][2]


def test_the_runs_own_issue_moves_with_its_members():
    """The feature stayed at `queued` while all six children passed it."""
    conn = FanOutConn(_members({1, 2, 3, 4}))
    db._fan_out_issue_status(conn, RUN, "in-review")

    assert ("in-review", "feature-1") in _status_updates(conn)


def test_a_failure_is_shared_by_every_member():
    """A commit that landed inside a failed run is not finished work."""
    conn = FanOutConn(_members({1, 2}))
    db._fan_out_issue_status(conn, RUN, "failed")

    updates = _status_updates(conn)
    assert updates[0] == ("failed", [f"story-{i}" for i in range(1, 7)])
    assert ("failed", "feature-1") in updates
    assert not [p for q, p in conn.calls if "'returned-to-pool'" in q]


def test_a_single_story_run_is_untouched_by_any_of_this():
    """No run_items means runs.issue_id is the whole membership — the
    pre-US-27.1 statement, unchanged."""
    conn = FanOutConn([])
    db._fan_out_issue_status(conn, RUN, "in-review")

    assert len(conn.calls) == 2  # the members read, then one update
    query, params = conn.calls[1]
    assert "run_issue_ids" in query
    assert params == ("in-review", "run-1")
