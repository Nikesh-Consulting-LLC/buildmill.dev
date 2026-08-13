"""US-31.2 / US-33.2: a preset's time ceiling narrows the lease-derived limit.

Split out of `test_ceiling_stop.py` by US-37.2, which removed the per-run SPEND
ceiling entirely. Time and money were only ever neighbours in one file: the
spend ceiling is gone, `max_minutes` is not, and it still owns the invariant
that a run's work limit stays strictly below its own claim lease.
"""

from __future__ import annotations

from supervisor.workloop import timeout_from_lease


# ------------------------------------------ the preset time ceiling (narrowing)


def test_a_preset_ceiling_narrows_the_lease_derived_limit():
    # us-31.2's rule: 90% of the lease, or lease − 60s headroom, whichever is
    # tighter. For an hour that is 3240s.
    assert timeout_from_lease(3600) == 3240
    # A 10-minute preset ceiling brings it down.
    assert timeout_from_lease(3600, 10) == 600


def test_a_preset_ceiling_can_never_widen_past_the_lease():
    """us-31.2 owns the invariant that the work limit stays strictly below the
    claim lease; a preset that could raise it would produce a run allowed to
    outlive its own claim."""
    lease = 900  # 15 minutes
    for greedy in (30, 60, 1440):
        derived = timeout_from_lease(lease, greedy)
        assert derived <= lease - 60
        assert derived < lease


def test_an_unset_ceiling_changes_nothing():
    for value in (None, 0, "", "not a number"):
        assert timeout_from_lease(3600, value) == 3240


def test_a_ceiling_with_no_lease_still_applies():
    """An older server sends no lease; a preset ceiling is then the only limit
    there is, and it is better than none."""
    assert timeout_from_lease(None, 10) == 600
    assert timeout_from_lease(None) is None


def test_the_floor_keeps_a_tiny_ceiling_usable():
    # 0 means unset, not "no time at all" — a preset that zeroed the allowance
    # would be a preset that cannot run anything.
    assert timeout_from_lease(3600, 0) == 3240
    # And a ceiling below the floor is raised to it rather than being unusable.
    assert timeout_from_lease(3600, 1) == 60
