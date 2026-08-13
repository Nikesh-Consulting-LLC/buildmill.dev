"""Deterministic synthetic content for PRD / story breakdown / plan runs.

Used when no LLM is configured (or the operator opts into simulation) so the
Phase-2 workflow is testable without a live model. Real LLM calls live in
llm.py; the external coding agent produces plan/code via the runner callback.
"""

from __future__ import annotations

import json
from typing import Any


def simulate_prd(title: str, body: str | None) -> str:
    idea = (body or "").strip() or title
    return (
        f"## Problem\n\n{idea}\n\n"
        f"## Goals\n\n"
        f"- Deliver “{title}” end-to-end with clear acceptance criteria.\n"
        f"- Keep the change reviewable and reverseable.\n\n"
        f"## Out of scope\n\n"
        f"- Unrelated refactors and opportunistic cleanups.\n"
        f"- Cross-cutting platform work not required for this feature.\n\n"
        f"## Acceptance criteria\n\n"
        f"- [ ] A user can complete the primary flow described in the problem.\n"
        f"- [ ] Edge cases called out in the problem are handled or documented.\n"
        f"- [ ] Failure modes surface a clear error rather than silent corruption.\n"
    )


def simulate_story_breakdown(title: str, prd: str) -> list[dict[str, Any]]:
    """Return editable story draft slices derived from a PRD."""
    _ = prd  # available for a real LLM; simulator uses title only
    base = title.strip() or "Feature"
    return [
        {
            "title": f"{base} — data model",
            "body": f"As a developer, I need the schema for “{base}” so later slices have a stable foundation.",
            "acceptance_criteria": [
                "Tables/columns (or equivalents) exist with RLS where applicable",
                "Types regenerated / contracts updated",
            ],
        },
        {
            "title": f"{base} — API & orchestration",
            "body": f"As an operator, I can drive “{base}” through the factory API without hand-editing the DB.",
            "acceptance_criteria": [
                "Endpoints cover the happy path and rejection paths",
                "Errors are actionable for the UI",
            ],
        },
        {
            "title": f"{base} — UI surface",
            "body": f"As a PM, I can create, review, and approve “{base}” work from the web app.",
            "acceptance_criteria": [
                "Primary actions are reachable from issue detail",
                "Status badges reflect the new lifecycle",
            ],
        },
    ]


def simulate_plan(ctx: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (plan_markdown, test_plan_markdown, structured_cases)."""
    title = ctx.get("title") or "issue"
    issue_type = ctx.get("type") or "story"
    criteria = ctx.get("acceptance_criteria") or []
    feedback = ctx.get("feedback")

    root_cause = ""
    if issue_type == "bug":
        root_cause = (
            "## Root cause\n\n"
            "Simulated diagnosis: the reported behavior mismatches the expected "
            "path described in the issue body. The fix should restore that path "
            "and add a regression check.\n\n"
        )

    plan = (
        f"{root_cause}"
        f"## Implementation plan\n\n"
        f"### Summary\nImplement “{title}” ({issue_type}).\n\n"
        f"### Files / services\n"
        f"- Application code touching the feature surface\n"
        f"- Tests covering the acceptance criteria\n\n"
        f"### Sequencing\n"
        f"1. Confirm current behavior against the issue body\n"
        f"2. Implement the minimal change set\n"
        f"3. Add/adjust tests from the test plan\n"
        f"4. Open a PR for review\n\n"
        f"### Dependencies & risks\n"
        f"- Depends on any approved PRD context supplied in input\n"
        f"- Risk: over-scoping — keep the diff limited to the criteria\n"
    )
    if feedback:
        plan += f"\n### Addressing prior feedback\n{feedback}\n"

    cases: list[dict[str, Any]] = []
    if criteria:
        for i, c in enumerate(criteria, 1):
            cases.append(
                {
                    "title": f"AC{i}: {str(c)[:120]}",
                    "steps": f"1. Set up the scenario for “{title}”\n2. Exercise: {c}\n3. Observe the result",
                    "expected_result": str(c),
                    "test_types": ["regression"],
                    "environments": ["dev", "uat"],
                }
            )
    else:
        cases.append(
            {
                "title": f"Verify: {title}",
                "steps": f"1. Open the surface for “{title}”\n2. Complete the primary flow\n3. Confirm success",
                "expected_result": f"“{title}” works as described",
                "test_types": ["regression"],
                "environments": ["dev"],
            }
        )

    test_plan_md = (
        f"## Test plan\n\n"
        f"### Approach\nManual + targeted automated checks for “{title}”.\n\n"
        f"### Coverage\n"
        + "\n".join(f"- {c['title']}" for c in cases)
        + "\n\n### Proposed cases (structured)\n```json\n"
        + json.dumps(cases, indent=2)
        + "\n```\n"
    )
    return plan, test_plan_md, cases


def as_text(value: Any) -> str:
    """US-42.1: an agent writes a test case's `steps` as a list of steps at
    least as often as one newline-joined string, and the two say the same
    thing — so every boundary coerces instead of refusing or storing the
    shape it happened to receive.

    Nothing is invented and nothing is dropped: a list is joined in the order
    given, a scalar is stringified, and a dict lands as compact JSON rather
    than a Python repr — unreadable beats lost."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = (as_text(v) for v in value)
        return "\n".join(p for p in (str(p).strip() for p in parts) if p)
    if isinstance(value, dict):
        try:
            return json.dumps(value)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def as_str_list(value: Any) -> list[str]:
    """The inverse of as_text on the same fields — one tag where a list of
    tags is expected. Same reasoning, so the same treatment."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [v if isinstance(v, str) else as_text(v) for v in value]
    return value


def _normalize_case(case: Any) -> dict[str, Any]:
    """Give a parsed case the shape the `test_cases` row expects.

    The JSON in a test plan is written by an agent, and it writes `steps` as
    a list about as often as a string. Coercing here rather than at each
    insert is what keeps the two materialization paths — the manager's plan
    approval and the auto-approve one in `db.py` — from disagreeing, and
    keeps `validate_submission` honest: it parses with this same function,
    so what it tells the agent will be stored is what is stored.

    Without this a list `steps` reached Postgres as a JSON array and landed
    in the text column verbatim, so the manager's UAT checklist read
    `["Sign in with a valid account.", ...]` instead of the steps.
    """
    if not isinstance(case, dict):
        return {"title": as_text(case)}
    out = dict(case)
    for key in ("title", "steps", "expected_result"):
        if key in out:
            out[key] = as_text(out[key])
    for key in ("test_types", "environments"):
        if key in out:
            out[key] = as_str_list(out[key])
    # US-81.5: a plan may mark a case automated (a spec will answer it) and
    # name the layer it belongs to. Anything else the agent invents for these
    # keys is dropped rather than stored — the columns have vocabularies.
    execution = str(out.pop("execution", "") or "").strip().lower()
    if execution in ("manual", "automated"):
        out["execution"] = execution
    layer = str(out.pop("layer", "") or "").strip().lower()
    if layer in ("api", "browser"):
        out["layer"] = layer
    return out


def parse_test_plan_cases(content: str) -> list[dict[str, Any]]:
    """Extract structured cases from a test_plan artifact (JSON fence or whole JSON)."""
    content = (content or "").strip()
    if not content:
        return []
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "cases" in data:
            return [_normalize_case(c) for c in data["cases"]]
        if isinstance(data, list):
            return [_normalize_case(c) for c in data]
    except json.JSONDecodeError:
        pass
    marker = "```json"
    start = content.find(marker)
    if start == -1:
        return []
    start = content.find("\n", start) + 1
    end = content.find("```", start)
    if end == -1:
        return []
    try:
        data = json.loads(content[start:end].strip())
        if isinstance(data, list):
            return [_normalize_case(c) for c in data]
        if isinstance(data, dict) and "cases" in data:
            return [_normalize_case(c) for c in data["cases"]]
    except json.JSONDecodeError:
        return []
    return []
