"""US-62.9: one page for "is the app fast" -- the summary/detail reads over
what us-62.3 (LLM latency), us-62.7 (frontend Web Vitals) and us-62.8
(API/DB timing) already capture. No new instrumentation here, only queries.
"""

from __future__ import annotations

from app import db


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


# ---------------------------------------------------------------- summary


def test_summary_reads_all_four_layers(monkeypatch):
    def script(q, p):
        if "client_perf_events" in q:
            return FakeCursor({"median": 800.0, "p95": 2100.0, "samples": 40})
        if "api_request_log" in q and "db_ms::numeric" in q:
            return FakeCursor({"median": 90.0, "p95": 400.0, "db_share": 0.6, "samples": 500})
        if "api_request_log" in q:
            return FakeCursor({"median": 40.0, "p95": 220.0, "samples": 500})
        if "llm_usage" in q:
            return FakeCursor({"median": 1200.0, "p95": 4000.0, "samples": 30})
        return FakeCursor()

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_summary(object())
    assert out["frontend"]["median"] == 800
    assert out["frontend"]["p95"] == 2100
    assert out["api"]["median"] == 90
    assert out["api"]["db_time_share_pct"] == 60
    assert out["database"]["median"] == 40
    assert out["llm"]["p95"] == 4000


def test_summary_handles_no_data_without_erroring(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor({"median": None, "p95": None, "samples": 0}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_summary(object())
    assert out["frontend"]["median"] is None
    assert out["frontend"]["samples"] == 0


def test_summary_window_is_clamped(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor({"median": None, "p95": None, "samples": 0}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_summary(object(), days=9999)
    assert out["days"] == 90


def test_frontend_query_filters_to_lcp(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor({"median": None, "p95": None, "samples": 0}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.performance_summary(object())
    q = next(q for q, _ in conn.queries if "client_perf_events" in q)
    assert "metric = 'LCP'" in q


def test_llm_query_excludes_null_latency(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor({"median": None, "p95": None, "samples": 0}))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.performance_summary(object())
    q = next(q for q, _ in conn.queries if "llm_usage" in q)
    assert "latency_ms is not null" in q


# ----------------------------------------------------------------- detail


def test_frontend_detail_groups_by_route(monkeypatch):
    conn = FakeConn(
        lambda q, p: FakeCursor(
            rows=[{"key": "/issues/:id", "samples": 12, "median": 700.0, "p95": 1800.0}]
        )
    )
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_detail(object(), "frontend")
    assert out[0]["key"] == "/issues/:id"
    assert out[0]["median"] == 700
    assert out[0]["db_time_share_pct"] is None


def test_api_detail_includes_db_time_share(monkeypatch):
    conn = FakeConn(
        lambda q, p: FakeCursor(
            rows=[
                {
                    "key": "/api/v1/issues/{issue_id}",
                    "samples": 200,
                    "median": 55.0,
                    "p95": 300.0,
                    "db_share": 0.72,
                }
            ]
        )
    )
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_detail(object(), "api")
    assert out[0]["db_time_share_pct"] == 72


def test_llm_detail_groups_by_model(monkeypatch):
    conn = FakeConn(
        lambda q, p: FakeCursor(
            rows=[{"key": "claude-sonnet-5", "samples": 8, "median": 1100.0, "p95": 3200.0}]
        )
    )
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.performance_detail(object(), "llm")
    assert out[0]["key"] == "claude-sonnet-5"


def test_an_unknown_layer_returns_nothing_not_an_error(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor())
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    assert db.performance_detail(object(), "nonsense") == []
    assert conn.queries == []
