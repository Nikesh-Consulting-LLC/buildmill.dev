"""The console asked PostgREST for a column that does not exist (2026-08-11).

`_replay` selected and ordered by `run_trace.created_at`. That table's column is
`at` (migration 121). Every attach 500'd before a single line was shown, and the
console had never worked in production — while every test passed, because they
all faked `postgrest_get` and a fake does not know the schema.

So this checks the column names against the generated types, the same way
`test_embed_ambiguity` checks the relationship graph: derived from the schema
rather than from a list someone has to remember to update.
"""

import re
from pathlib import Path

import embed_graph as eg
import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "app" / "routers" / "run_console.py"

# `postgrest_get(settings, token, "<table>", { ... })` — the table, then the
# params block it is called with.
CALL = re.compile(
    r'postgrest_get\(\s*settings,\s*[\w.]+,\s*"(?P<table>\w+)",\s*\{(?P<params>.*?)\}\s*,?\s*\)',
    re.S,
)


def _columns_of(types_src: str, table: str) -> set[str]:
    """The Row keys the generated types declare for one table."""
    block = re.search(
        rf'\b{table}:\s*\{{\s*Row:\s*\{{(?P<row>.*?)\n\s*\}}', types_src, re.S
    )
    if not block:
        return set()
    return set(re.findall(r"^\s*(\w+):", block.group("row"), re.M))


@pytest.fixture(scope="module")
def types_src():
    return eg.GENERATED_TYPES.read_text(encoding="utf-8")


def _asked_for(params: str) -> set[str]:
    """Column names this call names — in `select`, in `order`, and as filters."""
    wanted: set[str] = set()
    select = re.search(r'"select":\s*"([^"]+)"', params)
    if select:
        wanted |= {c.strip() for c in select.group(1).split(",") if c.strip()}
    order = re.search(r'"order":\s*"([^"]+)"', params)
    if order:
        wanted.add(order.group(1).split(".")[0])
    # `"run_id": f"eq.{...}"` — the key is the column
    for key in re.findall(r'"(\w+)":\s*f?"eq\.', params):
        wanted.add(key)
    return wanted


def test_every_console_query_names_columns_that_exist(types_src):
    calls = list(CALL.finditer(CONSOLE.read_text(encoding="utf-8")))
    assert calls, "the scan found no queries — it would pass on nothing"
    problems = []
    for call in calls:
        table = call.group("table")
        actual = _columns_of(types_src, table)
        assert actual, f"{table} is not in the generated types"
        for column in _asked_for(call.group("params")):
            if column not in actual:
                problems.append(f"{table}.{column}")
    assert not problems, (
        "the console asks PostgREST for columns that do not exist — every "
        "attach 500s, and a faked postgrest_get will not notice: "
        + ", ".join(sorted(problems))
    )


def test_the_column_that_broke_it_is_the_one_the_scan_would_catch(types_src):
    """Guards the guard: if `_columns_of` ever stopped parsing, the check above
    would pass against an empty set and prove nothing. `run_trace` has `at` and
    has never had `created_at`, which is the mistake that shipped."""
    columns = _columns_of(types_src, "run_trace")
    assert "at" in columns
    assert "created_at" not in columns
    assert {"kind", "content", "run_id"} <= columns
