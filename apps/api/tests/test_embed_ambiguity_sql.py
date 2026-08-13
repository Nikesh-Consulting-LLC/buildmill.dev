"""BUG-1.1: the same embed guard, against the real relationship graph.

`test_embed_ambiguity` reads the graph out of the checked-in generated types,
which is only as current as the last regeneration. This reads it out of
`pg_catalog`, so it also catches the case the plan flagged as the cost of
naming a constraint in code: a later migration recreating that foreign key
under a different name breaks every hinted embed at runtime, with nothing
failing at build time.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable. Read-only —
the connection is rolled back and closed either way.
"""

from __future__ import annotations

import os
from pathlib import Path

import embed_graph as eg
import psycopg
import pytest
from psycopg.rows import dict_row

FK_GRAPH_SQL = """
select con.conname                                   as name,
       cl.relname                                    as table_name,
       ref.relname                                   as ref_table,
       array_agg(att.attname order by u.ord)         as columns,
       bool_and(att.attnotnull)                      as not_null
  from pg_constraint con
  join pg_class cl        on cl.oid = con.conrelid
  join pg_namespace n     on n.oid = cl.relnamespace
  join pg_class ref       on ref.oid = con.confrelid
  join pg_namespace refn  on refn.oid = ref.relnamespace
  cross join lateral unnest(con.conkey) with ordinality as u(attnum, ord)
  join pg_attribute att   on att.attrelid = cl.oid and att.attnum = u.attnum
 where con.contype = 'f'
   and n.nspname = 'public'
   and refn.nspname = 'public'
 group by con.conname, cl.relname, ref.relname
"""


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="module")
def live_fks() -> list[eg.ForeignKey]:
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        rows = conn.execute(FK_GRAPH_SQL).fetchall()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    try:
        return [
            eg.ForeignKey(
                table=r["table_name"],
                name=r["name"],
                columns=tuple(r["columns"]),
                ref_table=r["ref_table"],
                not_null=bool(r["not_null"]),
            )
            for r in rows
        ]
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture(scope="module")
def embeds(live_fks):
    tables = {fk.table for fk in live_fks} | {fk.ref_table for fk in live_fks}
    return eg.find_embeds(tables)


def test_no_embed_is_ambiguous_against_the_live_schema(live_fks, embeds):
    assert embeds, "found no embeds to check"
    assert eg.violations(live_fks, embeds) == []


def test_every_hint_in_the_code_names_a_constraint_that_exists(live_fks, embeds):
    """Naming a constraint couples the code to that name. If a migration
    drops and recreates the foreign key, this is what notices."""
    live = {fk.name for fk in live_fks} | {fk.table for fk in live_fks}
    missing = sorted(
        f"{e.where}: {e.parent} -> {e.child}!{e.hint}"
        for e in embeds
        if e.hint and e.hint not in live
    )
    assert missing == []


def test_the_checked_in_types_agree_with_the_database_on_ambiguity(live_fks):
    """The offline guard is only as good as the last `database.types.ts`
    regeneration — CLAUDE.md requires one per migration, and this is where
    a skipped one shows up."""
    generated = eg.parse_generated_types(
        eg.GENERATED_TYPES.read_text(encoding="utf-8")
    )
    live_pairs = {tuple(sorted(p)) for p in eg.ambiguous_pairs(live_fks)}
    typed_pairs = {tuple(sorted(p)) for p in eg.ambiguous_pairs(generated)}
    assert live_pairs == typed_pairs
