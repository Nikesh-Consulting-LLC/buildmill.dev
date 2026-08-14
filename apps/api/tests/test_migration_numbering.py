"""A migration number is an order, so two files may not share one.

Migrations apply in numeric order (CLAUDE.md). Two files numbered the same
make that order ambiguous, and a fresh replay of the tree non-deterministic —
which is exactly what happened on 2026-08-13, when phase 87's
`249_org_pending_count.sql` and phase 89's `249_project_env.sql` were built on
parallel branches and both merged. Neither branch could see the other's number.

This is the check that branch pair did not have. It costs nothing and runs in
Essential, so the next collision fails here instead of on somebody's replay.
"""

from pathlib import Path

import pytest

MIGRATIONS = Path(__file__).resolve().parents[3] / "infra" / "supabase" / "migrations"

# Collisions that predate the check. They are left alone deliberately: all are
# long since applied to both projects, so their relative order is settled fact
# and renumbering them would only orphan the ledger rows that name them. New
# numbers do not get added to this list — they get a free number.
GRANDFATHERED = {"014", "015", "205"}


def numbers() -> dict[str, list[str]]:
    by_number: dict[str, list[str]] = {}
    for path in MIGRATIONS.glob("*.sql"):
        by_number.setdefault(path.name.split("_")[0], []).append(path.name)
    return by_number


def test_migrations_directory_is_found():
    # A wrong path would make every assertion below vacuously true.
    assert MIGRATIONS.is_dir(), f"migrations not at {MIGRATIONS}"
    assert len(list(MIGRATIONS.glob("*.sql"))) > 200


def test_no_duplicate_migration_numbers():
    dupes = {
        number: sorted(files)
        for number, files in numbers().items()
        if len(files) > 1 and number not in GRANDFATHERED
    }
    assert not dupes, (
        "two migrations share a number, so their apply order is ambiguous — "
        "renumber the later one to the next free number: "
        + "; ".join(f"{n}: {', '.join(f)}" for n, f in sorted(dupes.items()))
    )


@pytest.mark.parametrize("number", sorted(GRANDFATHERED))
def test_grandfathered_collisions_still_exist(number):
    """If one of these is ever cleaned up, drop it from the set rather than
    leaving a stale exemption that would hide a fresh collision on that
    number."""
    assert len(numbers().get(number, [])) > 1, (
        f"{number} is no longer a collision — remove it from GRANDFATHERED"
    )


def test_every_migration_is_numbered():
    unnumbered = sorted(
        p.name for p in MIGRATIONS.glob("*.sql") if not p.name[:3].isdigit()
    )
    assert not unnumbered, f"migrations without a numeric prefix: {unnumbered}"
