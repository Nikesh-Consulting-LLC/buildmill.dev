"""US-99.5: the file governs, and the text behind it is a fallback.

Once instructions live in the repository, an agent holds two copies of the
same guidance: the file in its workspace and whatever the run context
carries. Two copies is not redundancy, it is a question — a manager can save
an instruction without publishing it (us-99.4), so they *will* differ.

The file wins. The context names it and carries the resolved text behind it,
because a pointer to a file that is not there is worse than the prose was: a
project that has never published still has to get its instruction.

Pure-function tests, since `test_factory_mcp.py` is `needs_db` and skips.
"""

from __future__ import annotations

from app.factory_mcp import _instruction_pointer
from app.instruction_files import EXCLUDED, KIND_FILES


def test_the_pointer_names_the_file_for_every_kind():
    for kind, name in KIND_FILES.items():
        out = _instruction_pointer(kind, "do the thing")
        assert f".buildmill/{name}" in out, kind


def test_the_file_is_named_as_the_one_that_governs():
    out = _instruction_pointer("code", "do the thing")
    assert "Read it there first" in out
    assert "current one" in out


def test_the_resolved_text_still_travels():
    """The fallback is the whole reason this is safe to ship before a
    project has ever published."""
    out = _instruction_pointer("code", "BUILD IT CAREFULLY")
    assert "BUILD IT CAREFULLY" in out


def test_it_warns_that_the_copy_may_lag():
    """An agent that trusts the embedded copy over the file, on a project
    with unpublished edits, is running on yesterday's instruction."""
    out = _instruction_pointer("code", "text")
    assert "may lag" in out


def test_it_names_the_fallback_tool():
    out = _instruction_pointer("plan", "text")
    assert "get_instruction_file" in out


def test_a_blank_instruction_says_so_rather_than_pointing_at_nothing():
    out = _instruction_pointer("code", "")
    assert ".buildmill/Code.md" in out
    assert "No instruction text is configured" in out


def test_a_blank_instruction_is_handled_for_none_too():
    out = _instruction_pointer("code", None)
    assert "No instruction text is configured" in out


def test_an_excluded_kind_behaves_exactly_as_before():
    """The server-side LLM prompts have no file, so they must not gain a
    pointer to one — they would be pointing at something that will never
    exist."""
    for kind in EXCLUDED:
        assert _instruction_pointer(kind, "prompt text") == "prompt text"
        assert _instruction_pointer(kind, "") == ""


def test_an_unknown_kind_passes_its_text_through_untouched():
    assert _instruction_pointer("not_a_kind", "text") == "text"


def test_the_type_differentiated_kinds_each_point_somewhere_different():
    """Phase 96 made a bug's plan run genuinely different from a story's. If
    they pointed at the same file, that work would be undone silently."""
    paths = {
        k: _instruction_pointer(k, "x")
        for k in ("plan", "standalone_plan", "bug_rca", "code",
                  "standalone_code", "bug_fix", "chore")
    }
    files = [KIND_FILES[k] for k in paths]
    assert len(set(files)) == len(files)
    assert ".buildmill/RCA.md" in paths["bug_rca"]
    assert ".buildmill/Plan.md" in paths["plan"]
