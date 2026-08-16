"""Phase 95: the Costs section's API surface.

us-95.2 — the daily curve (`spend_trend`): zero-filled days, the previous
window as one total, and the same predicate the breakdown uses so the two can
never disagree. us-95.3 — the work-shaped dimensions (type / epic / item)
resolved through the run -> issue walk, with LEFT joins so the unattributable
money stays a named row. us-95.4 — the item_type filter, parameterised and
composable. Route-level: the trend endpoint answers a refusal — not data —
without the `view_costs` capability, while the shared /spend endpoint stays
member-read (us-95.1).
"""

from __future__ import annotations

import datetime as dt

import pytest

from app import db


class FakeCursor:
    def __init__(self, rows):
        self._rows = rows

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
        return FakeCursor(self.script(q, p))

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *e):
        return False


ORG = "11111111-1111-1111-1111-111111111111"
EPIC = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ITEM = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _usage_rows(*specs):
    return [
        {
            "key": key,
            "tokens_in": tin,
            "tokens_out": tout,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": cost,
            "calls": calls,
            "unparsed_calls": unparsed,
        }
        for key, tin, tout, cost, calls, unparsed in specs
    ]


def _wire(monkeypatch, usage_rows, script_extra=None):
    def script(q, p):
        if "from public.llm_usage" in q:
            return usage_rows
        if script_extra:
            return script_extra(q, p)
        return []

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


def _usage_query(conn):
    return [x for x in conn.queries if "from public.llm_usage" in x[0]][0]


# ------------------------------------------- us-95.3: the work-shaped dimensions


@pytest.mark.parametrize("dimension", ["type", "epic", "item"])
def test_work_dimensions_walk_run_to_issue(monkeypatch, dimension):
    conn = _wire(monkeypatch, [])
    out = db.spend_breakdown(object(), ORG, group_by=dimension)
    assert out["group_by"] == dimension
    q, _ = _usage_query(conn)
    assert db.SPEND_DIMENSIONS[dimension] in q
    assert "left join public.runs r on r.id = u.run_id" in q
    assert "left join public.issues i on i.id = r.issue_id" in q


@pytest.mark.parametrize("dimension", ["project", "agent", "provider", "model", "org"])
def test_infrastructure_dimensions_still_skip_the_join(monkeypatch, dimension):
    """The four US-33.3 dimensions answered without the walk for a year; a
    join they don't read must not tax them."""
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, group_by=dimension)
    q, _ = _usage_query(conn)
    assert "join public.runs" not in q


def test_the_joins_are_left_so_unattributable_money_keeps_its_row(monkeypatch):
    """us-95.3 AC3/AC4: an inner join would silently drop session calls and
    batch-run calls — grouping by type would then show less money than
    grouping by project, which is exactly the defect the story forbids."""
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, group_by="type")
    q, _ = _usage_query(conn)
    assert "left join public.runs" in q
    assert " inner join" not in q


def test_the_unattributable_bucket_is_named_not_dropped(monkeypatch):
    _wire(monkeypatch, _usage_rows((None, 20, 10, 0.5, 3, 0)))
    out = db.spend_breakdown(object(), ORG, group_by="type")
    assert out["rows"][0]["label"] == "Not attributable to a work item"
    assert out["totals"]["cost_usd"] == 0.5


def test_infrastructure_null_keys_keep_their_old_label(monkeypatch):
    _wire(monkeypatch, _usage_rows((None, 20, 10, 0.5, 1, 0)))
    out = db.spend_breakdown(object(), ORG, group_by="project")
    assert out["rows"][0]["label"] == "unattributed"


def test_type_keys_read_as_labels(monkeypatch):
    _wire(monkeypatch, _usage_rows(("bug", 10, 5, 1.0, 2, 0), ("feature", 5, 2, 0.5, 1, 0)))
    out = db.spend_breakdown(object(), ORG, group_by="type")
    assert [r["label"] for r in out["rows"]] == ["Bug", "Feature"]


def test_epic_labels_carry_number_title_and_project(monkeypatch):
    """us-95.3 AC2: epic numbers repeat across projects — a bare E4 from two
    projects would collapse two epics into one label."""

    def extra(q, p):
        if "from public.epics" in q:
            return [{"id": EPIC, "number": 4, "title": "Costs", "project": "Alpha"}]
        return []

    _wire(monkeypatch, _usage_rows((EPIC, 10, 5, 1.0, 2, 0)), extra)
    out = db.spend_breakdown(object(), ORG, group_by="epic")
    assert out["rows"][0]["label"] == "E4 — Costs · Alpha"


def test_item_labels_use_the_display_id_with_title_fallback(monkeypatch):
    ITEM2 = "cccccccc-cccc-cccc-cccc-cccccccccccc"

    def extra(q, p):
        if "from public.issues" in q:
            return [
                {"id": ITEM, "title": "The door", "display_id": "US-4.2.1"},
                {"id": ITEM2, "title": "Unnumbered spike", "display_id": None},
            ]
        return []

    _wire(
        monkeypatch,
        _usage_rows((ITEM, 10, 5, 1.0, 2, 0), (ITEM2, 5, 2, 0.5, 1, 0)),
        extra,
    )
    out = db.spend_breakdown(object(), ORG, group_by="item")
    assert out["rows"][0]["label"] == "US-4.2.1 — The door"
    assert out["rows"][1]["label"] == "Unnumbered spike"


def test_rows_order_by_cost_not_cache_reads(monkeypatch):
    """Regression: US-38.1 inserted the two cache columns at positions 4-5,
    silently shifting the `order by 4` that had meant cost onto cache reads.
    Position 6 is cost again; this pins it."""
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG)
    q, _ = _usage_query(conn)
    assert "order by 6 desc nulls last, 2 desc" in q


# ------------------------------------------------- us-95.4: the item_type filter


def test_an_item_type_filter_is_parameterised_and_forces_the_walk(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, group_by="project", item_type="bug")
    q, p = _usage_query(conn)
    assert "i.type = %s" in q
    assert "left join public.issues" in q
    assert "bug" in p


def test_a_junk_item_type_is_dropped_not_interpolated(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, item_type="'; drop table runs; --")
    q, p = _usage_query(conn)
    assert "i.type" not in q
    assert p == (ORG,)


def test_filters_compose_with_a_work_grouping(monkeypatch):
    P1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, group_by="epic", project_id=P1, item_type="story")
    q, p = _usage_query(conn)
    assert "u.project_id = %s" in q
    assert "i.type = %s" in q
    assert p == (ORG, P1, "story")


# ------------------------------------------------------- us-95.2: the daily curve


def _trend_rows(*specs):
    return [
        {
            "day": day,
            "in_window": in_window,
            "cost_usd": cost,
            "tokens_in": tin,
            "tokens_out": tout,
            "calls": calls,
            "unparsed_calls": unparsed,
        }
        for day, in_window, cost, tin, tout, calls, unparsed in specs
    ]


def test_the_series_fills_every_day_with_honest_zeros(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    _wire(
        monkeypatch,
        _trend_rows((today, True, 2.5, 100, 50, 3, 0)),
    )
    out = db.spend_trend(object(), ORG, days=7)
    # N days back through today: N+1 calendar buckets, the first partial.
    assert len(out["series"]) == 8
    assert out["series"][-1]["cost_usd"] == 2.5
    assert all(p["cost_usd"] == 0.0 for p in out["series"][:-1])
    assert out["total_cost_usd"] == 2.5


def test_the_previous_window_is_one_total_with_its_calls(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    _wire(
        monkeypatch,
        _trend_rows(
            (today, True, 1.0, 10, 5, 1, 0),
            (today - dt.timedelta(days=10), False, 4.0, 40, 20, 2, 0),
            (today - dt.timedelta(days=12), False, 2.0, 20, 10, 1, 0),
        ),
    )
    out = db.spend_trend(object(), ORG, days=7)
    assert out["previous_cost_usd"] == 6.0
    assert out["previous_calls"] == 3
    assert out["total_cost_usd"] == 1.0


def test_an_empty_previous_window_is_none_not_zero(monkeypatch):
    """A percentage of zero is not a number; the client must be able to say
    'nothing to compare against' rather than +infinity."""
    today = dt.datetime.now(dt.timezone.utc).date()
    _wire(monkeypatch, _trend_rows((today, True, 1.0, 10, 5, 1, 0)))
    out = db.spend_trend(object(), ORG, days=7)
    assert out["previous_cost_usd"] is None
    assert out["previous_calls"] == 0


def test_a_straddling_day_is_split_by_the_in_window_flag(monkeypatch):
    """The calendar day the cutoff falls on can hold both in-window and
    out-of-window rows; the flag keeps each portion on its own side, so the
    series still sums to exactly the breakdown's total."""
    today = dt.datetime.now(dt.timezone.utc).date()
    edge = today - dt.timedelta(days=7)
    _wire(
        monkeypatch,
        _trend_rows(
            (edge, True, 1.0, 10, 5, 1, 0),
            (edge, False, 9.0, 90, 45, 2, 0),
        ),
    )
    out = db.spend_trend(object(), ORG, days=7)
    assert out["series"][0]["day"] == edge.isoformat()
    assert out["series"][0]["cost_usd"] == 1.0
    assert out["previous_cost_usd"] == 9.0
    assert out["total_cost_usd"] == 1.0


def test_the_trend_queries_double_the_window_once(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_trend(object(), ORG, days=30)
    q, _ = _usage_query(conn)
    assert "interval '60 days'" in q  # one scan covers both windows
    assert "interval '30 days'" in q  # the flag that splits them


def test_trend_filters_match_the_breakdowns(monkeypatch):
    P1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    W1 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    conn = _wire(monkeypatch, [])
    db.spend_trend(object(), ORG, days=7, project_id=P1, worker_id=W1, item_type="bug")
    q, p = _usage_query(conn)
    assert "u.project_id = %s" in q
    assert "u.worker_id = %s" in q
    assert "i.type = %s" in q
    assert "left join public.issues" in q
    assert p == (ORG, P1, W1, "bug")


def test_the_trend_window_is_clamped_like_the_breakdowns(monkeypatch):
    _wire(monkeypatch, [])
    assert db.spend_trend(object(), ORG, days=9999)["days"] == 366
    assert db.spend_trend(object(), ORG, days="x")["days"] == 30


def test_unparsed_calls_ride_the_series(monkeypatch):
    today = dt.datetime.now(dt.timezone.utc).date()
    _wire(monkeypatch, _trend_rows((today, True, 0.0, 0, 0, 4, 4)))
    out = db.spend_trend(object(), ORG, days=7)
    assert out["unparsed_calls"] == 4
    # calls happened but none carried a price: 0.0, not None — the calls are
    # a fact even when the money is unknown.
    assert out["total_cost_usd"] == 0.0


# --------------------------------------------- the routes: gate vs member-read


def _fake_rpc(member=True, view_costs=True):
    async def fake_rpc(settings, user_token, fn, args):
        if fn == "is_org_member":
            return member
        if fn == "has_org_capability":
            assert args["p_capability"] == "view_costs"
            return view_costs
        raise AssertionError(f"unexpected rpc {fn}")

    return fake_rpc


def test_spend_trend_refuses_without_the_capability(client, make_token, monkeypatch):
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(view_costs=False))
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/spend-trend",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 403
    assert "owners and admins" in resp.json()["detail"]


def test_spend_trend_answers_holders_and_passes_the_filters(
    client, make_token, monkeypatch
):
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(view_costs=True))
    captured = {}

    def fake_trend(settings, org_id, **kw):
        captured["org_id"] = org_id
        captured.update(kw)
        return {"days": kw.get("days"), "series": [], "total_cost_usd": None,
                "previous_cost_usd": None, "previous_calls": 0, "calls": 0,
                "unparsed_calls": 0}

    monkeypatch.setattr("app.db.spend_trend", fake_trend)
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/spend-trend",
        params={"days": 7, "item_type": "bug"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["org_id"] == ORG
    assert captured["days"] == 7
    assert captured["item_type"] == "bug"


def test_shared_spend_stays_member_read_and_carries_item_type(
    client, make_token, monkeypatch
):
    """us-95.1: the member-visible surfaces (project card, agent page) read
    /spend — gating it on view_costs would break them. It stays member-read;
    only the section's own endpoint checks the key."""
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(member=True))
    captured = {}

    def fake_breakdown(settings, org_id, **kw):
        captured.update(kw)
        return {"group_by": "type", "days": 30, "rows": [], "totals": {}}

    monkeypatch.setattr("app.db.spend_breakdown", fake_breakdown)
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/spend",
        params={"group_by": "type", "item_type": "bug"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["group_by"] == "type"
    assert captured["item_type"] == "bug"


def test_spend_still_refuses_non_members(client, make_token, monkeypatch):
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(member=False))
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/spend",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 403


# ------------------------------------------- us-102.2: what the money bought


def _summary_row(**over):
    row = {
        "work_seconds": 7200,
        "runs": 4,
        "items_landed": 2,
        "bugs_landed": 1,
        "lines_added": 300,
        "lines_removed": 40,
    }
    row.update(over)
    return [row]


def _wire_runs(monkeypatch, rows):
    def script(q, p):
        return rows if "from public.runs r" in q else []

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


def _runs_query(conn):
    return [x for x in conn.queries if "from public.runs r" in x[0]][0]


def test_work_summary_counts_landed_items_distinctly(monkeypatch):
    """Four attempts on one story are one work item delivered. The DISTINCT is
    the whole difference between "items landed" and "runs that merged"."""
    conn = _wire_runs(monkeypatch, _summary_row())
    out = db.work_summary(object(), ORG, days=7)
    q, _ = _runs_query(conn)
    assert "count(distinct r.issue_id) filter (where r.merge_commit_sha is not null)" in q
    assert out["items_landed"] == 2
    assert out["bugs_landed"] == 1
    assert out["work_seconds"] == 7200


def test_work_summary_counts_only_terminal_runs(monkeypatch):
    """`work_seconds` is written once, on arrival at a terminal state
    (migration 252). Summing over a running run would add a null, and counting
    it would claim work that has not happened yet."""
    conn = _wire_runs(monkeypatch, _summary_row())
    db.work_summary(object(), ORG)
    q, _ = _runs_query(conn)
    assert "r.status in ('succeeded', 'failed', 'cancelled', 'abandoned', 'stopped')" in q


def test_work_summary_windows_on_when_the_run_ended(monkeypatch):
    """The seam named in us-102.2 AC4: a run is in the window it ENDED in, not
    the one its pull request merged in."""
    conn = _wire_runs(monkeypatch, _summary_row())
    db.work_summary(object(), ORG, days=30)
    q, _ = _runs_query(conn)
    assert (
        "coalesce(r.finished_at, r.last_heartbeat_at, r.claimed_at)"
        " > now() - interval '30 days'" in q
    )


def test_work_summary_joins_issues_even_unfiltered(monkeypatch):
    """`bugs_landed` needs i.type on every call, not only when the type filter
    is set — and the join stays LEFT so a run with no work item behind it still
    contributes its hours and its lines."""
    conn = _wire_runs(monkeypatch, _summary_row())
    db.work_summary(object(), ORG)
    q, _ = _runs_query(conn)
    assert "left join public.issues i on i.id = r.issue_id" in q
    assert " inner join" not in q


def test_work_summary_filters_are_parameterised(monkeypatch):
    P1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    W1 = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    conn = _wire_runs(monkeypatch, _summary_row())
    db.work_summary(object(), ORG, days=7, project_id=P1, worker_id=W1, item_type="bug")
    q, p = _runs_query(conn)
    assert "r.project_id = %s" in q
    assert "r.worker_id = %s" in q
    assert "i.type = %s" in q
    assert p == (ORG, P1, W1, "bug")


def test_work_summary_drops_junk_rather_than_interpolating_it(monkeypatch):
    conn = _wire_runs(monkeypatch, _summary_row())
    db.work_summary(
        object(), ORG, project_id="'; drop table runs; --", item_type="banana"
    )
    q, p = _runs_query(conn)
    assert "drop table" not in q
    assert "i.type = %s" not in q
    assert p == (ORG,)


def test_work_summary_window_is_clamped_like_the_spend_queries(monkeypatch):
    _wire_runs(monkeypatch, _summary_row())
    assert db.work_summary(object(), ORG, days=9999)["days"] == 366
    assert db.work_summary(object(), ORG, days="x")["days"] == 7


def test_work_summary_zero_is_a_number(monkeypatch):
    """A slice filtered down to nothing answers zeroes — nulls from an empty
    aggregate would render as blanks and read as a broken page."""
    _wire_runs(monkeypatch, [{
        "work_seconds": None, "runs": 0, "items_landed": 0,
        "bugs_landed": 0, "lines_added": None, "lines_removed": None,
    }])
    out = db.work_summary(object(), ORG)
    assert out["work_seconds"] == 0
    assert out["lines_added"] == 0
    assert out["items_landed"] == 0


def test_work_summary_refuses_without_the_capability(client, make_token, monkeypatch):
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(view_costs=False))
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/work-summary",
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 403
    assert "owners and admins" in resp.json()["detail"]


def test_work_summary_passes_the_same_filters_as_spend(client, make_token, monkeypatch):
    monkeypatch.setattr("app.routers.llm.rpc", _fake_rpc(view_costs=True))
    captured = {}

    def fake_summary(settings, org_id, **kw):
        captured["org_id"] = org_id
        captured.update(kw)
        return {"days": kw.get("days"), "work_seconds": 0, "runs": 0,
                "items_landed": 0, "bugs_landed": 0, "lines_added": 0,
                "lines_removed": 0}

    monkeypatch.setattr("app.db.work_summary", fake_summary)
    P1 = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    resp = client.get(
        f"/api/v1/llm/orgs/{ORG}/work-summary",
        params={"days": 7, "project_id": P1, "item_type": "bug"},
        headers={"Authorization": f"Bearer {make_token()}"},
    )
    assert resp.status_code == 200, resp.text
    assert captured["org_id"] == ORG
    assert captured["days"] == 7
    assert captured["project_id"] == P1
    assert captured["item_type"] == "bug"
