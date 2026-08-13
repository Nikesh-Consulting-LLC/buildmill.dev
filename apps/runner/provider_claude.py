"""Real Claude Code provider (US-1.15).

Same contract as provider_sim: execute(input_context) -> ProviderResult.
The runner selects it with RUNNER_PROVIDER=claude; the simulator stays
the default until this story is accepted.

Phase-3 shape (us-3.2/3.5/3.8): the provider clones the project through
the FACTORY git remote — HTTP Basic with the runner's own worker token,
no GitHub credentials on this machine — works on the context's
factory/issue-<id> branch, invokes the Claude Code CLI headless,
commits and pushes, and returns the branch ref. The factory verifies
the branch and opens the PR at submit; a worker never talks to GitHub.

Artifacts the CLI writes for the harness land in .factory-out/ (never
committed): plan.md + test_plan.md for plan runs, test_cases.json for
agent-written test cases.

Env:
    FACTORY_WORKER_TOKEN   git auth for the factory remote
    RUNNER_WORKSPACE       checkout root (default: ./workspace)
    RUNNER_CLAUDE_CMD      CLI command (default: claude)
    RUNNER_CLAUDE_ARGS     extra args (default: --permission-mode bypassPermissions)
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from provider_sim import ProviderResult

OUT_DIR = ".factory-out"


def _split_cmd(value: str) -> list[str]:
    return shlex.split(value, posix=(os.name != "nt"))


CLAUDE_CMD = _split_cmd(os.environ.get("RUNNER_CLAUDE_CMD", "claude"))
CLAUDE_ARGS = _split_cmd(
    os.environ.get("RUNNER_CLAUDE_ARGS", "--permission-mode bypassPermissions")
)


def _workspace() -> Path:
    root = os.environ.get("RUNNER_WORKSPACE") or str(
        Path(__file__).resolve().parent / "workspace"
    )
    Path(root).mkdir(parents=True, exist_ok=True)
    return Path(root)


def _authed_url(remote: str, token: str) -> str:
    """Inject the worker token as Basic auth into an http(s) factory
    remote; other schemes (file:// in tests) pass through untouched."""
    parts = urlsplit(remote)
    if parts.scheme not in ("http", "https") or not token:
        return remote
    host = parts.netloc.rsplit("@", 1)[-1]  # drop any existing creds
    return urlunsplit(
        (parts.scheme, f"worker:{quote(token, safe='')}@{host}", *parts[2:])
    )


def _git(args: list[str], cwd: Path | None = None, timeout: int = 300) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "Software Factory",
            "GIT_AUTHOR_EMAIL": "factory@localhost",
            "GIT_COMMITTER_NAME": "Software Factory",
            "GIT_COMMITTER_EMAIL": "factory@localhost",
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:400]}")
    return proc.stdout


def _prepare_checkout(ctx: dict, remote: str) -> Path:
    issue = str(ctx.get("issue_id") or "work")
    workdir = _workspace() / f"issue-{issue[:8]}"
    if (workdir / ".git").exists():
        _git(["remote", "set-url", "origin", remote], cwd=workdir)
        _git(["fetch", "origin"], cwd=workdir)
    else:
        if workdir.exists():
            shutil.rmtree(workdir)
        _git(["clone", remote, str(workdir)])
    return workdir


def _checkout_branch(workdir: Path, branch: str, default_branch: str) -> None:
    """Continue the branch when it exists upstream (retry), otherwise cut
    it from the default branch head."""
    remote_heads = _git(["ls-remote", "--heads", "origin", branch], cwd=workdir)
    if remote_heads.strip():
        _git(["checkout", "-B", branch, f"origin/{branch}"], cwd=workdir)
    else:
        base = f"origin/{default_branch}"
        try:
            _git(["checkout", "-B", branch, base], cwd=workdir)
        except RuntimeError:
            _git(["checkout", "-B", branch], cwd=workdir)  # empty repo


def _build_prompt(ctx: dict, run_kind: str) -> str:
    sections: list[str] = [
        "You are completing a Software Factory work item inside this git checkout.",
        f"# {ctx.get('title', 'Work item')} ({ctx.get('type', 'story')})",
    ]
    for key, heading in (
        ("story", "Story"),
        ("acceptance_criteria", "Acceptance criteria"),
        ("plan", "Approved implementation plan"),
        ("test_plan", "Approved test plan"),
        ("prd", "PRD context"),
        ("guidelines", "Project guidelines"),
        ("learnings", "Project learnings"),
        ("feedback", "Rejection feedback — this is a retry; address it"),
    ):
        value = ctx.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = "\n".join(f"- {v}" for v in value)
        sections.append(f"## {heading}\n{value}")

    if run_kind == "plan":
        sections.append(
            "## Your task\n"
            "Produce a plan, not code. Do NOT modify any project file.\n"
            f"Write the implementation plan (markdown) to {OUT_DIR}/plan.md and "
            f"the test plan to {OUT_DIR}/test_plan.md. Optionally propose "
            f"structured test cases in {OUT_DIR}/test_cases.json as a JSON array "
            "of {title, steps, expected_result, test_types, environments}."
        )
    else:
        sections.append(
            "## Your task\n"
            "Implement the change in this checkout, honoring the approved plan "
            "and acceptance criteria. Leave your changes uncommitted — the "
            "harness commits and pushes. Do not create branches, do not push, "
            "and do not open pull requests.\n"
            f"Write test cases a human should run to {OUT_DIR}/test_cases.json "
            "as a JSON array of {title, steps, expected_result, test_types, "
            "environments}."
        )
    return "\n\n".join(sections)


def _build_prd_prompt(ctx: dict) -> str:
    sections = [
        "Write a product requirements document (PRD) for this feature. "
        "Respond with ONLY markdown containing exactly these headings, in "
        "this order: ## Problem, ## Goals, ## Out of scope, "
        "## Acceptance criteria. No other text before or after.",
        f"# {ctx.get('title', 'Feature')}",
    ]
    for key, heading in (
        ("story", "Raw idea"),
        ("previous_prd", "Prior draft"),
        ("feedback", "Send-back feedback — address it"),
        ("guidelines", "Project guidelines"),
        ("learnings", "Project learnings"),
    ):
        value = ctx.get(key)
        if value:
            sections.append(f"## Context: {heading}\n{value}")
    return "\n\n".join(sections)


def _execute_prd(ctx: dict, timeout_seconds: int) -> ProviderResult:
    """No git checkout — the CLI is invoked in a scratch dir purely as an
    LLM call, and its stdout *is* the PRD markdown."""
    scratch = _workspace() / "prd-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    ok, stdout = _run_cli(_build_prd_prompt(ctx), scratch, timeout_seconds)
    if not ok:
        return ProviderResult(
            outcome="failed",
            stdout=stdout[-20000:],
            error="Claude Code CLI failed or timed out",
        )
    content = stdout.strip()
    if not content:
        return ProviderResult(
            outcome="failed", stdout=stdout[-20000:], error="CLI produced no PRD content"
        )
    return ProviderResult(outcome="succeeded", stdout=stdout[-20000:], prd=content)


def _build_breakdown_prompt(ctx: dict) -> str:
    mode = ctx.get("breakdown_mode") or "automatic"
    mode_line = {
        "single": "Produce EXACTLY ONE story covering the whole PRD.",
        "multiple": "Produce a detailed split — several focused stories.",
    }.get(mode, "Split into focused, independently deliverable stories, "
          "ordered by dependency.")
    sections = [
        "Break this feature's approved PRD into engineering stories. "
        + mode_line
        + ' Respond with ONLY a JSON array; each element is {"title": '
        'string, "body": string, "acceptance_criteria": [string]}. No prose '
        "before or after.",
        f"# {ctx.get('title', 'Feature')}",
    ]
    for key, heading in (
        ("prd", "Approved PRD"),
        ("breakdown_instructions", "Manager instructions — honor them"),
        ("guidelines", "Project guidelines"),
        ("learnings", "Project learnings"),
    ):
        value = ctx.get(key)
        if value:
            sections.append(f"## Context: {heading}\n{value}")
    return "\n\n".join(sections)


def _parse_stories(text: str) -> list:
    """Extract the JSON array of stories from CLI stdout, tolerant of code
    fences and surrounding prose."""
    import re

    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    start, end = raw.find("["), raw.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    out = []
    for s in data if isinstance(data, list) else []:
        if isinstance(s, dict) and str(s.get("title") or "").strip():
            out.append(
                {
                    "title": str(s.get("title")).strip(),
                    "body": str(s.get("body") or "").strip(),
                    "acceptance_criteria": [
                        str(a) for a in (s.get("acceptance_criteria") or [])
                    ],
                }
            )
    return out


def _execute_breakdown(ctx: dict, timeout_seconds: int) -> ProviderResult:
    """No git checkout — the CLI is an LLM call whose stdout is a JSON array
    of stories (US-2.33)."""
    scratch = _workspace() / "breakdown-scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    ok, stdout = _run_cli(
        _build_breakdown_prompt(ctx), scratch, timeout_seconds
    )
    if not ok:
        return ProviderResult(
            outcome="failed",
            stdout=stdout[-20000:],
            error="Claude Code CLI failed or timed out",
        )
    stories = _parse_stories(stdout)
    if not stories:
        return ProviderResult(
            outcome="failed",
            stdout=stdout[-20000:],
            error="CLI produced no parseable stories",
        )
    return ProviderResult(
        outcome="succeeded", stdout=stdout[-20000:], stories=stories
    )


def _run_cli(prompt: str, workdir: Path, timeout_seconds: int) -> tuple[bool, str]:
    """Invoke the CLI headless; on wall-clock timeout, kill it and return
    the partial stdout — mirroring the simulator's 'stuck' behavior."""
    cmd = [*CLAUDE_CMD, "-p", prompt, "--output-format", "text", *CLAUDE_ARGS]
    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        out, _ = proc.communicate(timeout=timeout_seconds)
        return proc.returncode == 0, out or ""
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return False, (out or "") + f"\n[runner] killed after {timeout_seconds}s"


def _read_out_file(workdir: Path, name: str) -> str | None:
    path = workdir / OUT_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return None


def _read_test_cases(workdir: Path) -> list | None:
    raw = _read_out_file(workdir, "test_cases.json")
    if not raw:
        return None
    try:
        cases = json.loads(raw)
        return cases if isinstance(cases, list) else None
    except json.JSONDecodeError:
        return None


def execute(ctx: dict, timeout_seconds: int = 1200) -> ProviderResult:
    run_kind = ctx.get("run_kind") or "code"
    if run_kind == "prd":
        return _execute_prd(ctx, timeout_seconds)
    if run_kind == "breakdown":
        return _execute_breakdown(ctx, timeout_seconds)

    token = os.environ.get("FACTORY_WORKER_TOKEN", "")
    remote = ctx.get("git_remote_url") or ""
    branch = ctx.get("branch_name") or ctx.get("previous_branch") or ""
    default_branch = ctx.get("default_branch") or "main"

    if not remote or not branch:
        return ProviderResult(
            outcome="failed",
            error="context is missing git_remote_url or branch_name",
        )

    try:
        workdir = _prepare_checkout(ctx, _authed_url(remote, token))
        _checkout_branch(workdir, branch, default_branch)
        out_dir = workdir / OUT_DIR
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir()

        ok, stdout = _run_cli(
            _build_prompt(ctx, run_kind), workdir, timeout_seconds
        )
        if not ok:
            return ProviderResult(
                outcome="failed",
                stdout=stdout[-20000:],
                error="Claude Code CLI failed or timed out",
            )

        if run_kind == "plan":
            plan = _read_out_file(workdir, "plan.md")
            test_plan = _read_out_file(workdir, "test_plan.md")
            if not plan:
                return ProviderResult(
                    outcome="failed",
                    stdout=stdout[-20000:],
                    error=f"CLI wrote no {OUT_DIR}/plan.md",
                )
            return ProviderResult(
                outcome="succeeded",
                stdout=stdout[-20000:],
                plan=plan,
                test_plan=test_plan,
                test_cases=_read_test_cases(workdir),
            )

        test_cases = _read_test_cases(workdir)
        shutil.rmtree(out_dir, ignore_errors=True)  # never committed

        status = _git(["status", "--porcelain"], cwd=workdir)
        head_before = None
        remote_head = _git(["ls-remote", "--heads", "origin", branch], cwd=workdir)
        if not status.strip() and remote_head.strip():
            # retry with everything already pushed still counts as work
            pass
        elif not status.strip():
            return ProviderResult(
                outcome="failed",
                stdout=stdout[-20000:],
                error="CLI made no changes to the checkout",
            )
        if status.strip():
            _git(["add", "-A"], cwd=workdir)
            _git(
                ["commit", "-m", f"{ctx.get('title', 'factory change')} (factory)"],
                cwd=workdir,
            )
        _git(["push", "origin", branch], cwd=workdir, timeout=600)

        return ProviderResult(
            outcome="succeeded",
            stdout=stdout[-20000:],
            branch_ref=branch,
            test_cases=test_cases,
        )
    except Exception as e:  # report, never vanish
        return ProviderResult(outcome="failed", error=f"provider error: {e}")


if __name__ == "__main__":  # smoke: print the resolved CLI
    print("claude cmd:", CLAUDE_CMD, CLAUDE_ARGS, file=sys.stderr)
