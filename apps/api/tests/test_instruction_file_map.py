"""US-99.1: the kind-to-file map is total, reversible, and says the same
thing on both sides of the language boundary.

Phase 96 proved that a mapping with two homes is a mapping that disagrees
with itself, and `run-kinds.ts` proved the same thing again by listing seven
run kinds while the database allowed ten for months. The map here has two
homes by necessity — Python api, TypeScript web, no generation step — so the
agreement is a test rather than a hope.

The totality check is the important one: a future instruction kind added
without a file would otherwise publish nothing and fail silently, which is
exactly the class of bug this phase exists to stop.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.instruction_files import (
    CONVENTIONS_FILE,
    EXCLUDED,
    KIND_FILES,
    ROOT,
    all_paths,
    kind_for_path,
    path_for,
)

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "infra" / "supabase" / "migrations"
TS_MAP = REPO / "apps" / "web" / "src" / "lib" / "instruction-files.ts"


def _db_instruction_kinds() -> set[str]:
    """Every value `worker_instructions.run_kind` allows, from the newest
    migration that rewrites the constraint."""
    newest = None
    for path in sorted(MIGRATIONS.glob("*.sql")):
        text = path.read_text(encoding="utf-8")
        if "add constraint worker_instructions_run_kind_check" in text:
            newest = text
    assert newest, "no migration defines worker_instructions_run_kind_check"
    block = newest.split(
        "add constraint worker_instructions_run_kind_check", 1
    )[1].split(";", 1)[0]
    return set(re.findall(r"'([a-z_]+)'", block))


def _ts_object(name: str) -> dict[str, str]:
    src = TS_MAP.read_text(encoding="utf-8")
    block = src.split(f"export const {name}", 1)[1].split("{", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"^\s*([a-z_]+):\s*\"([^\"]+)\"", block, re.MULTILINE))


def _ts_array(name: str) -> list[str]:
    """Everything between `= [` and the closing `]`.

    Splitting on a bare "[" finds the one in `readonly string[]` and returns
    nothing — which passes vacuously against an empty set, so the anchor is
    the assignment, not the bracket."""
    src = TS_MAP.read_text(encoding="utf-8")
    block = src.split(f"export const {name}", 1)[1].split("= [", 1)[1]
    block = block.split("]", 1)[0]
    return re.findall(r'"([a-z_]+)"', block)


def test_the_map_covers_every_instruction_kind_or_names_why_not():
    """Total by construction: every kind the database allows is either
    published to a file or on the excluded list with a stated reason."""
    db = _db_instruction_kinds()
    accounted = set(KIND_FILES) | set(EXCLUDED)
    unaccounted = db - accounted
    assert not unaccounted, (
        f"these instruction kinds publish nowhere and are not excluded: "
        f"{sorted(unaccounted)} — add them to KIND_FILES, or to EXCLUDED with "
        "the reason they are not agent-readable."
    )


def test_the_map_invents_no_kind_the_database_rejects():
    db = _db_instruction_kinds()
    invented = (set(KIND_FILES) | set(EXCLUDED)) - db
    assert not invented, f"not real instruction kinds: {sorted(invented)}"


def test_every_excluded_kind_carries_a_reason():
    for kind, reason in EXCLUDED.items():
        assert reason.strip(), f"{kind} is excluded without saying why"


def test_no_two_kinds_share_a_file():
    files = list(KIND_FILES.values())
    assert len(files) == len(set(files)), "two kinds publish to one file"


def test_conventions_do_not_collide_with_any_kind():
    """`Guidelines.md` is the project's conventions; the refresh RUN's
    instruction is `Guidelines_Refresh.md`. If these ever collapse into one
    name, a manager editing 'guidelines' can no longer tell which they are
    editing."""
    assert CONVENTIONS_FILE not in KIND_FILES.values()
    assert KIND_FILES["guidelines"] == "Guidelines_Refresh.md"


def test_paths_round_trip():
    for kind in KIND_FILES:
        p = path_for(kind)
        assert p and p.startswith(f"{ROOT}/")
        assert kind_for_path(p) == kind


def test_a_path_the_factory_does_not_own_maps_to_nothing():
    """Checked before overwriting, so a file the factory did not write is
    never mistaken for one it did."""
    assert kind_for_path("README.md") is None
    assert kind_for_path(f"{ROOT}/Something_Else.md") is None
    assert kind_for_path("src/Code.md") is None
    assert kind_for_path("") is None


def test_excluded_kinds_have_no_path():
    for kind in EXCLUDED:
        assert path_for(kind) is None


def test_all_paths_includes_the_conventions_and_nothing_stray():
    paths = all_paths()
    assert f"{ROOT}/{CONVENTIONS_FILE}" in paths
    assert len(paths) == len(KIND_FILES) + 1
    assert all(p.startswith(f"{ROOT}/") for p in paths)


def test_the_web_mirror_agrees_exactly():
    ts = _ts_object("KIND_FILES")
    assert ts == KIND_FILES, (
        "apps/web/src/lib/instruction-files.ts and "
        "apps/api/app/instruction_files.py disagree — missing from the web: "
        f"{sorted(set(KIND_FILES) - set(ts))}; unknown to Python: "
        f"{sorted(set(ts) - set(KIND_FILES))}"
    )


def test_the_web_mirrors_the_exclusions_too():
    assert set(_ts_array("EXCLUDED_KINDS")) == set(EXCLUDED)


def test_the_web_mirrors_the_conventions_filename():
    src = TS_MAP.read_text(encoding="utf-8")
    assert f'CONVENTIONS_FILE = "{CONVENTIONS_FILE}"' in src


def test_the_root_is_dotbuildmill_on_both_sides():
    assert ROOT == ".buildmill"
    src = TS_MAP.read_text(encoding="utf-8")
    assert f'INSTRUCTION_ROOT = "{ROOT}"' in src


# --- us-99.6: the template editors carry the whole set ----------------------


TEMPLATE_EDITORS = (
    REPO / "apps/web/src/app/(app)/admin/project-templates/page.tsx",
    REPO
    / "apps/web/src/app/(app)/settings/project-templates/project-templates-client.tsx",
)


def test_neither_template_editor_hand_lists_the_kinds():
    """The list was duplicated verbatim in both files and omitted all five
    kinds Phase 96 added, so a template could not carry per-type
    instructions. Deriving it from the map is what stops that recurring —
    a hand-written list here is the regression."""
    for path in TEMPLATE_EDITORS:
        src = path.read_text(encoding="utf-8")
        assert "WORKER_INSTRUCTION_KINDS = Object.keys(KIND_FILES)" in src, (
            f"{path.name} hand-lists the instruction kinds again"
        )
        assert 'from "@/lib/instruction-files"' in src


def test_the_phase_96_kinds_are_now_carryable():
    for kind in (
        "chore",
        "bug_rca",
        "bug_fix",
        "standalone_plan",
        "standalone_code",
    ):
        assert kind in KIND_FILES
