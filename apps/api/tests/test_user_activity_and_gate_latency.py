"""US-62.4 / US-62.5: a human's work in one report, and how long each gate
waited for a decision. Every source is a table that already attributes a
real user id -- these tests pin the merge, the exclusions, and the shape.
"""

from __future__ import annotations

from app import db

ORG = "11111111-1111-1111-1111-111111111111"
PROJECT = "22222222-2222-2222-2222-222222222222"
USER = "33333333-3333-3333-3333-333333333333"


class FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


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


# ------------------------------------------------------------- user_activity


def test_user_activity_merges_three_sources_into_one_row(monkeypatch):
    def script(q, p):
        if "from public.approvals a" in q:
            return FakeCursor([{"user_id": USER, "org_id": ORG, "project_id": PROJECT, "approved": 3}])
        if "from public.test_run_results" in q:
            return FakeCursor(
                [{"user_id": USER, "org_id": ORG, "project_id": PROJECT, "test_pass": 5, "test_fail": 1}]
            )
        if "from public.issue_comments" in q:
            return FakeCursor([{"user_id": USER, "org_id": ORG, "project_id": PROJECT, "comments": 2}])
        if "from public.profiles" in q:
            return FakeCursor([{"id": USER, "name": "Kaushlesh"}])
        if "from public.projects" in q:
            return FakeCursor([{"id": PROJECT, "name": "Demo"}])
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.user_activity(object(), ORG)
    assert len(out["rows"]) == 1
    row = out["rows"][0]
    assert row["approved"] == 3
    assert row["test_pass"] == 5
    assert row["test_fail"] == 1
    assert row["comments"] == 2
    assert row["user_label"] == "Kaushlesh"
    assert row["project_label"] == "Demo"


def test_user_activity_sums_reviewing_ms_from_activity_sessions(monkeypatch):
    """US-62.6: real, instrumented active time -- distinct from us-62.5's
    queue-inclusive latency, summed per user/project alongside the counts."""

    def script(q, p):
        if "from public.user_activity_sessions" in q:
            return FakeCursor(
                [{"user_id": USER, "org_id": ORG, "project_id": PROJECT, "reviewing_ms": 754000}]
            )
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.user_activity(object(), ORG)
    assert out["rows"][0]["reviewing_ms"] == 754000


def test_user_activity_excludes_auto_approved(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor([]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.user_activity(object(), ORG)
    q = conn.queries[0][0]
    assert "not a.auto_approved" in q


def test_user_activity_org_none_means_every_org(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor([]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.user_activity(object(), None)
    q, params = conn.queries[0]
    assert "i.org_id = %s" not in q
    assert ORG not in (params or ())


def test_user_activity_window_is_clamped(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor([]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.user_activity(object(), ORG, days=9999)
    assert out["days"] == 366


def test_a_user_with_no_activity_never_appears(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor([]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.user_activity(object(), ORG)
    assert out["rows"] == []


# -------------------------------------------------------------- gate_latency


def test_every_gate_asks_for_its_own_ready_event(monkeypatch):
    seen_ready_types = []

    def script(q, p):
        if "join lateral" in q:
            seen_ready_types.append(p[0])
            return FakeCursor([])
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.gate_latency(object(), ORG)
    assert set(seen_ready_types) == set(db.GATE_READY_EVENTS.values())


def test_auto_approved_is_counted_separately_never_in_latency(monkeypatch):
    def script(q, p):
        if "join lateral" in q:
            return FakeCursor(
                [{"user_id": USER, "org_id": ORG, "project_id": PROJECT,
                  "decisions": 2, "avg_seconds": 100.0, "min_seconds": 50.0,
                  "max_seconds": 150.0, "p95_seconds": 145.0}]
            )
        if "a.auto_approved" in q and "join lateral" not in q:
            return FakeCursor([{"org_id": ORG, "project_id": PROJECT, "auto_approved": 4}])
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.gate_latency(object(), ORG)
    # Every gate's latency rows are real (non-auto) decisions only.
    assert all(r["decisions"] == 2 for r in out["rows"] if r["gate"] in db.GATE_READY_EVENTS)
    # Auto-approved counts exist, per gate, separate from the latency rows.
    assert len(out["auto_approved"]) == len(db.GATE_READY_EVENTS)
    assert all(a["auto_approved"] == 4 for a in out["auto_approved"])


def test_qa_signoff_and_promotion_use_release_milestones(monkeypatch):
    def script(q, p):
        if "from public.releases" in q and "signed_off_at" in q and "uat_deployed_at" in q:
            return FakeCursor(
                [{"user_id": USER, "org_id": ORG, "project_id": PROJECT, "decisions": 1,
                  "avg_seconds": 3600.0, "min_seconds": 3600.0, "max_seconds": 3600.0,
                  "p95_seconds": 3600.0}]
            )
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.gate_latency(object(), ORG)
    gates = {r["gate"] for r in out["rows"]}
    assert "qa-signoff" in gates


def test_promotion_measures_signoff_to_promoted(monkeypatch):
    seen = []

    def script(q, p):
        if "from public.releases" in q:
            seen.append(q)
        return FakeCursor([])

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.gate_latency(object(), ORG)
    promotion_q = next(q for q in seen if "promoted_by" in q)
    assert "r.signed_off_at" in promotion_q
    assert "r.promoted_at" in promotion_q
