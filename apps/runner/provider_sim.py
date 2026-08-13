"""Simulated provider (US-1.10 / US-2.5).

Stands in for Claude Code CLI behind the same contract the real provider
will implement: input_context in, ProviderResult out. The scenario is
chosen by a [sim:...] tag in the issue title or story text:

    [sim:ok]     succeed with a plausible result (default)
    [sim:stuck]  hang past the runner timeout -> reported as failed
    [sim:fail]   crash like a provider error -> reported as failed

When input_context.run_kind == "plan" (or kind from claim), produces an
implementation plan + test plan instead of a code diff.
"""

import json
import time
from dataclasses import dataclass


@dataclass
class ProviderResult:
    outcome: str  # succeeded | failed
    stdout: str | None = None
    diff: str | None = None
    branch_ref: str | None = None
    pr_url: str | None = None
    error: str | None = None
    test_cases: list | None = None
    plan: str | None = None
    test_plan: str | None = None
    prd: str | None = None
    stories: list | None = None


def _scenario(ctx: dict) -> str:
    text = f"{ctx.get('title', '')} {ctx.get('story', '')} {ctx.get('body', '')}".lower()
    for tag in ("stuck", "fail", "ok"):
        if f"[sim:{tag}]" in text:
            return tag
    return "ok"


def _slug(title: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in title.lower())[:40].strip("-")


def _fake_diff(ctx: dict, retry: bool) -> str:
    criteria = ctx.get("acceptance_criteria") or []
    lines = [
        "diff --git a/src/health.py b/src/health.py",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/src/health.py",
        "@@ -0,0 +1,8 @@",
        "+# " + ctx.get("title", "change"),
        "+def health():",
        '+    return {"status": "ok", "version": VERSION}',
        "+",
    ]
    for i, c in enumerate(criteria, 1):
        lines.append(f"+# criterion {i}: {c}")
    if retry:
        lines += [
            "diff --git a/src/health.py b/src/health.py",
            "--- a/src/health.py",
            "+++ b/src/health.py",
            "@@ -1,3 +1,4 @@",
            f"+# addressed feedback: {ctx.get('feedback', '')[:60]}",
        ]
    return "\n".join(lines)


def _plan_result(ctx: dict) -> ProviderResult:
    title = ctx.get("title", "issue")
    issue_type = ctx.get("type") or "story"
    criteria = ctx.get("acceptance_criteria") or []
    feedback = ctx.get("feedback")

    root = ""
    if issue_type == "bug":
        root = (
            "## Root cause\n\n"
            "Simulated diagnosis: behavior diverges from the expected path in "
            "the issue body. Fix restores that path and adds a regression check.\n\n"
        )

    plan = (
        f"{root}## Implementation plan\n\n"
        f"### Summary\nImplement “{title}” ({issue_type}).\n\n"
        f"### Files / services\n"
        f"- Application code for the requested change\n"
        f"- Tests covering acceptance criteria\n\n"
        f"### Sequencing\n"
        f"1. Reproduce / confirm current behavior\n"
        f"2. Implement the minimal change set\n"
        f"3. Cover with the test plan cases\n"
        f"4. Open a PR\n\n"
        f"### Risks\n"
        f"- Over-scoping beyond the acceptance criteria\n"
    )
    if feedback:
        plan += f"\n### Addressing prior feedback\n{feedback}\n"
    if ctx.get("previous_plan"):
        plan += "\n### Prior plan (context)\nSee previous_plan in input context.\n"
    if ctx.get("prd"):
        plan += "\n### PRD context\nApproved PRD attached in input context.\n"

    cases = []
    if criteria:
        for i, c in enumerate(criteria, 1):
            cases.append(
                {
                    "title": f"AC{i}: {str(c)[:120]}",
                    "steps": f"1. Set up “{title}”\n2. Exercise: {c}\n3. Observe",
                    "expected_result": str(c),
                    "test_types": ["regression"],
                    "environments": ["dev", "uat"],
                }
            )
    else:
        cases.append(
            {
                "title": f"Verify: {title}",
                "steps": f"1. Open “{title}”\n2. Complete the primary flow\n3. Confirm",
                "expected_result": f"“{title}” works as described",
                "test_types": ["regression"],
                "environments": ["dev"],
            }
        )

    test_plan = (
        f"## Test plan\n\n### Approach\nManual verification for “{title}”.\n\n"
        f"### Coverage\n"
        + "\n".join(f"- {c['title']}" for c in cases)
        + "\n\n### Proposed cases (structured)\n```json\n"
        + json.dumps(cases, indent=2)
        + "\n```\n"
    )

    stdout = "\n".join(
        [
            f"[sim] plan run started: {title}",
            f"[sim] type={issue_type} run_kind=plan",
            "[sim] wrote implementation plan + test plan",
        ]
    )
    return ProviderResult(
        outcome="succeeded",
        stdout=stdout,
        plan=plan,
        test_plan=test_plan,
    )


def _prd_result(ctx: dict) -> ProviderResult:
    title = ctx.get("title", "feature")
    body = (ctx.get("story") or ctx.get("body") or "")[:200]
    feedback = ctx.get("feedback")
    content = (
        f"## Problem\n\nSimulated PRD for '{title}'. {body}\n\n"
        f"## Goals\n\n- Deliver '{title}' as described in the raw idea\n\n"
        f"## Out of scope\n\n- Anything not explicitly listed above\n\n"
        f"## Acceptance criteria\n\n- '{title}' behaves as described\n"
    )
    if feedback:
        content += f"\n## Addressing feedback\n\n{feedback}\n"
    return ProviderResult(
        outcome="succeeded",
        stdout=f"[sim] prd run started: {title}\n[sim] wrote PRD draft",
        prd=content,
    )


def _breakdown_result(ctx: dict) -> ProviderResult:
    """US-2.33: a deterministic story split from the PRD's Goals bullets,
    honoring the breakdown mode. One story when 'single' or no goals parse."""
    title = ctx.get("title", "feature")
    prd = ctx.get("prd") or ""
    mode = ctx.get("breakdown_mode") or "automatic"
    goals: list[str] = []
    in_goals = False
    for line in prd.splitlines():
        s = line.strip()
        if s.lower().startswith("## goals"):
            in_goals = True
            continue
        if in_goals and s.startswith("## "):
            break
        if in_goals and s[:2] in ("- ", "* "):
            goals.append(s[2:].strip())
    if mode == "single" or not goals:
        stories = [
            {
                "title": title,
                "body": f"Implement '{title}' per the approved PRD.",
                "acceptance_criteria": [f"'{title}' works as the PRD describes"],
            }
        ]
    else:
        stories = [
            {
                "title": g[:80],
                "body": f"Deliver: {g}",
                "acceptance_criteria": [g],
            }
            for g in goals[:5]
        ]
    return ProviderResult(
        outcome="succeeded",
        stdout=f"[sim] breakdown run: {title} -> {len(stories)} story(ies)",
        stories=stories,
    )


def execute(ctx: dict, timeout_seconds: int = 120) -> ProviderResult:
    scenario = _scenario(ctx)
    retry = bool(ctx.get("feedback"))
    title = ctx.get("title", "issue")
    run_kind = ctx.get("run_kind") or "code"

    if scenario == "stuck":
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(1)
        return ProviderResult(
            outcome="failed",
            stdout="[sim] ...no output... (simulating a hang)",
            error=f"provider produced no result within {timeout_seconds}s (stuck)",
        )

    if scenario == "fail":
        return ProviderResult(
            outcome="failed",
            stdout="[sim] fatal: could not apply changes (simulated crash)",
            error="provider exited with code 1 (simulated failure)",
        )

    if run_kind == "prd":
        time.sleep(1)
        return _prd_result(ctx)

    if run_kind == "breakdown":
        time.sleep(1)
        return _breakdown_result(ctx)

    if run_kind == "plan":
        time.sleep(1)
        return _plan_result(ctx)

    time.sleep(2)
    branch = ctx.get("previous_branch") or f"factory/{_slug(title)}"
    pr_url = ctx.get("previous_pr_url") or f"simulated://pr/{_slug(title)}"
    stdout_lines = [
        f"[sim] provider started: {title}",
        f"[sim] repo: {ctx.get('repo_full_name')} (base: {ctx.get('default_branch')})",
    ]
    if retry:
        stdout_lines.append(f"[sim] retry with feedback: {ctx.get('feedback')}")
        stdout_lines.append(f"[sim] continuing on branch {ctx.get('previous_branch')}")
    stdout_lines += [
        "[sim] wrote changes, committed",
        f"[sim] pushed branch {branch}",
        f"[sim] opened PR {pr_url}",
    ]
    criteria = ctx.get("acceptance_criteria") or []
    first = criteria[0] if criteria else f"{title} works as described"
    test_case = {
        "title": f"Verify: {first}"[:200],
        "steps": (
            f"1. Open the app where “{title}” applies\n"
            f"2. Exercise the change\n"
            f"3. Check: {first}"
        ),
        "expected_result": first,
        "test_types": ["regression"],
        "environments": ["dev"],
    }
    stdout_lines.append(f"[sim] contributed test case: {test_case['title']}")

    return ProviderResult(
        outcome="succeeded",
        stdout="\n".join(stdout_lines),
        diff=_fake_diff(ctx, retry),
        branch_ref=branch,
        pr_url=pr_url,
        test_cases=[test_case],
    )
