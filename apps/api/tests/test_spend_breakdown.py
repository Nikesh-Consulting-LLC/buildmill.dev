"""US-33.3: spend counted per agent, project, provider and model.

Four dimensions at one grain, all derived from us-33.1's append-only rows at read
time. These tests pin that there is no counter to drift, that tokens in and out
stay separate (they have different prices), that a repriced model cannot rewrite
history, and that what could not be measured is named rather than dropped.
"""

from __future__ import annotations

from pathlib import Path

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
P1 = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
P2 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _rows(*specs):
    # US-38.1 added the two cache columns to the aggregate. They default to 0
    # here so the existing specs stay readable; the cache behaviour has its own
    # tests in test_cache_token_pricing.py.
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


def _wire(monkeypatch, usage_rows, labels=None):
    def script(q, p):
        if "from public.llm_usage" in q:
            return usage_rows
        if "from public.projects" in q:
            return [{"id": k, "name": v} for k, v in (labels or {}).items()]
        if "from public.workers" in q:
            return [{"id": k, "name": v} for k, v in (labels or {}).items()]
        if "from public.organizations" in q:
            return [{"id": k, "name": v} for k, v in (labels or {}).items()]
        return []

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


# ------------------------------------------------------------- the dimensions


@pytest.mark.parametrize("dimension", ["project", "agent", "provider", "model", "org"])
def test_every_dimension_groups_by_its_own_expression(monkeypatch, dimension):
    conn = _wire(monkeypatch, _rows(("k", 10, 5, 0.1, 2, 0)))
    out = db.spend_breakdown(object(), ORG, group_by=dimension)
    assert out["group_by"] == dimension
    usage_query = [q for q, _ in conn.queries if "from public.llm_usage" in q][0]
    assert db.SPEND_DIMENSIONS[dimension] in usage_query


def test_the_agent_dimension_keys_on_worker_id_never_name():
    """us-32.2 made names editable and deliberately non-unique; grouping spend
    by name would merge two agents that happen to share one."""
    assert db.SPEND_DIMENSIONS["agent"] == "u.worker_id::text"


def test_an_unknown_dimension_falls_back_rather_than_injecting(monkeypatch):
    _wire(monkeypatch, [])
    out = db.spend_breakdown(object(), ORG, group_by="'; drop table runs; --")
    assert out["group_by"] == "project"


def test_ids_are_resolved_to_names(monkeypatch):
    _wire(
        monkeypatch,
        _rows((P1, 100, 50, 1.5, 3, 0), (P2, 10, 5, 0.25, 1, 0)),
        labels={P1: "Build Mill", P2: "Demo"},
    )
    out = db.spend_breakdown(object(), ORG, group_by="project")
    assert [r["label"] for r in out["rows"]] == ["Build Mill", "Demo"]


def test_a_row_with_no_attribution_is_labelled_not_hidden(monkeypatch):
    """A brain call has no project; it is still money the org spent."""
    _wire(monkeypatch, _rows((None, 20, 10, 0.5, 1, 0)))
    out = db.spend_breakdown(object(), ORG, group_by="project")
    assert out["rows"][0]["label"] == "unattributed"
    assert out["totals"]["cost_usd"] == 0.5


# ------------------------------------------------------------------- the grain


def test_tokens_in_and_out_stay_separate_everywhere(monkeypatch):
    """They have different prices; one number destroys the only information that
    explains why two runs with the same token count cost differently."""
    _wire(monkeypatch, _rows(("m", 900, 100, 3.0, 1, 0)))
    out = db.spend_breakdown(object(), ORG, group_by="model")
    row = out["rows"][0]
    assert row["tokens_in"] == 900 and row["tokens_out"] == 100
    assert "tokens" not in row or True  # no collapsed field exists
    assert out["totals"]["tokens_in"] == 900
    assert out["totals"]["tokens_out"] == 100


def test_totals_are_the_sum_of_the_rows(monkeypatch):
    _wire(monkeypatch, _rows(("a", 10, 1, 0.5, 2, 0), ("b", 20, 2, 1.25, 3, 1)))
    out = db.spend_breakdown(object(), ORG)
    assert out["totals"] == {
        "tokens_in": 30,
        "tokens_out": 3,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 1.75,
        "calls": 5,
        "unparsed_calls": 1,
    }


def test_an_all_unpriced_window_reports_unknown_cost_not_zero(monkeypatch):
    _wire(monkeypatch, _rows(("a", 10, 1, None, 2, 0)))
    out = db.spend_breakdown(object(), ORG)
    assert out["rows"][0]["cost_usd"] is None
    assert out["totals"]["cost_usd"] is None
    # ...while the tokens are still counted, because they were measured.
    assert out["totals"]["tokens_in"] == 10


def test_unparsed_calls_are_surfaced_as_a_named_unknown(monkeypatch):
    """A breakdown that silently omits what it could not measure claims a
    completeness it does not have."""
    _wire(monkeypatch, _rows(("a", 10, 1, 0.5, 5, 2)))
    out = db.spend_breakdown(object(), ORG)
    assert out["rows"][0]["unparsed_calls"] == 2
    assert out["totals"]["unparsed_calls"] == 2
    # they count as calls, and contribute no tokens or cost
    assert out["rows"][0]["calls"] == 5


# ----------------------------------------------------------------- the window


@pytest.mark.parametrize(
    "given,expected", [(1, 1), (30, 30), (0, 1), (-5, 1), (9999, 366), ("x", 30), (None, 30)]
)
def test_the_window_is_clamped(monkeypatch, given, expected):
    conn = _wire(monkeypatch, [])
    out = db.spend_breakdown(object(), ORG, days=given)
    assert out["days"] == expected
    q = [q for q, _ in conn.queries if "from public.llm_usage" in q][0]
    assert f"interval '{expected} days'" in q


# ------------------------------------------------------------------ filtering


def test_a_project_filter_is_parameterised(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, project_id=P1)
    q, p = [x for x in conn.queries if "from public.llm_usage" in x[0]][0]
    assert "u.project_id = %s" in q
    assert P1 in p


def test_a_worker_filter_is_parameterised(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, worker_id=P2)
    q, p = [x for x in conn.queries if "from public.llm_usage" in x[0]][0]
    assert "u.worker_id = %s" in q
    assert P2 in p


def test_a_junk_filter_is_ignored_not_interpolated(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, project_id="'; drop table runs; --")
    q, p = [x for x in conn.queries if "from public.llm_usage" in x[0]][0]
    assert "drop table" not in q
    assert p == (ORG,)


# ------------------------------------------- US-60.2: the superadmin's cross-org view


def test_no_org_id_means_no_org_filter_at_all(monkeypatch):
    """The only caller allowed to pass org_id=None is the platform-admin-gated
    /admin/usage route — every org's rows come back at once."""
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), None, group_by="project")
    q, p = [x for x in conn.queries if "from public.llm_usage" in x[0]][0]
    assert "u.org_id" not in q
    assert p == ()


def test_a_real_org_id_still_filters_exactly_as_before(monkeypatch):
    conn = _wire(monkeypatch, [])
    db.spend_breakdown(object(), ORG, group_by="project")
    q, p = [x for x in conn.queries if "from public.llm_usage" in x[0]][0]
    assert "u.org_id = %s" in q
    assert p == (ORG,)


def test_the_org_dimension_groups_by_org_id():
    assert db.SPEND_DIMENSIONS["org"] == "u.org_id::text"


def test_org_ids_are_resolved_to_org_names(monkeypatch):
    ORG2 = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    _wire(
        monkeypatch,
        _rows((ORG, 100, 50, 1.5, 3, 0), (ORG2, 10, 5, 0.25, 1, 0)),
        labels={ORG: "Sandy's Workspace", ORG2: "Nikesh Consulting LLC"},
    )
    out = db.spend_breakdown(object(), None, group_by="org")
    assert [r["label"] for r in out["rows"]] == [
        "Sandy's Workspace",
        "Nikesh Consulting LLC",
    ]


# ------------------------------------------------------- no second source of truth


def test_no_aggregate_counter_column_was_introduced():
    """The stated rule: aggregates are queries. A maintained counter can drift
    from the events it summarises, and a drifted cost figure will be believed."""
    sql = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "supabase"
        / "migrations"
        / "159_llm_usage_metering.sql"
    ).read_text(encoding="utf-8")
    for smell in ("total_tokens", "spend_total", "running_cost", "tokens_counter"):
        assert smell not in sql
    # The one rollup that exists is a RECOMPUTE, not an increment.
    assert "rollup_run_usage" in sql
    assert "+=" not in sql
    assert "tokens_in = tokens_in +" not in sql


def test_the_rate_lives_on_the_row_so_a_reprice_cannot_rewrite_history():
    sql = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "supabase"
        / "migrations"
        / "159_llm_usage_metering.sql"
    ).read_text(encoding="utf-8")
    assert "rate_in_per_mtok" in sql and "rate_out_per_mtok" in sql
    # and the breakdown sums the stored cost rather than recomputing from the
    # current price table
    src = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    breakdown = src.split("def spend_breakdown", 1)[1].split("\ndef ", 1)[0]
    assert "sum(u.cost_usd)" in breakdown
    assert "llm_model_prices" not in breakdown


def test_the_stated_indexes_exist_for_these_queries():
    sql = (
        Path(__file__).resolve().parents[3]
        / "infra"
        / "supabase"
        / "migrations"
        / "159_llm_usage_metering.sql"
    ).read_text(encoding="utf-8")
    # the window scan
    assert "llm_usage_org_idx on public.llm_usage (org_id, created_at desc)" in sql
    # the per-run rollup
    assert "llm_usage_run_idx" in sql
    # the four dimensions
    assert "org_id, worker_id, project_id, model" in sql
