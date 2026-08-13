"""US-38.1: a cache read is not priced as fresh input.

The failure being prevented: `metering.py` folded `cache_creation_input_tokens`
and `cache_read_input_tokens` into `tokens_in` and multiplied the total by one
rate. Anthropic bills a cache read at 0.1x the input rate and a write at 1.25x,
so a cached token was charged nine to twelve times what it cost — on a workload
running 67 input tokens per output token, that is most of the bill.

Two properties matter more than the arithmetic and are asserted first:

  * `tokens_in` still means ALL input tokens. Every aggregate in the app reads
    it, including us-37.1's project budget, which now stops work.
  * A row that predates the split costs exactly what it costs today. History
    does not get cheaper retroactively on the strength of an assumption.
"""

from __future__ import annotations

import json

from app import metering


def _meter(usage: dict, provider_type: str = "anthropic") -> dict:
    m = metering.UsageMeter(provider_type)
    m.feed(json.dumps({"usage": usage}).encode())
    m.finish()
    return m.as_row()


# --------------------------------------------------------------- the split


def test_anthropic_cache_fields_are_recorded_apart_and_still_counted_in():
    row = _meter(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 1_000,
            "cache_read_input_tokens": 9_000,
        }
    )
    # tokens_in is the TOTAL, unchanged in meaning.
    assert row["tokens_in"] == 10_100
    assert row["cache_read_tokens"] == 9_000
    assert row["cache_write_tokens"] == 1_000
    assert row["tokens_out"] == 50


def test_openai_shaped_reports_reads_only_and_is_not_double_counted():
    """Their `prompt_tokens` ALREADY includes the cached ones — adding them
    again would inflate the input side of every OpenAI-shaped call."""
    row = _meter(
        {
            "prompt_tokens": 8_000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 6_000},
        },
        provider_type="openai",
    )
    assert row["tokens_in"] == 8_000  # not 14_000
    assert row["cache_read_tokens"] == 6_000
    # No write count exists in this shape. NULL is the true answer; 0 would
    # claim the provider said "nothing was written", which it did not.
    assert row["cache_write_tokens"] is None


def test_a_provider_silent_about_caching_records_null_not_zero():
    row = _meter({"input_tokens": 500, "output_tokens": 10})
    assert row["tokens_in"] == 500
    assert row["cache_read_tokens"] is None
    assert row["cache_write_tokens"] is None


def test_the_cache_classes_never_exceed_the_total_they_are_subsets_of():
    """Migration 165 enforces this as a check constraint. The meter clamps too,
    because a constraint violation would lose the whole usage row, and losing a
    usage row is worse than clamping one."""
    for usage in (
        {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 5},
        {
            "prompt_tokens": 100,
            "completion_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 999_999},
        },
    ):
        row = _meter(usage)
        total = (row["cache_read_tokens"] or 0) + (row["cache_write_tokens"] or 0)
        assert total <= row["tokens_in"], usage


# ---------------------------------------------------------------- pricing


def test_a_pre_split_row_costs_exactly_what_it_costs_today():
    """NULL cache columns mean "this row predates the split", so the whole of
    tokens_in is priced as fresh — the old formula, to the cent."""
    old = (1_000_000 / 1_000_000) * 3.0 + (10_000 / 1_000_000) * 15.0
    assert metering.cost_for(1_000_000, 10_000, 3.0, 15.0) == round(old, 6)
    assert (
        metering.cost_for(1_000_000, 10_000, 3.0, 15.0, None, None, None, None)
        == round(old, 6)
    )


def test_an_unset_cache_rate_charges_the_full_input_rate():
    """us-33.1's rule: unknown cost must never read as free. Guessing 0.1x
    against a provider that does not price that way is an underestimate the
    manager cannot see."""
    with_cache = metering.cost_for(
        1_000_000, 0, 3.0, 15.0, cache_read=900_000, cache_write=50_000
    )
    without = metering.cost_for(1_000_000, 0, 3.0, 15.0)
    assert with_cache == without


def test_cache_rates_price_the_three_classes_apart():
    # 1M input: 900k read @ $0.30, 50k write @ $3.75, 50k fresh @ $3.00.
    cost = metering.cost_for(
        1_000_000,
        0,
        3.0,
        15.0,
        cache_read=900_000,
        cache_write=50_000,
        rate_cache_read=0.30,
        rate_cache_write=3.75,
    )
    expected = (0.9 * 0.30) + (0.05 * 3.75) + (0.05 * 3.00)
    assert cost == round(expected, 6)
    # And it is far cheaper than charging the lot as fresh — which is the whole
    # point, and the size of the error being corrected.
    assert cost < metering.cost_for(1_000_000, 0, 3.0, 15.0) / 3


def test_fresh_input_is_what_is_left_and_never_goes_negative():
    cost = metering.cost_for(
        100, 0, 3.0, 15.0, cache_read=90, cache_write=90, rate_cache_read=0.0,
        rate_cache_write=0.0,
    )
    # Both subsets over-report; fresh clamps at 0 rather than crediting money
    # back. Cost cannot be negative.
    assert cost == 0.0


def test_no_rate_at_all_is_still_none_not_zero():
    """The distinction us-33.1 exists to preserve, unchanged by the split."""
    assert metering.cost_for(1_000, 10, None, None, cache_read=500) is None
