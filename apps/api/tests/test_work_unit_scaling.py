"""US-39.2: a batch run gets an allowance for the work it carries.

The failure this comes from, observed 2026-07-27 on a real feature build:

    the claude CLI exited 1 after 851s. Its last output:
    Error: Reached max turns (40)

851 seconds is well inside any time limit — the run died out of TURNS, having
done a fraction of eight stories' work on a budget sized for one. Turns are the
tighter of the two limits, which is why they bit first; time would have been
next.

The property that keeps this safe is asserted first and hardest: **a one-unit
run must be unchanged**. Everything here multiplies by `run_work_units()`, and
that returns 1 for every run except a feature/epic batch code run — so a
project that never batches cannot notice this story.
"""

from __future__ import annotations

import pathlib
import re

from app import db

SRC = pathlib.Path(db.__file__).read_text(encoding="utf-8")
WORKER_SRC = (
    pathlib.Path(db.__file__).parent / "routers" / "worker.py"
).read_text(encoding="utf-8")
MIGRATION = (
    pathlib.Path(db.__file__).resolve().parents[3]
    / "infra"
    / "supabase"
    / "migrations"
    / "167_run_work_units.sql"
).read_text(encoding="utf-8")


# ------------------------------------------------------------ the default


def test_the_autonomous_default_is_two_hours():
    """15 minutes was the default and it was far too short: timeout_from_lease
    turns it into ~13.5 minutes of CLI time, so an agent with nothing configured
    was cut off before it could finish anything substantial."""
    assert db._LEASE_SECONDS["autonomous"] == 7200
    assert db._LEASES["autonomous"] == "120 minutes"
    # The human lease is a different thing entirely and is untouched.
    assert db._LEASE_SECONDS["human"] == 86400


# -------------------------------------------------------------- the count


def test_only_a_feature_batch_code_run_carries_more_than_one_unit():
    """Every guard in the function matters; losing any one of them would start
    scaling runs that carry a single story."""
    for guard in (
        "r.kind = 'code'",
        "f.type in ('feature', 'epic')",
        "coalesce(p.build_mode, 'story') in ('feature', 'epic')",
        "c.abandoned_at is null",
    ):
        assert guard in MIGRATION, guard
    # Never zero: a run with no countable children is one unit, not none.
    assert "greatest(1," in MIGRATION


def test_an_uncountable_batch_is_one_unit_never_zero(monkeypatch):
    """If the count itself fails, the run must get a normal allowance rather
    than an allowance of nothing."""

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "_connect", boom)
    assert db.run_work_units(object(), "11111111-1111-1111-1111-111111111111") == 1
    # A malformed id is also one, not an error.
    assert db.run_work_units(object(), "not-a-uuid") == 1


# --------------------------------------------------------------- the time


def test_the_claim_scales_its_lease_and_bounds_the_product():
    claim = SRC[SRC.index("def claim_run(") : SRC.index("def claim_run(") + 4000]
    # Per-story x the work it carries...
    assert "public.run_work_units(runs.id)" in claim
    # ...bounded, or a forty-story feature could hold the box for a day.
    assert "least(" in claim
    assert "max_total_run_minutes" in claim


def test_the_ceiling_column_exists_and_is_bounded():
    assert "max_total_run_minutes" in MIGRATION
    assert "between 1 and 1440" in MIGRATION
    assert db.DEFAULT_TOTAL_RUN_MINUTES == 1440


def test_the_bundle_lease_comes_from_the_claim_not_a_second_calculation():
    """The dangerous half. `claim_run` writes claim_expires_at in SQL and
    worker_lease_seconds fed the bundle from Python; they agreed by coincidence.
    Once the claim scales, a parallel calculation would promise the runner time
    the claim will not honour — the claim would expire mid-run, requeue the
    work, and the failure would look like the agent dying."""
    fn = SRC[
        SRC.index("def worker_lease_seconds(") : SRC.index("def run_work_units(")
    ]
    assert "claim_expires_at" in fn
    assert "run_id" in fn
    # And the caller passes the run.
    bundle = WORKER_SRC[WORKER_SRC.index('"lease_seconds"') :][:400]
    assert "run.get(\"id\")" in bundle


# -------------------------------------------------------------- the turns


def test_turns_scale_with_the_work_and_are_bounded():
    assert "db.run_work_units(settings, str(run[\"id\"]))" in WORKER_SRC
    assert "turns * units" in WORKER_SRC
    assert "db.MAX_SCALED_TURNS" in WORKER_SRC
    assert db.MAX_SCALED_TURNS == 2000


def test_turns_scale_only_when_there_is_more_than_one_unit():
    """The one-unit guarantee, in the code rather than only in the story."""
    block = WORKER_SRC[WORKER_SRC.index("units = db.run_work_units") :][:900]
    assert "if units > 1:" in block


def test_the_scaled_turn_count_is_explained_on_the_trace():
    """A run whose limits differ from its preset's must not leave the manager
    comparing two numbers by hand."""
    block = WORKER_SRC[WORKER_SRC.index("units = db.run_work_units") :][:1400]
    assert "record_run_trace" in block
    assert "carries" in block and "stories" in block


def test_scaling_happens_after_preset_validation_not_by_widening_it():
    """max_turns is validated 1..500 as a PRESET value — what a human may type.
    An eight-story run resolving to 320 is a different question, and widening
    the preset range to allow it would have been the wrong fix."""
    from app import presets

    assert "max_turns" in presets.PRESET_SETTINGS
    with __import__("pytest").raises(presets.PresetInvalid):
        presets.clean_settings({"max_turns": 501})
    # The scaled value is computed after resolution, in the claim path.
    # (us-116.1: the claim resolves through model_resolution's helper now —
    # the one place the resolver's arguments are built.)
    assert WORKER_SRC.index("units = db.run_work_units") > WORKER_SRC.index(
        "resolved = model_resolution.resolve_for_kind("
    )
