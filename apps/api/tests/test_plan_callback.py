"""US-2.5: plan-run callback stores artifacts and moves to plan-review."""

import json

from app import db


def test_complete_plan_run_writes_artifacts(monkeypatch):
    executed: list[tuple[str, tuple | None]] = []

    class FakeCursor:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

        def fetchall(self):
            # US-27.1: no run_items — a single-story plan run
            return []

    class FakeConn:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))
            q = " ".join(sql.split())
            # US-22.9: complete_run also returns the run id now, so it can
            # fan the status move out over run_issue_ids.
            if "returning id, org_id, issue_id, kind" in q:
                return FakeCursor(
                    {
                        "id": "run-plan-1",
                        "org_id": "org-1",
                        "issue_id": "issue-1",
                        "kind": "plan",
                    }
                )
            if "coalesce(max(version)" in q:
                return FakeCursor({"v": 1})
            return FakeCursor(None)

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "_connect", lambda settings: FakeConn())

    ok = db.complete_run(
        settings=object(),
        run_id="run-plan-1",
        outcome="succeeded",
        stdout="planned",
        diff=None,
        branch_ref=None,
        pr_url=None,
        error=None,
        plan="# Implementation plan\nDo the thing.",
        test_plan="# Test plan\n```json\n[]\n```",
    )
    assert ok is True
    joined = " ".join(q for q, _ in executed)
    assert "plan-review" in joined or any(
        p and "plan-review" in str(p) for _, p in executed
    )
    assert "public.artifacts" in joined
    assert any(
        p and p[2] == "plan" for _, p in executed if p and len(p) >= 3
    )
    # event type
    assert any(
        p and "plan-ready" in p for _, p in executed if p
    )
    # superseded prior drafts
    assert "superseded" in joined
