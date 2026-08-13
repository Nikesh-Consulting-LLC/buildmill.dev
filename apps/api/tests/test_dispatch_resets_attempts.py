"""US-36.2: a manual dispatch resets the attempt count.

us-31.5 made releasing a blocked item a separate, deliberate action — correctly,
because the problem it solved was an AGENT looping unattended on work it could
not finish. These tests pin the narrower rule that replaces it: the manager's
own dispatch resets, every automatic path still hits the cap.
"""

from __future__ import annotations

import json
import uuid

import pytest

from app import db

ISSUE = str(uuid.uuid4())
ORG = str(uuid.uuid4())


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Scripts the three statements `reset_issue_attempts` issues."""

    def __init__(self, issue_row, attempts):
        self.issue_row = issue_row
        self.attempts = attempts
        self.statements: list[tuple[str, tuple]] = []
        self.committed = False

    def execute(self, query, params=None):
        flat = " ".join(query.split())
        self.statements.append((flat, params))
        if flat.startswith("select id, org_id from public.issues"):
            return FakeCursor([self.issue_row] if self.issue_row else [])
        if flat.startswith("delete from public.run_attempts"):
            return FakeCursor(self.attempts)
        return FakeCursor([])

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wrote(self, *needles):
        return [s for s, _ in self.statements if all(n in s for n in needles)]

    def event_payload(self):
        for flat, params in self.statements:
            if "insert into public.issue_events" in flat:
                return json.loads(params[2])
        return None


_MISSING = object()


def _conn(monkeypatch, attempts, issue_row=_MISSING):
    if issue_row is _MISSING:
        issue_row = {"id": ISSUE, "org_id": ORG}
    conn = FakeConn(issue_row, attempts)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


def test_dispatch_clears_the_latch_and_the_history(monkeypatch):
    """Clearing only the latch would let the very next failure re-block it, so
    the manager would be back where they started after a single try."""
    conn = _conn(monkeypatch, attempts=[{"id": 1}, {"id": 2}, {"id": 3}])

    cleared = db.reset_issue_attempts(object(), ISSUE, actor="me@example.com")

    assert cleared == 3
    assert conn.wrote("delete from public.run_attempts")
    assert conn.wrote("update public.issues", "attempts_blocked_at = null")
    assert conn.committed


def test_it_resets_even_when_the_item_is_not_blocked(monkeypatch):
    """`release_attempt_block` acts only on a latched item. A dispatch must mean
    the same thing regardless of a counter the manager cannot see."""
    conn = _conn(monkeypatch, attempts=[{"id": 1}])

    assert db.reset_issue_attempts(object(), ISSUE) == 1
    # no `and attempts_blocked_at is not null` guard on the update
    updates = conn.wrote("update public.issues", "attempts_blocked_at = null")
    assert updates and "is not null" not in updates[0]


def test_an_event_records_who_released_and_how_many(monkeypatch):
    conn = _conn(monkeypatch, attempts=[{"id": 1}, {"id": 2}])

    db.reset_issue_attempts(object(), ISSUE, actor="me@example.com")

    payload = conn.event_payload()
    assert payload == {"cleared": 2, "actor": "me@example.com", "via": "dispatch"}


def test_a_clean_item_writes_no_event(monkeypatch):
    """A dispatch of a healthy item must not litter its timeline."""
    conn = _conn(monkeypatch, attempts=[])

    assert db.reset_issue_attempts(object(), ISSUE) == 0
    assert conn.event_payload() is None


def test_an_unknown_issue_is_a_no_op(monkeypatch):
    conn = _conn(monkeypatch, attempts=[{"id": 1}], issue_row=None)

    assert db.reset_issue_attempts(object(), ISSUE) == 0
    assert not conn.wrote("delete from public.run_attempts")


@pytest.mark.parametrize("bad", ["", "not-a-uuid", "123"])
def test_a_malformed_id_never_reaches_the_database(monkeypatch, bad):
    def explode(_s):  # pragma: no cover - must not be called
        raise AssertionError("should not open a connection")

    monkeypatch.setattr(db, "_connect", explode)
    assert db.reset_issue_attempts(object(), bad) == 0


def test_the_reset_only_touches_this_issue(monkeypatch):
    """The cap us-31.5 built lives in a trigger on `runs` and must keep
    covering every automatic path. The reset is scoped to one item and writes
    nothing to `runs` — so nothing an agent does is affected."""
    conn = _conn(monkeypatch, attempts=[{"id": 1}])

    db.reset_issue_attempts(object(), ISSUE)

    assert not conn.wrote("public.runs"), "the reset must not touch runs"
    for _flat, params in conn.statements:
        if params:
            assert ISSUE in [str(p) for p in params], (
                "every statement is scoped to the one issue"
            )


def test_the_explicit_release_still_exists():
    """A manager who wants to clear the block WITHOUT running it keeps that."""
    assert callable(db.release_attempt_block)
