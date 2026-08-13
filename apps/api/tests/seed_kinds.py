"""US-75.1: the run kinds a new project is seeded with, read from the seeding
trigger itself.

Three tests used to keep their own hard-coded copy of this list, and all three
went red together when `test_case_elaborate` and `deploy_script_generate` were
added — the third time the list had been re-hard-coded and the third time it
went stale. The list has exactly one source of truth,
`seed_worker_instructions`, so the tests read it from there: extend the trigger
and the expectations follow, forget to seed a kind and the tests still trip,
which is the property those tests were written for.

The trigger seeds in two passes, and the difference matters to the assertions:

* **instruction kinds** — filled from `default_worker_instruction(kind)`, so a
  seeded row always has content.
* **prompt kinds** — the two project-shaped thinking prompts, filled from the
  org template when it carries one and otherwise left **deliberately blank**
  (`resolve_prompt` falls back to the global override / LLM_FUNCTIONS default).
  Asserting these are non-empty would be asserting the opposite of the design.
"""

from __future__ import annotations

import re

# The trigger builds its kinds as `values ('prd'), ('plan'), ...` — a quoted
# lowercase token wrapped in its own parentheses. Nothing else in the body has
# that shape: `= 'worker_instruction'` has no parens, `coalesce(..., '')` is
# empty, and `default_worker_instruction(k.kind)` is unquoted.
_KIND_IN_VALUES = re.compile(r"\('([a-z_]+)'\)")

_INSERT = "insert into public.worker_instructions"


def _prosrc(db) -> str:
    row = db.execute(
        "select prosrc from pg_proc p "
        "join pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = 'seed_worker_instructions'"
    ).fetchone()
    assert row, "seed_worker_instructions is missing from the database"
    return row["prosrc"]


def seeded_kinds(db) -> tuple[set[str], set[str]]:
    """`(instruction_kinds, prompt_kinds)` as the trigger seeds them."""
    src = _prosrc(db)
    passes = src.split(_INSERT)
    # [0] is the declaration preamble; each following chunk is one insert.
    assert len(passes) == 3, (
        f"expected 2 insert statements in seed_worker_instructions, found "
        f"{len(passes) - 1}; the trigger's shape changed and this extractor "
        "needs updating"
    )
    instruction = set(_KIND_IN_VALUES.findall(passes[1]))
    prompt = set(_KIND_IN_VALUES.findall(passes[2]))
    # A parse that silently returned nothing would make every assertion that
    # uses it vacuously true — the exact failure mode this module prevents.
    assert len(instruction) >= 10 and prompt, (
        f"parsed instruction={sorted(instruction)} prompt={sorted(prompt)} "
        "from seed_worker_instructions; the trigger's shape changed"
    )
    assert not (instruction & prompt), "a kind is seeded by both passes"
    return instruction, prompt


def seeded_run_kinds(db) -> set[str]:
    """Every run_kind a new project gets a row for, from either pass."""
    instruction, prompt = seeded_kinds(db)
    return instruction | prompt
