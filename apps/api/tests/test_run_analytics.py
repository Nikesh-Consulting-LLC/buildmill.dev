"""US-62.1: task runs, sliced by kind/project/org/agent, with the duration
spread (avg/min/max/p95) a timeout decision needs -- not just an average.
SQL-shape level with a fake connection, mirroring how `preset_outcomes`
(the pattern this generalizes) is pinned in test_escalation_and_overrides.py.
"""

from __future__ import annotations

import pytest

from app import db

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
WORKER = "33333333-3333-3333-3333-333333333333"


class FakeCursor:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([row] if row else [])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, script):
        self.queries: list[tuple[str, tuple | None]] = []
        self.script = script

    def execute(self, q, p=None):
        self.queries.append((" ".join(q.split()), p))
        return self.script(q, p)

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False


def _rows_conn(rows):
    return FakeConn(lambda q, p: FakeCursor(rows=rows))


# ------------------------------------------------------------- run_analytics


def test_unknown_dimension_falls_back_to_kind(monkeypatch):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics(object(), ORG, group_by="nonsense")
    assert out["group_by"] == "kind"
    assert "r.kind as key" in conn.queries[0][0]


@pytest.mark.parametrize(
    "group_by,expr",
    [
        ("kind", "r.kind as key"),
        ("project", "r.project_id::text as key"),
        ("org", "r.org_id::text as key"),
        ("agent", "r.worker_id::text as key"),
    ],
)
def test_every_dimension_groups_on_its_own_column(monkeypatch, group_by, expr):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), ORG, group_by=group_by)
    assert expr in conn.queries[0][0]


def test_cancelled_is_its_own_bucket_not_excluded(monkeypatch):
    """Unlike preset_outcomes (which excludes cancelled runs entirely), this
    report counts them separately -- a manually-cancelled kind is a different
    signal from a timing-out one."""
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), ORG)
    q = conn.queries[0][0]
    assert "filter (where r.status = 'cancelled')" in q
    assert "r.status in (" not in q  # no exclusion filter, unlike preset_outcomes


def test_reports_min_max_p95_alongside_the_average(monkeypatch):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), ORG)
    q = conn.queries[0][0]
    assert "avg(extract(epoch from (r.finished_at - r.started_at)))" in q
    assert "min(extract(epoch from (r.finished_at - r.started_at)))" in q
    assert "max(extract(epoch from (r.finished_at - r.started_at)))" in q
    assert "percentile_cont(0.95) within group" in q


@pytest.mark.parametrize("given,expected", [(30, 30), (0, 1), (9999, 366), ("x", 30)])
def test_the_window_is_clamped(monkeypatch, given, expected):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), ORG, days=given)
    assert f"interval '{expected} days'" in conn.queries[0][0]


def test_org_id_none_means_every_org_at_once(monkeypatch):
    """Only the platform-admin route may call it this way (US-60.2's rule)."""
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), None)
    q, params = conn.queries[0]
    assert "r.org_id = %s" not in q
    assert ORG not in (params or ())


def test_optional_filters_are_appended(monkeypatch):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(
        object(), ORG, project_id=PROJECT, worker_id=WORKER, kind="code"
    )
    q, params = conn.queries[0]
    assert "r.project_id = %s" in q
    assert "r.worker_id = %s" in q
    assert "r.kind = %s" in q
    assert list(params) == [ORG, PROJECT, WORKER, "code"]


def test_success_rate_and_rounding(monkeypatch):
    rows = [
        {
            "key": "code",
            "runs": 8,
            "succeeded": 6,
            "failed": 1,
            "stopped": 1,
            "cancelled": 0,
            "cost_usd": 3.0,
            "avg_seconds": 120.4,
            "min_seconds": 10.0,
            "max_seconds": 900.9,
            "p95_seconds": 850.2,
        }
    ]
    conn = _rows_conn(rows)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics(object(), ORG)
    row = out["rows"][0]
    assert row["success_rate"] == 0.75
    assert row["avg_seconds"] == 120
    assert row["max_seconds"] == 901
    assert row["p95_seconds"] == 850


def test_agent_labels_resolve_through_workers_and_principals(monkeypatch):
    calls = []

    def script(q, p):
        calls.append((" ".join(q.split()), p))
        if "select w.id::text as id" in q:
            return FakeCursor(rows=[{"id": WORKER, "name": "Programmer"}])
        if "run_attempts" in q:
            return FakeCursor(rows=[])
        return FakeCursor(rows=[{"key": WORKER, "runs": 1, "succeeded": 1, "failed": 0,
                                  "stopped": 0, "cancelled": 0, "cost_usd": None,
                                  "avg_seconds": None, "min_seconds": None,
                                  "max_seconds": None, "p95_seconds": None}])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics(object(), ORG, group_by="agent")
    assert out["rows"][0]["label"] == "Programmer"


def test_agent_grouping_attaches_attempts_per_run(monkeypatch):
    """US-62.2: run_attempts is the only real retry count -- a lease requeue
    mutates the run row in place rather than adding one to `runs`."""

    def script(q, p):
        if "select w.id::text as id" in q:
            return FakeCursor(rows=[{"id": WORKER, "name": "Programmer"}])
        if "run_attempts" in q:
            return FakeCursor(rows=[{"id": WORKER, "attempts": 6}])
        return FakeCursor(
            rows=[
                {
                    "key": WORKER, "runs": 4, "succeeded": 2, "failed": 2,
                    "stopped": 0, "cancelled": 0, "cost_usd": None,
                    "avg_seconds": None, "min_seconds": None,
                    "max_seconds": None, "p95_seconds": None,
                }
            ]
        )

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics(object(), ORG, group_by="agent")
    row = out["rows"][0]
    assert row["attempts"] == 6
    assert row["attempts_per_run"] == 1.5


def test_attempts_is_null_for_non_agent_dimensions(monkeypatch):
    conn = _rows_conn(
        [{"key": "code", "runs": 1, "succeeded": 1, "failed": 0, "stopped": 0,
          "cancelled": 0, "cost_usd": None, "avg_seconds": None, "min_seconds": None,
          "max_seconds": None, "p95_seconds": None}]
    )
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics(object(), ORG, group_by="kind")
    row = out["rows"][0]
    assert row["attempts"] is None
    assert row["attempts_per_run"] is None


def test_a_kind_group_needs_no_label_lookup(monkeypatch):
    """kind is already human-legible; resolving it against a table would be
    a query for nothing."""
    conn = _rows_conn(
        [{"key": "code", "runs": 1, "succeeded": 1, "failed": 0, "stopped": 0,
          "cancelled": 0, "cost_usd": None, "avg_seconds": None, "min_seconds": None,
          "max_seconds": None, "p95_seconds": None}]
    )
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics(object(), ORG, group_by="kind")
    assert len(conn.queries) == 1


# ------------------------------------------------------ run_analytics_detail


def test_detail_uses_the_matching_key_column(monkeypatch):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.run_analytics_detail(object(), ORG, group_by="agent", key=WORKER)
    q, params = conn.queries[0]
    assert "r.worker_id = %s" in q
    assert params[0] == WORKER


def test_detail_is_empty_for_an_unknown_dimension_or_missing_key(monkeypatch):
    conn = _rows_conn([])
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    assert db.run_analytics_detail(object(), ORG, group_by="nonsense", key="x") == []
    assert db.run_analytics_detail(object(), ORG, group_by="kind", key="") == []
    assert conn.queries == []


def test_detail_computes_seconds_from_the_timestamps(monkeypatch):
    import datetime

    started = datetime.datetime(2026, 1, 1, 0, 0, 0)
    finished = datetime.datetime(2026, 1, 1, 0, 2, 0)
    rows = [
        {
            "id": "44444444-4444-4444-4444-444444444444",
            "kind": "code",
            "status": "succeeded",
            "created_at": started,
            "started_at": started,
            "finished_at": finished,
            "cost_usd": None,
            "error": None,
            "worker_name": "Programmer",
            "issue_title": "AI configurations",
        }
    ]
    conn = _rows_conn(rows)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.run_analytics_detail(object(), ORG, group_by="kind", key="code")
    assert out[0]["seconds"] == 120
