"""Every closed set that lists run kinds agrees with the database — US-98.1.

`test_runner_kind_coverage.py` pins the runner's `HANDBACK_SHAPE` against
`runs_kind_check` because a kind the runner has never heard of leaves runs
sitting `queued` forever. That bug shipped three times.

It is not the only closed set. Two more list run kinds and can rot the same
way, and both had:

  * `ROUTE_KINDS` (apps/api/app/routers/runner_socket.py) — a kind absent
    here cannot be model-routed or gated by a preset.
  * `RUN_KINDS` (apps/web/src/lib/run-kinds.ts) — a kind absent here reads
    as "not dispatchable yet" everywhere in the web app. It had frozen at
    **seven** while the database moved to ten; `guidelines`, `elaborate` and
    `wireframe` were dispatchable and routable for months while this file
    said otherwise. Its own header rationalized understating as "the safe
    direction to be wrong in" — true of a kind that does not exist yet,
    false of three that do.

Sources are parsed, not imported: apps/web is TypeScript and apps/runner is
a separate program, and this contract must hold without either toolchain.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "infra" / "supabase" / "migrations"
RUN_KINDS_TS = REPO / "apps" / "web" / "src" / "lib" / "run-kinds.ts"
RUNNER_SOCKET = REPO / "apps" / "api" / "app" / "routers" / "runner_socket.py"


def _db_run_kinds() -> set[str]:
    """The kinds `runs.kind` allows, from the newest migration that rewrites
    the constraint. Same derivation as test_runner_kind_coverage."""
    newest = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "add constraint runs_kind_check" in text:
            newest = text
    assert newest, "no migration defines runs_kind_check"
    block = newest.split("add constraint runs_kind_check", 1)[1].split(")", 1)[0]
    return set(re.findall(r"'([a-z_]+)'", block))


def _ts_array(name: str) -> list[str]:
    """The string members of an `export const <name> = [...]` array."""
    src = RUN_KINDS_TS.read_text(encoding="utf-8")
    block = src.split(f"export const {name} = [", 1)[1].split("]", 1)[0]
    return re.findall(r'"([a-z_]+)"', block)


def _ts_record_keys(name: str) -> set[str]:
    """The keys of an `export const <name>: Record<...> = {...}` object."""
    src = RUN_KINDS_TS.read_text(encoding="utf-8")
    block = src.split(f"export const {name}", 1)[1].split("{", 1)[1].split("}", 1)[0]
    return set(re.findall(r"^\s*([a-z_]+):", block, re.MULTILINE))


def _route_kinds() -> set[str]:
    src = RUNNER_SOCKET.read_text(encoding="utf-8")
    block = src.split("ROUTE_KINDS = (", 1)[1].split(")", 1)[0]
    return set(re.findall(r'"([a-z_]+)"', block))


def test_route_kinds_covers_every_dispatchable_kind():
    missing = _db_run_kinds() - _route_kinds()
    assert not missing, (
        f"ROUTE_KINDS omits {sorted(missing)} — a run of this kind cannot be "
        "model-routed or gated by a preset. Add it in "
        "apps/api/app/routers/runner_socket.py."
    )


def test_route_kinds_declares_nothing_the_database_rejects():
    extra = _route_kinds() - _db_run_kinds()
    assert not extra, f"ROUTE_KINDS lists kinds the database rejects: {sorted(extra)}"


def test_web_run_kinds_matches_the_database():
    """The web app's idea of what is dispatchable, against the real one."""
    web = set(_ts_array("RUN_KINDS"))
    db = _db_run_kinds()
    assert web == db, (
        f"run-kinds.ts and runs_kind_check disagree — missing from the web: "
        f"{sorted(db - web)}; unknown to the database: {sorted(web - db)}. A "
        "kind absent from RUN_KINDS reads as 'not dispatchable yet' across "
        "the whole web app, which is how three real kinds stayed invisible."
    )


def test_web_run_kinds_has_no_duplicates():
    members = _ts_array("RUN_KINDS")
    assert len(members) == len(set(members)), f"RUN_KINDS repeats a kind: {members}"


def test_every_web_kind_map_is_total():
    """`Record<RunKind, string>` is only checked by tsc at the declaration;
    these maps are what surfaces actually read, so a missing entry is a raw
    slug on screen."""
    kinds = set(_ts_array("RUN_KINDS"))
    for name in ("RUN_KIND_LABELS", "RUN_KIND_RUN_PHRASES", "RUN_KIND_GRANT_HELP"):
        keys = _ts_record_keys(name)
        assert keys == kinds, (
            f"{name} does not cover RUN_KINDS — missing {sorted(kinds - keys)}, "
            f"extra {sorted(keys - kinds)}"
        )


def test_merge_is_dispatchable_everywhere():
    """us-98.1's own kind, named explicitly so a revert is loud."""
    assert "merge" in _db_run_kinds()
    assert "merge" in _route_kinds()
    assert "merge" in _ts_array("RUN_KINDS")
