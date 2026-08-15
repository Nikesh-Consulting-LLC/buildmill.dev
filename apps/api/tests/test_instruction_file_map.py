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


TEMPLATE_FILES_TS = REPO / "apps/web/src/lib/template-files.ts"
SHARED_EDITOR = REPO / "apps/web/src/components/template-files-editor.tsx"


def test_neither_template_editor_hand_lists_the_kinds():
    """The list was duplicated verbatim in both files and omitted all five
    kinds Phase 96 added, so a template could not carry per-type
    instructions. us-100.4 moved the derivation into one shared module
    (`template-files.ts`, which reads KIND_FILES) and both editors render
    from the one shared component — a hand-written list in either page is
    the regression."""
    src = TEMPLATE_FILES_TS.read_text(encoding="utf-8")
    assert "Object.keys(KIND_FILES)" in src
    assert 'from "./instruction-files.ts"' in src
    for path in TEMPLATE_EDITORS:
        page = path.read_text(encoding="utf-8")
        assert 'from "@/components/template-files-editor"' in page, (
            f"{path.name} does not use the shared template file editor"
        )
        assert "WORKER_INSTRUCTION_KINDS" not in page, (
            f"{path.name} hand-lists the instruction kinds again"
        )
        # us-100.4 AC2: neither retired section type is offered any more —
        # no code path names them (prose in comments may).
        for token in ('"guideline"', '"prompt"', "NEW_GUIDELINE", "PROMPT_KINDS"):
            assert token not in page, f"{path.name} still offers {token} sections"
    shared = SHARED_EDITOR.read_text(encoding="utf-8")
    assert "templateFileGroups()" in shared


def test_the_phase_96_kinds_are_now_carryable():
    for kind in (
        "chore",
        "bug_rca",
        "bug_fix",
        "standalone_plan",
        "standalone_code",
    ):
        assert kind in KIND_FILES


# --- us-100.3: one vocabulary, and the old words are gone -------------------


PROJECT_PAGE = REPO / "apps/web/src/app/(app)/projects/[id]/page.tsx"
AUDIT_LABELS = REPO / "apps/web/src/app/(app)/projects/[id]/content-audit-section.tsx"


def test_the_tabs_are_named_for_what_they_hold():
    """The reported defect: the tab labelled "Agent Instructions" held the
    per-TASK instructions, so the one place a manager would look for their
    project's instructions showed a list of run-kind editors."""
    src = PROJECT_PAGE.read_text(encoding="utf-8")
    assert '<TabsTrigger value="guidelines">Agent Instructions</TabsTrigger>' in src
    assert "Task Instructions" in src


def test_no_surface_still_calls_the_document_guidelines():
    """`guidelines` survives as a storage key and a deep-link value — both
    deliberate — but nothing a manager READS may still say it."""
    for path in (PROJECT_PAGE, AUDIT_LABELS):
        src = path.read_text(encoding="utf-8")
        assert ">Guidelines<" not in src, f"{path.name} still labels it Guidelines"
        assert '"Guidelines"' not in src, f"{path.name} still labels it Guidelines"


def test_the_audit_trail_uses_the_same_two_names():
    src = AUDIT_LABELS.read_text(encoding="utf-8")
    assert 'guidelines: "Agent Instructions"' in src
    assert 'worker_instructions: "Task Instructions"' in src


def test_the_deep_link_value_is_unchanged():
    """us-100.3 AC4: ?tab=guidelines must keep resolving — it is in
    notifications and bookmarks."""
    src = PROJECT_PAGE.read_text(encoding="utf-8")
    assert 'value="guidelines"' in src
    assert "?tab=guidelines" in src


# --- us-100.4: a template carries the document, and loses nothing -----------


def test_the_template_migration_deletes_nothing():
    """DELIBERATE DEVIATION, pinned so it is not "corrected" later.

    us-100.4 AC2 said prompt rows are dropped. Production holds 18 prompt
    sections and every one has content; `llm_prompt_templates` does not hold
    the same thing, so deleting them is real data loss. Retiring them from the
    EDITOR needs no deletion, so migration 265 is purely additive.
    """
    src = (MIGRATIONS / "265_a_template_carries_the_document.sql").read_text(
        encoding="utf-8"
    )
    lowered = src.lower()
    assert "drop column" not in lowered
    assert "drop table" not in lowered
    assert "delete from" not in lowered
    # It adds the document to both template levels.
    assert "project_templates" in src and "org_project_templates" in src
    assert "agent_instructions" in src


def test_the_template_migration_verifies_its_own_backfill():
    """Same shape as 263: compare against what it was derived from, and abort
    rather than leave a template short of the document it should have."""
    src = (MIGRATIONS / "265_a_template_carries_the_document.sql").read_text(
        encoding="utf-8"
    )
    assert "raise exception" in src.lower()
    assert "rolling back" in src.lower()


def test_the_template_editors_no_longer_offer_prompt_sections():
    """us-100.4: retired from the editor, NOT from the database. Migration 265
    deletes nothing, so reverting this commit restores the surface — no backup
    involved."""
    for path in TEMPLATE_EDITORS:
        src = path.read_text(encoding="utf-8")
        assert "PROMPT_KINDS" not in src and "PROMPT_LABELS" not in src, (
            f"{path.name} still offers prompt sections in a template"
        )
    # And the server refuses to write them (AC6).
    from app.routers import admin as admin_router

    assert "prompt" not in admin_router._TEMPLATE_SECTION_TYPES
    assert "guideline" not in admin_router._TEMPLATE_SECTION_TYPES


def test_a_new_project_inherits_its_templates_document():
    src = (MIGRATIONS / "266_a_new_project_inherits_the_document.sql").read_text(
        encoding="utf-8"
    )
    assert "agent_instructions = t.agent_instructions" in src
    # Never overwrite a document the create supplied itself.
    assert "coalesce(btrim(p.agent_instructions), '') = ''" in src
    assert "delete from" not in src.lower()
