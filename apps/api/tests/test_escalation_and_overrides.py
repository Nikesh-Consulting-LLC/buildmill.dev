"""US-33.4/33.5/33.6: escalation, the dispatch override, and the comparison.

All three ride the precedence us-32.7 already built — which is the point. The
supervisor's escalation and the manager's dispatch choice are the two override
layers the resolver was given, so both are visible and explainable by
construction rather than being hidden behaviours of their own code paths.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import db, run_settings


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


ORG = "11111111-1111-1111-1111-111111111111"
ISSUE = "22222222-2222-2222-2222-222222222222"
RUN = "33333333-3333-3333-3333-333333333333"
BALANCED = "44444444-4444-4444-4444-444444444444"
DEEP = "55555555-5555-5555-5555-555555555555"


def _last_run(**over):
    row = {
        "id": RUN,
        "status": "failed",
        "fault_class": "work-fault",
        "preset_id": BALANCED,
        "preset_name": "Balanced",
    }
    row.update(over)
    return row


DEEP_ROW = {
    "id": DEEP,
    "name": "Deep",
    "model": "claude-opus-5",
    "settings": {"effort": "high", "max_budget_usd": 15},
    "version": 2,
}


def _wire(monkeypatch, last=None, nxt=None):
    def script(q, p):
        # The raw query is multi-line, so match on single-line fragments.
        if "escalates_to" in q:
            return FakeCursor(nxt)
        if "r.issue_id" in q:
            return FakeCursor(last)
        return FakeCursor(None)

    conn = FakeConn(script)
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    return conn


# ------------------------------------------------------- US-33.4: escalation


def test_a_work_fault_escalates_to_the_next_preset_up(monkeypatch):
    _wire(monkeypatch, last=_last_run(), nxt=DEEP_ROW)
    override, reason = db.escalation_for(object(), ORG, ISSUE, "code")
    assert override == {
        "effort": "high",
        "max_budget_usd": 15,
        "model": "claude-opus-5",
    }
    assert "Balanced" in reason and "Deep" in reason
    # US-33.2: the ceiling legitimately rises with the escalation, and says so.
    assert "v2" in reason


def test_a_runner_fault_escalates_nothing(monkeypatch):
    """Retrying a broken box at higher effort is superstition, and expensive
    superstition."""
    _wire(monkeypatch, last=_last_run(fault_class="runner-fault"), nxt=DEEP_ROW)
    assert db.escalation_for(object(), ORG, ISSUE, "code") == (None, None)


def test_an_unclassified_failure_escalates_nothing(monkeypatch):
    _wire(monkeypatch, last=_last_run(fault_class=None), nxt=DEEP_ROW)
    assert db.escalation_for(object(), ORG, ISSUE, "code") == (None, None)


def test_the_ladder_ends(monkeypatch):
    """Without a declared next step there is no escalation — so no run climbs
    forever."""
    _wire(monkeypatch, last=_last_run(), nxt=None)
    assert db.escalation_for(object(), ORG, ISSUE, "code") == (None, None)


def test_a_first_attempt_escalates_nothing(monkeypatch):
    _wire(monkeypatch, last=None, nxt=DEEP_ROW)
    assert db.escalation_for(object(), ORG, ISSUE, "code") == (None, None)


def test_a_previous_attempt_with_no_preset_escalates_nothing(monkeypatch):
    """A hand-tuned Custom row is not a rung on any ladder."""
    _wire(monkeypatch, last=_last_run(preset_id=None), nxt=DEEP_ROW)
    assert db.escalation_for(object(), ORG, ISSUE, "code") == (None, None)


def test_a_stopped_run_is_also_grounds_to_escalate(monkeypatch):
    """It ran out of budget rather than failing at the work; the next rung has a
    bigger one, which is exactly the right answer."""
    conn = _wire(monkeypatch, last=_last_run(status="stopped"), nxt=DEEP_ROW)
    override, reason = db.escalation_for(object(), ORG, ISSUE, "code")
    assert override is not None
    q = [q for q, _ in conn.queries if "r.issue_id" in q][0]
    assert "r.status in ('failed', 'stopped')" in q


def test_escalation_looks_only_at_the_same_run_kind(monkeypatch):
    conn = _wire(monkeypatch, last=_last_run(), nxt=DEEP_ROW)
    db.escalation_for(object(), ORG, ISSUE, "plan")
    q, p = [x for x in conn.queries if "r.issue_id" in x[0]][0]
    assert "r.kind = %s" in q
    assert p == (ISSUE, "plan")


def test_a_junk_issue_id_escalates_nothing_without_a_query(monkeypatch):
    monkeypatch.setattr(db, "_connect", lambda s: pytest.fail("no query expected"))
    assert db.escalation_for(object(), ORG, None, "code") == (None, None)
    assert db.escalation_for(object(), ORG, "../etc", "code") == (None, None)


def test_escalation_lands_in_the_supervisor_layer_not_the_manager_layer():
    """The whole design: it uses the slot it was given, so precedence still puts
    a manager's explicit choice above it."""
    out = run_settings.resolve(
        kind="code",
        run_routes={},
        presets_by_id={},
        org_default={
            "id": BALANCED,
            "name": "Balanced",
            "version": 1,
            "model": "claude-sonnet-5",
            "settings": {"effort": "medium"},
        },
        supervisor_override={"effort": "high"},
        manager_override={"effort": "low"},
    )
    assert out.values["effort"] == "low"
    assert out.sources["effort"] == run_settings.MANAGER


def test_escalation_does_not_buy_extra_attempts():
    """us-31.5's ceilings are unchanged: escalation changes HOW the remaining
    attempts are spent, never how many there are — otherwise the fix for the
    infinite loop reopens it."""
    src = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    block = src.split("def escalation_for", 1)[1].split("\ndef ", 1)[0]
    for forbidden in (
        "release_attempt_block",
        "attempts_blocked_at",
        "delete from public.run_attempts",
        "max_item_attempts",
    ):
        assert forbidden not in block, (
            f"escalation touches {forbidden!r}; it must not change the attempt budget"
        )


def test_an_explicit_supervisor_override_wins_over_the_ladder():
    src = (
        Path(__file__).resolve().parents[1] / "app" / "routers" / "worker.py"
    ).read_text(encoding="utf-8")
    assert "if not supervisor:" in src
    assert "db.escalation_for(" in src


def test_the_escalation_reason_reaches_the_run_trace():
    src = (
        Path(__file__).resolve().parents[1] / "app" / "routers" / "worker.py"
    ).read_text(encoding="utf-8")
    assert "escalation_reason" in src
    assert 'db.record_run_trace(' in src


# ------------------------------------------- US-33.5: the manager at dispatch


def test_the_dispatch_choice_copies_the_values_not_a_reference(monkeypatch):
    """Editing the preset later must not silently change what the manager asked
    for on this run."""
    captured = {}

    def script(q, p):
        if "from public.agent_presets p" in q and "join public.runs r" in q:
            return FakeCursor(DEEP_ROW)
        if "update public.runs" in q:
            captured["q"] = q
            captured["p"] = p
            return FakeCursor({"id": RUN})
        return FakeCursor(None)

    monkeypatch.setattr(db, "_connect", lambda s: FakeConn(script))
    assert db.set_manager_settings_override(object(), RUN, DEEP) is True
    values = json.loads(captured["p"][0])
    assert values == {"effort": "high", "max_budget_usd": 15, "model": "claude-opus-5"}
    # ...and the preset it came from is named, for the run detail to show
    meta = json.loads(captured["p"][1])
    assert meta == {"id": DEEP, "name": "Deep", "version": 2}


def test_the_override_only_applies_to_a_still_queued_run(monkeypatch):
    captured = {}

    def script(q, p):
        if "from public.agent_presets p" in q:
            return FakeCursor(DEEP_ROW)
        captured["q"] = q
        return FakeCursor(None)

    monkeypatch.setattr(db, "_connect", lambda s: FakeConn(script))
    db.set_manager_settings_override(object(), RUN, DEEP)
    assert "status = 'queued'" in captured["q"]


def test_a_preset_from_another_org_is_refused(monkeypatch):
    """The join is on the RUN's org, so a preset id from elsewhere finds nothing."""
    conn_queries = []

    def script(q, p):
        conn_queries.append(q)
        return FakeCursor(None)  # the org-joined lookup returns nothing

    monkeypatch.setattr(db, "_connect", lambda s: FakeConn(script))
    assert db.set_manager_settings_override(object(), RUN, DEEP) is False
    assert "r.org_id = p.org_id" in conn_queries[0]


def test_junk_ids_are_refused_without_a_query(monkeypatch):
    monkeypatch.setattr(db, "_connect", lambda s: pytest.fail("no query expected"))
    assert db.set_manager_settings_override(object(), "x", DEEP) is False
    assert db.set_manager_settings_override(object(), RUN, "y") is False


def test_the_resolver_already_reads_where_dispatch_writes():
    """No new plumbing — that is the story's whole claim."""
    src = (
        Path(__file__).resolve().parents[1] / "app" / "routers" / "worker.py"
    ).read_text(encoding="utf-8")
    assert 'get("settings_override")' in src
    dbsrc = (Path(__file__).resolve().parents[1] / "app" / "db.py").read_text(
        encoding="utf-8"
    )
    assert "'settings_override'" in dbsrc
    assert "'manager'" in dbsrc


# --------------------------------------------- US-33.6: compared on outcome


def test_outcomes_group_by_name_and_version(monkeypatch):
    rows = [
        {
            "name": "Deep",
            "version": 1,
            "runs": 10,
            "succeeded": 8,
            "failed": 1,
            "stopped": 1,
            "cost_usd": 12.5,
            "avg_cost_usd": 1.25,
            "avg_seconds": 630.4,
        },
        {
            "name": "Deep",
            "version": 2,
            "runs": 4,
            "succeeded": 1,
            "failed": 3,
            "stopped": 0,
            "cost_usd": 6.0,
            "avg_cost_usd": 1.5,
            "avg_seconds": None,
        },
    ]
    conn = FakeConn(lambda q, p: FakeCursor(rows=rows))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    out = db.preset_outcomes(object(), ORG)
    assert [(o["name"], o["version"]) for o in out] == [("Deep", 1), ("Deep", 2)]
    # "Deep got worse last week" is answerable because the versions are separate.
    assert out[0]["success_rate"] == 0.8
    assert out[1]["success_rate"] == 0.25
    assert out[0]["avg_seconds"] == 630
    assert out[1]["avg_seconds"] is None
    query = conn.queries[0][0]
    assert "group by 1, 2" in query


def test_unpreset_runs_are_excluded_not_lumped_together(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor(rows=[]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.preset_outcomes(object(), ORG)
    q = conn.queries[0][0]
    assert "r.preset_name is not null" in q


def test_only_finished_runs_are_counted(monkeypatch):
    conn = FakeConn(lambda q, p: FakeCursor(rows=[]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.preset_outcomes(object(), ORG)
    q = conn.queries[0][0]
    assert "r.status in ('succeeded', 'failed', 'stopped')" in q
    # a queued or running row would drag every average toward nothing


@pytest.mark.parametrize("given,expected", [(90, 90), (0, 1), (9999, 366), ("x", 90)])
def test_the_outcome_window_is_clamped(monkeypatch, given, expected):
    conn = FakeConn(lambda q, p: FakeCursor(rows=[]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.preset_outcomes(object(), ORG, given)
    assert f"interval '{expected} days'" in conn.queries[0][0]


def test_a_stopped_run_is_its_own_column_not_a_failure(monkeypatch):
    """us-33.2's distinction has to survive into the comparison, or `Fast` looks
    unreliable when it was merely cheap."""
    conn = FakeConn(lambda q, p: FakeCursor(rows=[]))
    monkeypatch.setattr(db, "_connect", lambda s: conn)
    db.preset_outcomes(object(), ORG)
    q = conn.queries[0][0]
    assert "filter (where r.status = 'stopped')" in q
    assert "filter (where r.status = 'failed')" in q
