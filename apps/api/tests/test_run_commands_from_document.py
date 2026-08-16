"""us-100.1 AC7 (closing a reader the story missed): the "Run commands"
section a work context surfaces (US-5.9) comes from the Agent Instructions
document, not from `project_guidelines`.

After migration 263 that table is a frozen rollback snapshot. Reading it
would keep serving the run commands a manager has since edited in the
document — a stale instruction with the authority of a live one.
"""

from __future__ import annotations

import inspect

from app import db
from app.db import extract_markdown_section

DOC = """# Conventions

Some prose.

## Run commands

npm run build
npm test

## Testing

pytest -q
"""


def test_extracts_the_body_under_the_heading():
    assert extract_markdown_section(DOC, "Run commands") == "npm run build\nnpm test"


def test_heading_match_is_case_and_level_insensitive():
    assert extract_markdown_section("### RUN COMMANDS\n\nmake", "Run commands") == "make"


def test_stops_at_the_next_heading_of_equal_or_higher_level():
    doc = "## Run commands\n\nnpm test\n\n### detail\n\nmore\n\n## Next\n\nno"
    assert extract_markdown_section(doc, "Run commands") == "npm test\n\n### detail\n\nmore"


def test_absent_or_blank_is_none():
    assert extract_markdown_section("# Other\n\ntext", "Run commands") is None
    assert extract_markdown_section("## Run commands\n\n\n## Next\n\nx", "Run commands") is None
    assert extract_markdown_section("", "Run commands") is None


def test_the_reader_reads_the_document_not_the_dead_table():
    src = inspect.getsource(db.get_run_commands_section)
    assert "agent_instructions" in src
    assert "from public.project_guidelines" not in src


def test_no_python_reads_project_guidelines_any_more():
    """us-100.1 AC7: nothing reads the table after the document landed —
    checked, not intended. `assemble_project_guidelines` (the SQL function
    263 repointed at the document) is the one name that may still appear."""
    src = inspect.getsource(db)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or "never from" in stripped:
            continue
        if "public.project_guidelines" in stripped and "assemble_project_guidelines" not in stripped:
            raise AssertionError(f"db.py still reads project_guidelines: {stripped}")
    assert not hasattr(db, "get_guideline_section")
    assert not hasattr(db, "list_guideline_section_keys")
