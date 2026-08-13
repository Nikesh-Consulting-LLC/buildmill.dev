"""US-1.17: complete_run computes change metrics from the diff on success."""

import json

from app import db


class FakeCursor:
    def __init__(self, row, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))
        # US-22.9: complete_run returns the run id too, so it can fan the
        # status move out over the run's membership.
        if "returning id, org_id, issue_id" in query:
            return FakeCursor(
                {
                    "id": "run-1",
                    "org_id": "org-1",
                    "issue_id": "issue-1",
                    "kind": "code",
                }
            )
        return FakeCursor(None)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_complete_run_computes_metrics_on_success(monkeypatch):
    fake_conn = FakeConn()
    monkeypatch.setattr(db, "_connect", lambda settings: fake_conn)

    diff = (
        "diff --git a/apps/api/app/main.py b/apps/api/app/main.py\n"
        "--- a/apps/api/app/main.py\n"
        "+++ b/apps/api/app/main.py\n"
        "@@ -1,1 +1,2 @@\n"
        "+added line\n"
    )
    ok = db.complete_run(
        settings=object(),
        run_id="run-1",
        outcome="succeeded",
        stdout="ok",
        diff=diff,
        branch_ref="factory/x",
        pr_url="https://github.com/x/y/pull/1",
        error=None,
    )
    assert ok is True

    update_query, params = fake_conn.calls[0]
    assert "lines_added" in update_query
    # layout: … metrics(4) · usage(3, US-2.15) · claude_session_id (US-59.1) · run_id
    assert params[-9:-5] == (1, 0, 1, json.dumps(
        [{"path": "apps/api/app/main.py", "added": 1, "removed": 0, "area": "backend"}]
    ))
    assert params[-5:-2] == (None, None, None)
    assert params[-2] is None  # claude_session_id, not passed by this call


def test_complete_run_leaves_metrics_null_without_diff(monkeypatch):
    fake_conn = FakeConn()
    monkeypatch.setattr(db, "_connect", lambda settings: fake_conn)

    ok = db.complete_run(
        settings=object(),
        run_id="run-1",
        outcome="failed",
        stdout=None,
        diff=None,
        branch_ref=None,
        pr_url=None,
        error="boom",
    )
    assert ok is True

    _, params = fake_conn.calls[0]
    # US-59.1: shifted one right by the new claude_session_id param — see
    # test_complete_run_computes_metrics_on_success for the full layout.
    assert params[-9:-5] == (None, None, None, None)


def test_complete_run_updates_issue_not_task(monkeypatch):
    """US-2.1: complete_run writes issues/issue_events, never tasks."""
    executed: list[str] = []

    class FakeCursor:
        def fetchone(self):
            return {
                "id": "run-1",
                "org_id": "org-1",
                "issue_id": "issue-1",
                "kind": "code",
            }

        def fetchall(self):
            # US-27.1: no run_items — a single-story run
            return []

    class FakeConn:
        def execute(self, sql, params=None):
            executed.append(" ".join(sql.split()))
            return FakeCursor()

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("app.db._connect", lambda settings: FakeConn())

    from app import db

    ok = db.complete_run(
        settings=None,
        run_id="run-1",
        outcome="succeeded",
        stdout="out",
        diff=None,
        branch_ref="feat/x",
        pr_url="https://github.com/acme/webshop/pull/1",
        error=None,
    )

    assert ok is True
    joined = " ".join(executed)
    assert "public.issues" in joined
    assert "public.issue_events" in joined
    assert "public.tasks" not in joined
    assert "public.task_events" not in joined


# ----------------------------------- US-15.8: acceptance_criteria coercion


def test_coerce_criteria_keeps_array_of_strings():
    assert db.coerce_acceptance_criteria(["a", "b"]) == ["a", "b"]


def test_coerce_criteria_drops_blank_and_stringifies():
    assert db.coerce_acceptance_criteria(["a", "", "  ", 7]) == ["a", "7"]


def test_coerce_criteria_splits_numbered_block():
    assert db.coerce_acceptance_criteria("1. do a\n2. do b\n3. do c") == [
        "do a",
        "do b",
        "do c",
    ]


def test_coerce_criteria_strips_bullet_and_paren_markers():
    assert db.coerce_acceptance_criteria("- do a\n2) do b\n* do c") == [
        "do a",
        "do b",
        "do c",
    ]


def test_coerce_criteria_non_list_non_string_is_empty():
    assert db.coerce_acceptance_criteria(None) == []
    assert db.coerce_acceptance_criteria({"a": 1}) == []
