"""US-5.21: structural dry-run checks for submissions.

Shared by the validate_submission MCP tool and the submit endpoints, and
built on the same parser the plan-approval gate uses
(artifacts_sim.parse_test_plan_cases) — parity by construction, never a
reimplementation that can drift. Structure only: quality stays the
manager's gate, and findings are warnings, not new rejection paths.
"""

import re
from typing import Any

from . import github
from .artifacts_sim import parse_test_plan_cases

__all__ = [
    "parse_test_plan_cases",
    "validate_code_branch",
    "validate_plan",
    "validate_prd",
    "validate_stories",
]

PRD_SECTIONS = (
    ("problem", "## Problem"),
    ("goals", "## Goals"),
    ("out of scope", "## Out of scope"),
    ("acceptance criteria", "## Acceptance criteria"),
)


def validate_prd(prd: str) -> list[str]:
    """The four required sections: present, in order, non-empty."""
    text = prd or ""
    if not text.strip():
        return ["PRD is empty"]
    findings: list[str] = []
    headers = [
        (m.group(1).strip().lower(), m.start(), m.end())
        for m in re.finditer(r"(?m)^##\s+(.+?)\s*$", text)
    ]
    positions: list[int] = []
    for key, display in PRD_SECTIONS:
        hit = next((h for h in headers if h[0] == key), None)
        if hit is None:
            findings.append(f"missing required section `{display}`")
            continue
        positions.append(hit[1])
        later = [h[1] for h in headers if h[1] > hit[1]]
        body_end = min(later) if later else len(text)
        if not text[hit[2] : body_end].strip():
            findings.append(f"section `{display}` is empty")
    if positions != sorted(positions):
        findings.append(
            "PRD sections are out of order — expected Problem, Goals, "
            "Out of scope, Acceptance criteria"
        )
    return findings


# US-11.5: a plan is executed by whichever worker claims the code run, and
# they differ in capability — a supervisor runner has a shell and can run a
# suite, a bare MCP client has no checkout and no interpreter at all. A plan
# that makes "the suite is green" a hard exit criterion is therefore
# unsatisfiable for some of its possible executors, and on 2026-07-20 that
# deadlocked a real code run twice: the agent had written every file, could
# not run pytest, and correctly refused to report results it had not
# observed. Flagged so the planning agent rewrites the criterion into
# something any executor can meet.
_TEST_COMMANDS = (
    r"pytest|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+test|vitest|jest"
    r"|go\s+test|cargo\s+test|mvn\s+test|gradle\s+test|rspec|phpunit"
)
_GREEN_WORDS = r"green|passing|passes|pass\b|clean|succeed|no\s+failures|0\s+failures"
_EXECUTION_EXIT_CRITERION = re.compile(
    rf"(?i)(?:(?:{_TEST_COMMANDS}).*(?:{_GREEN_WORDS})"
    rf"|(?:{_GREEN_WORDS}).*(?:{_TEST_COMMANDS}))"
)


# US-45.2: above this, a test plan reads as a unit-test list rather than the
# UAT checklist approval turns it into. Advisory only — see validate_plan.
MAX_ADVISORY_TEST_CASES = 8


def validate_plan(plan: str, test_plan: str | None) -> list[str]:
    """Implementation plan non-empty; test plan (when provided) parses
    into ≥1 structured case via the approval gate's own parser."""
    findings: list[str] = []
    if not (plan or "").strip():
        findings.append("implementation plan is empty")
    for line in (plan or "").splitlines():
        if _EXECUTION_EXIT_CRITERION.search(line):
            findings.append(
                "exit criteria appear to require *running* a test suite "
                f"({line.strip()[:80]!r}). The worker that executes this "
                "plan may have no way to run it — a supervisor runner has a "
                "shell, a bare MCP client has no checkout and no "
                "interpreter. State the criterion as tests authored and "
                "validate_submission clean, and leave execution to whoever "
                "can actually observe it."
            )
            break
    if (test_plan or "").strip():
        cases = parse_test_plan_cases(test_plan or "")
        if not cases:
            findings.append(
                "test plan did not parse: 0 cases found — approval will "
                "materialize no test cases. Wrap a ```json fence around "
                '{"cases": [{"title", "steps", "expected_result", '
                '"test_types"}]}'
            )
        # US-45.2: a test plan is not a document — approving it INSERTS
        # test_cases rows the manager walks by hand. A plan run cannot run
        # anything (no workspace, no shell), so a long list here is an agent
        # inventing unit tests about code that does not exist yet, one story
        # before the agent that will have both the tree and a shell.
        #
        # Advisory, and never a refusal. validate_submission records findings
        # as a `submission-findings` event (us-5.21); us-42.1 is the standing
        # lesson that a refusal discards the whole payload — fifteen runs paid
        # for that once. Eight is a judgement, not a derivation: it is stated
        # here so the next person can move it knowing it was chosen.
        if len(cases) > MAX_ADVISORY_TEST_CASES:
            findings.append(
                f"test plan carries {len(cases)} cases — more than "
                f"{MAX_ADVISORY_TEST_CASES}, which usually means it reads as "
                "a unit-test list rather than a UAT checklist. Approving it "
                "materializes one row per case for a human to walk. Aim for "
                "three to six acceptance-level cases (things a person can "
                "observe); the unit and integration tests belong to the "
                "coding agent, which has the working tree and can run them."
            )
        for i, case in enumerate(cases, 1):
            title = (case.get("title") or "").strip() if isinstance(case, dict) else ""
            if not isinstance(case, dict) or not title:
                findings.append(f"test case {i} has no title")
            if isinstance(case, dict) and not (
                case.get("expected_result") or ""
            ).strip():
                findings.append(
                    f"test case {i} ({title or 'untitled'}) has no "
                    "expected_result"
                )
    return findings


def validate_stories(stories: list[dict[str, Any]] | None) -> list[str]:
    """US-2.33 breakdown hand-back: at least one story, each with a
    non-empty title. US-15.8: acceptance_criteria, when present, must be a
    JSON array of strings — a worker that formats it as one numbered block of
    text stores a jsonb *string* that crashes the story's page. Flag it at the
    dry-run so it never reaches submit_stories. Story quality stays the
    manager's per-story gate."""
    items = stories or []
    if not items:
        return ["no stories — the breakdown must produce at least one story"]
    findings: list[str] = []
    for i, s in enumerate(items, 1):
        if not isinstance(s, dict) or not str(s.get("title") or "").strip():
            findings.append(f"story {i} has no title")
        if isinstance(s, dict):
            ac = s.get("acceptance_criteria")
            if ac is not None and not (
                isinstance(ac, list) and all(isinstance(c, str) for c in ac)
            ):
                findings.append(
                    f"story {i} acceptance_criteria must be a JSON array of "
                    "strings, not a single block of text"
                )
    return findings


async def validate_code_branch(
    token: str,
    repo_full_name: str,
    default_branch: str,
    branch_ref: str,
) -> list[str]:
    """The branch exists on GitHub and is ahead of the default branch
    (has commits to merge)."""
    owner, repo = repo_full_name.split("/", 1)
    try:
        await github.get_branch(token, owner, repo, branch_ref)
    except github.GitHubError as e:
        if "not found" in e.message:
            return [
                f"branch '{branch_ref}' not found on GitHub — push it "
                "and retry"
            ]
        return [e.message]
    try:
        compare = await github.compare_commits(
            token, owner, repo, default_branch, branch_ref
        )
    except github.GitHubError as e:
        return [e.message]
    if not int(compare.get("ahead_by") or 0):
        return [
            f"branch '{branch_ref}' has no commits beyond "
            f"'{default_branch}' — nothing to merge"
        ]
    return []
