"""US-43.7: the runner knows hand-back shapes, not run kinds.

Two live failures produced this — `no enabled module can do 'guidelines' work`
and the same for `test`. `CLIModule.capabilities` had not moved since it was
written, so five shipped run kinds were refused; and the prompt had exactly two
branches, so simply widening the set would have told a guidelines run to
implement a change and let the harness commit it.
"""

import pytest

from supervisor import modules
from supervisor.modules.base import RunContext
from supervisor.modules.cli_base import (
    HANDBACK_FILES,
    HANDBACK_MCP,
    HANDBACK_WORKTREE,
    _sections,
    _task_section,
    handback_shape,
)
from supervisor.workloop import MCP_REQUIRED_KINDS, module_can_do

DISPATCHABLE = (
    "prd",
    "breakdown",
    "plan",
    "code",
    "test",
    "release",
    "deploy",
    "guidelines",
    "elaborate",
)


@pytest.mark.parametrize("kind", DISPATCHABLE)
def test_the_claude_module_can_do_every_dispatchable_kind(kind):
    ok, why = module_can_do("claude", kind)
    assert ok, why


def test_an_unknown_kind_gets_the_safe_shape():
    """A kind this runner has never heard of must not be told to leave a
    working tree for the harness to commit. Submitting over MCP is
    wrong-but-harmless, and the factory's own instruction corrects it."""
    assert handback_shape("something-invented-later") == HANDBACK_MCP


def test_the_three_shapes_are_assigned_as_the_story_says():
    assert handback_shape("plan") == HANDBACK_FILES
    assert handback_shape("code") == HANDBACK_WORKTREE
    for kind in ("prd", "breakdown", "test", "release", "deploy",
                 "guidelines", "elaborate"):
        assert handback_shape(kind) == HANDBACK_MCP


def test_the_plan_shape_still_names_the_files_the_harness_reads():
    task = _task_section("plan")
    for name in ("plan.md", "test_plan.md", "test_cases.json"):
        assert name in task
    assert "Do NOT modify any project file" in task


def test_the_code_shape_still_forbids_committing_and_branching():
    task = _task_section("code")
    assert "uncommitted" in task
    assert "Do not create branches or open PRs" in task
    assert "refused hand-back is NOT a finished run" in task


def test_the_mcp_shape_forbids_writing_and_committing():
    """The bug this story fixes, stated as a test: a guidelines run must never
    be told to implement a change and let the harness commit it."""
    task = _task_section("guidelines")
    assert "commit nothing" in task
    assert "Write no project file" in task
    assert "uncommitted" not in task
    assert "harness commits" not in task


def test_the_runner_no_longer_describes_the_task_itself():
    """The task text belongs to the factory: get_work_context returns
    get_worker_instruction(project, kind), which a manager can edit per
    project. Repeating a guess here is what made a manager's edit lose to
    hardcoded prose."""
    for kind in DISPATCHABLE:
        task = _task_section(kind)
        assert "## Handing back" in task
        assert "## Your task" not in task
    assert "get_work_context" in _task_section("guidelines")


def test_mcp_required_covers_the_repo_reading_kinds():
    for kind in ("code", "plan", "test", "guidelines", "elaborate"):
        assert kind in MCP_REQUIRED_KINDS
    # These answer from context and need no repository — unchanged.
    assert "prd" not in MCP_REQUIRED_KINDS
    assert "breakdown" not in MCP_REQUIRED_KINDS


def test_sim_keeps_its_own_narrower_set():
    # It fabricates results; it genuinely cannot do the kinds it does not
    # simulate, so widening it would be a lie rather than a fix.
    assert modules.get("sim").capabilities == {"code", "plan", "prd", "breakdown"}
    ok, why = module_can_do("sim", "guidelines")
    assert not ok and "does not do" in why


def test_a_guidelines_prompt_is_built_without_a_kind_branch():
    ctx = RunContext(
        run_id="r1",
        kind="guidelines",
        context={"title": "Refresh project guidelines", "type": "chore"},
    )
    prompt = _sections(ctx, "guidelines", mcp=True)
    assert "get_work_context" in prompt
    assert "commit nothing" in prompt
    assert "Implement the change" not in prompt


# ---------------------------------------------------------------- us-96.8
# The hand-back speaks with one voice: the transport actually in play, not
# the run kind alone, decides the wording — and the harness never commits a
# tree whose agent already handed back over MCP.


def test_an_mcp_transport_code_run_never_reads_the_worktree_wording():
    task = _task_section("code", shape=HANDBACK_MCP)
    assert "uncommitted" not in task
    assert ".factory-out" not in task
    assert task.count("submit_changeset") == 1
    # AC2: exactly one route for test cases — the submit's own field.
    assert "test_cases" in task and "test_cases.json" not in task
    # AC4: the echo check is part of the contract.
    assert "echo" in task


def test_the_worktree_wording_is_unchanged_for_the_default_transport():
    task = _task_section("code")
    assert "uncommitted" in task
    assert "test_cases.json" in task


def test_the_interactive_module_declares_mcp_handback_for_code():
    from supervisor.modules.interactive import InteractiveModule

    m = InteractiveModule()
    assert m.handback_shape_for("code") == HANDBACK_MCP
    # Everything else keeps the kind's own shape.
    assert m.handback_shape_for("plan") == HANDBACK_FILES
    assert m.handback_shape_for("guidelines") == HANDBACK_MCP


def test_the_generic_mcp_wording_still_forbids_project_files():
    """A guidelines/elaborate run must keep 'write no project file' — only
    the code-over-MCP variant edits the checkout."""
    task = _task_section("guidelines", shape=HANDBACK_MCP)
    assert "Write no project file" in task


def test_the_sweep_names_files_that_never_reached_the_branch(monkeypatch):
    """us-96.8 AC5: dirty paths absent from the remote branch's diff are the
    files an MCP hand-back forgot; factory scratch is legitimately local."""
    import asyncio
    from pathlib import Path

    from supervisor import gitwork

    async def fake_git(prim, args, cwd=None, timeout=300):
        if args[0] == "fetch":
            return ""
        if args[0] == "diff":
            return "src/app.py\nsrc/lib.py\n"
        if args[0] == "status":
            return (
                " M src/app.py\n"
                " M src/forgotten.py\n"
                "?? .factory-out/test_cases.json\n"
                "?? .grok/config.toml\n"
            )
        return ""

    monkeypatch.setattr(gitwork, "git", fake_git)
    out = asyncio.run(gitwork.unsubmitted_paths(None, Path("."), "feat/x"))
    assert out == ["src/forgotten.py"]
