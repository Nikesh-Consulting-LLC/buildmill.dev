"""app/validation.py — US-5.21 structural submission checks."""

import asyncio

import pytest

from app import artifacts_sim, validation
from app.github import GitHubError

GOOD_PRD = """## Problem

Users lose notes.

## Goals

Keep notes.

## Out of scope

Sync.

## Acceptance criteria

- notes persist
"""

GOOD_TEST_PLAN = """Some prose.

```json
{"cases": [{"title": "saves", "steps": "type, save", "expected_result": "persisted", "test_types": ["functional"]}]}
```
"""


def test_parser_parity_is_the_same_function():
    """The dry run can never drift from the gate: it IS the gate's parser."""
    assert validation.parse_test_plan_cases is artifacts_sim.parse_test_plan_cases


def test_prd_happy_path():
    assert validation.validate_prd(GOOD_PRD) == []


def test_prd_missing_section():
    findings = validation.validate_prd(GOOD_PRD.replace("## Out of scope", "## Scope"))
    assert any("## Out of scope" in f and "missing" in f for f in findings)


def test_prd_empty_section():
    prd = GOOD_PRD.replace("Sync.\n", "")
    findings = validation.validate_prd(prd)
    assert any("## Out of scope" in f and "empty" in f for f in findings)


def test_prd_out_of_order():
    reordered = GOOD_PRD.replace("## Problem", "## Zz").replace(
        "## Acceptance criteria", "## Problem"
    ).replace("## Zz", "## Acceptance criteria")
    findings = validation.validate_prd(reordered)
    assert any("out of order" in f for f in findings)


def test_prd_empty_document():
    assert validation.validate_prd("  ") == ["PRD is empty"]


def test_plan_happy_path():
    assert validation.validate_plan("# Plan\n\ndo things", GOOD_TEST_PLAN) == []


def test_plan_empty_plan():
    findings = validation.validate_plan("", GOOD_TEST_PLAN)
    assert any("implementation plan is empty" in f for f in findings)


def test_plan_unparseable_test_plan_names_zero_cases():
    findings = validation.validate_plan("# Plan", "just prose, no fence")
    assert any("0 cases" in f for f in findings)


def test_plan_case_without_expected_result():
    tp = '```json\n{"cases": [{"title": "saves", "steps": "s"}]}\n```'
    findings = validation.validate_plan("# Plan", tp)
    assert any("expected_result" in f for f in findings)


# US-11.5: exit criteria that require *running* a suite are unsatisfiable
# for a worker with no execution environment. On 2026-07-20 a plan whose
# exit criteria said "pytest green, npm test green, no skips" deadlocked a
# code run twice — the agent had written every file and correctly refused
# to report results it could not observe.
@pytest.mark.parametrize(
    "line",
    [
        "Exit criteria: `pytest` green, `npm test` green, no skips.",
        "Done when the full pytest suite passes.",
        "All tests pass and vitest is clean.",
        "The suite must show 0 failures from npm run test.",
        "go test ./... succeeds",
    ],
)
def test_plan_flags_execution_exit_criteria(line):
    findings = validation.validate_plan(f"# Plan\n\n{line}\n", GOOD_TEST_PLAN)
    assert any("require *running* a test suite" in f for f in findings), findings


@pytest.mark.parametrize(
    "line",
    [
        "Add tests in tests/test_auth.py covering each acceptance criterion.",
        "Write pytest cases for the three failure branches.",
        "The plan passes review once the manager approves it.",
        "Run the migration against the live project.",
    ],
)
def test_plan_does_not_flag_ordinary_test_authoring(line):
    """Authoring tests, or the word 'pass' on its own, is not a demand to
    execute a suite — flagging those would train planners to ignore this."""
    findings = validation.validate_plan(f"# Plan\n\n{line}\n", GOOD_TEST_PLAN)
    assert not any("require *running* a test suite" in f for f in findings), findings


def _code_findings(get_branch=None, compare=None):
    import app.validation as v

    async def default_branch_ok(token, owner, repo, branch):
        return {"name": branch}

    async def default_compare(token, owner, repo, base, head):
        return {"ahead_by": 2, "status": "ahead"}

    orig_branch, orig_compare = v.github.get_branch, v.github.compare_commits
    v.github.get_branch = get_branch or default_branch_ok
    v.github.compare_commits = compare or default_compare
    try:
        return asyncio.run(
            v.validate_code_branch("tok", "acme/webshop", "main", "factory/x")
        )
    finally:
        v.github.get_branch, v.github.compare_commits = orig_branch, orig_compare


def test_code_branch_ahead_is_clean():
    assert _code_findings() == []


def test_code_branch_missing_is_worker_fixable():
    async def missing(token, owner, repo, branch):
        raise GitHubError(f"branch '{branch}' not found")

    findings = _code_findings(get_branch=missing)
    assert any("push it" in f for f in findings)


def test_code_branch_not_ahead_flags_nothing_to_merge():
    async def identical(token, owner, repo, base, head):
        return {"ahead_by": 0, "status": "identical"}

    findings = _code_findings(compare=identical)
    assert any("nothing to merge" in f for f in findings)


# ------------------------------------------------- US-15.8: story AC shape


def test_stories_happy_path_array_of_strings():
    stories = [{"title": "T", "acceptance_criteria": ["a", "b"]}]
    assert validation.validate_stories(stories) == []


def test_stories_missing_criteria_is_allowed():
    """acceptance_criteria is optional at the breakdown gate — absence is fine,
    only a *malformed* value is rejected."""
    assert validation.validate_stories([{"title": "T"}]) == []


def test_stories_criteria_as_bare_string_is_rejected():
    stories = [{"title": "T", "acceptance_criteria": "1. do a\n2. do b"}]
    findings = validation.validate_stories(stories)
    assert any("array of strings" in f for f in findings)


def test_stories_criteria_with_non_string_item_is_rejected():
    stories = [{"title": "T", "acceptance_criteria": ["ok", 42]}]
    findings = validation.validate_stories(stories)
    assert any("array of strings" in f for f in findings)


def test_stories_criteria_finding_names_the_index():
    stories = [
        {"title": "A", "acceptance_criteria": ["ok"]},
        {"title": "B", "acceptance_criteria": "block of text"},
    ]
    findings = validation.validate_stories(stories)
    assert any(f.startswith("story 2 ") for f in findings)
    assert not any(f.startswith("story 1 ") for f in findings)


# US-45.2: a long test plan reads as a unit-test list, not the UAT checklist
# approval turns it into. Advisory only — a hand-back is never refused over
# it (us-42.1: a refusal discards the whole payload).


def _test_plan_with(n: int) -> str:
    cases = ", ".join(
        f'{{"title": "case {i}", "steps": "do it", '
        f'"expected_result": "it happened", "test_types": ["functional"]}}'
        for i in range(n)
    )
    return '```json\n{"cases": [' + cases + "]}\n```"


def test_plan_flags_a_test_plan_that_reads_as_a_unit_test_list():
    findings = validation.validate_plan("# Plan", _test_plan_with(9))
    assert any("9 cases" in f and "unit-test list" in f for f in findings)


def test_plan_does_not_flag_eight_cases():
    # Eight is the boundary, and it is a judgement rather than a derivation —
    # pinning both sides of it means moving the number is a deliberate act.
    findings = validation.validate_plan("# Plan", _test_plan_with(8))
    assert not any("unit-test list" in f for f in findings)


def test_the_case_count_finding_is_advisory_not_a_refusal():
    # validate_plan returns findings; nothing here raises, and every other
    # finding still fires alongside it.
    tp = '```json\n{"cases": [' + ", ".join(
        '{"title": "", "steps": "s"}' for _ in range(9)
    ) + "]}\n```"
    findings = validation.validate_plan("# Plan", tp)
    assert any("unit-test list" in f for f in findings)
    assert any("has no title" in f for f in findings)
    assert any("expected_result" in f for f in findings)
