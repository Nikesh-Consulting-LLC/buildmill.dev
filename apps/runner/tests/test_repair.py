"""US-10.8: self-repair loop — classification, bounded retry, recovery actions."""

import asyncio

from supervisor.modules.base import ModuleResult, RunContext, ShellResult
from supervisor.repair import apply_repair, classify, classify_fault, execute_with_repair


def _failed(err="", out=""):
    return ModuleResult(outcome="failed", error=err, stdout=out)


def test_classify_categories():
    assert classify(_failed(err="fatal: could not read; git clone failed")) == "reclone"
    assert classify(_failed(err="ModuleNotFoundError: No module named 'x'")) == "reinstall"
    assert classify(_failed(err="process killed after 120s")) == "wait"
    assert classify(_failed(err="assertion failed in business logic")) == "unrecoverable"


def test_classify_fault_runner_vs_work():
    assert classify_fault(_failed(err="git clone failed")) == "runner-fault"
    assert classify_fault(_failed(err="npm ERR! install")) == "runner-fault"
    assert classify_fault(_failed(err="acceptance criterion not met")) == "work-fault"


class FakeModule:
    name = "fake"
    capabilities = {"code"}

    def __init__(self, fail_times, err="git clone failed"):
        self.calls = 0
        self.fail_times = fail_times
        self.err = err

    async def execute(self, ctx, prim):
        self.calls += 1
        if self.calls <= self.fail_times:
            return ModuleResult(outcome="failed", error=self.err)
        return ModuleResult(outcome="succeeded", branch_ref="b")


class FakePrim:
    async def run_shell(self, argv, cwd=None, timeout=None, on_line=None):
        return ShellResult(list(argv), 0, "")

    async def run_api(self, *a, **k):
        raise AssertionError


def _ctx():
    return RunContext(run_id="r1", kind="code", context={"issue_id": "abc12345"})


def test_repair_retries_until_success(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    # A failed clone leaves a workspace folder behind; it must exist for the
    # reclone repair to have something to change (US-27.12: a repair that
    # changes nothing is not retried).
    workdir = tmp_path / "run-r1"
    workdir.mkdir()
    (workdir / "stale.txt").write_text("leftover from the failed clone")
    mod = FakeModule(fail_times=1, err="git clone failed")
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=2))
    assert res.outcome == "succeeded"
    assert mod.calls == 2  # failed once, repaired (reclone), succeeded


def test_repair_gives_up_on_unrecoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    mod = FakeModule(fail_times=99, err="logic assertion failed")
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=3))
    assert res.outcome == "failed"
    assert mod.calls == 1  # unrecoverable -> no retries
    assert "exhausted" in res.error


def test_reclone_escalates_invalidate_then_wipe(tmp_path, monkeypatch):
    """US-31.8: reclone no longer deletes on sight.

    The workspace is now per-project and KEPT between runs, so wiping first
    throws away installed dependencies for what is usually a stale source
    tree. Tier one invalidates the source record (folder and artifacts
    survive); tier two wipes, once there is nothing left to invalidate.
    """
    from supervisor import workspace

    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    ctx = RunContext(run_id="r1", kind="code", context={"project_id": "p-abc"})
    wd = workspace.ensure(workspace.workspace_for("p-abc", "r1"))
    (wd / "node_modules").mkdir()
    workspace.write_state(wd, base_sha="abc123")

    first = asyncio.run(apply_repair("reclone", ctx, FakePrim()))
    assert first and "invalidated" in first
    assert wd.exists() and (wd / "node_modules").exists()

    second = asyncio.run(apply_repair("reclone", ctx, FakePrim()))
    assert second and "including installed dependencies" in second
    assert not wd.exists()


# --------------------------------------------------------------------- US-54.1
def test_turn_limit_is_terminal_named_and_a_work_fault():
    from supervisor.repair import named_failure, turn_limit_hit

    r = _failed(
        err="the claude agent hit its turn ceiling (max_turns=40) after 41 "
        "turns — the session was ended mid-work after 247s"
    )
    assert turn_limit_hit(r)
    # Terminal: the same work under the same cap ends the same way, and the
    # one repair ever tried for it (reclone) destroys the work it produced.
    assert classify(r) == "unrecoverable"
    # A budget/config problem the manager fixes — not the box's fault.
    assert classify_fault(r) == "work-fault"
    named = named_failure(r)
    assert named is not None
    assert "max_turns" in named
    assert "not retried" in named


def test_turn_limit_beats_narration_keywords():
    """The real 2026-07-30 failure: the agent's narration mentioned the repo
    being checked out and git branches, and the old classifier read that as a
    broken checkout. The turn-limit verdict must win over every keyword."""
    r = _failed(
        err="the claude agent hit its turn ceiling (max_turns=40) after 41 "
        "turns — the session was ended mid-work after 247s. Its last output:\n"
        "```\nThe repo is already checked out locally. Let me check the "
        "branch and clone conventions before implementing.\n```",
    )
    assert classify(r) == "unrecoverable"


def test_turn_limit_run_is_not_repaired(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    mod = FakeModule(
        fail_times=99,
        err="the claude agent hit its turn ceiling (max_turns=40) after 41 turns",
    )
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=3))
    assert res.outcome == "failed"
    assert mod.calls == 1  # no retry, no reclone — the workspace survives
    assert "max_turns" in res.error


# --------------------------------------------------------------------- US-54.2
def test_agent_narration_never_triggers_destructive_repair():
    """Words an agent uses while working ("checkout", "branch", "clone",
    "install", "no such file") must not pick reclone/reinstall — only failure
    phrasings may. 2026-07-30: reclone chosen off narration deleted 41 turns
    of written code."""
    narration = _failed(
        err="the claude CLI exited 1 after 251s. Its last output:\n"
        "```\nThe repo is already checked out locally on the story branch. "
        "Let me clone the conventions from ProfilePage and install the "
        "imports. No such file exists yet for ai_config, so I will create "
        "it.\n```",
    )
    assert classify(narration) == "unrecoverable"

    # The genuine failure phrasings still repair.
    assert classify(_failed(err="git clone failed: fatal: repository not found")) == "reclone"
    assert classify(_failed(err="fatal: Unable to create '.git/index.lock': File exists")) == "reclone"
    assert classify(_failed(err="ModuleNotFoundError: No module named 'httpx'")) == "reinstall"
    assert classify(_failed(err="sh: 1: claude: command not found")) == "reinstall"


# --------------------------------------------------------------------- US-52.4
def test_usage_cap_is_terminal_named_and_a_runner_fault():
    from supervisor.repair import named_failure, usage_cap_hit

    r = _failed(
        err="the claude CLI exited 1 after 8s. Its last output:\n\n"
        "```\nClaude AI usage limit reached|1722268800\n```"
    )
    assert usage_cap_hit(r)
    # Terminal: the same prompt cannot succeed before the cap resets, so a
    # retry only burns an attempt against a wall.
    assert classify(r) == "unrecoverable"
    # And an agent-side resource condition, not a defect in the story.
    assert classify_fault(r) == "runner-fault"
    named = named_failure(r)
    assert named is not None
    assert "usage cap" in named
    assert "not retried" in named


def test_provider_rate_limit_still_waits():
    # A provider 429 is transient and keeps its `wait` classification — only
    # the subscription cap phrasing is terminal.
    assert classify(_failed(err="429 rate limit exceeded, please retry")) == "wait"


# --------------------------------------------------------------------- US-63.x
def test_wait_is_time_boxed_not_count_boxed(monkeypatch):
    """A connection failure (the class a factory-api restart during a deploy
    produces) must not be capped by max_attempts the way reclone/reinstall
    are — it gets its own time budget and keeps retrying past max_attempts
    as long as that budget remains."""
    import supervisor.repair as repair

    monkeypatch.setattr(repair, "_WAIT_RETRY_SECONDS", 0)
    monkeypatch.setattr(repair, "_WAIT_MAX_SECONDS", 100)
    mod = FakeModule(fail_times=5, err="connection reset by peer")
    # max_attempts=1 would stop reclone/reinstall after one try; wait must
    # still succeed on its 6th call because 5 * _WAIT_RETRY_SECONDS (0) never
    # exhausts a 100s budget.
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=1))
    assert res.outcome == "succeeded"
    assert mod.calls == 6


def test_wait_gives_up_once_its_budget_is_spent(monkeypatch):
    import supervisor.repair as repair

    # Small but nonzero, so the budget check (>=) actually trips instead of
    # looping forever, while keeping the test's real wall-clock time trivial.
    monkeypatch.setattr(repair, "_WAIT_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(repair, "_WAIT_MAX_SECONDS", 0.02)
    mod = FakeModule(fail_times=99, err="connection refused")
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=2))
    assert res.outcome == "failed"
    # budget(0.02) / retry(0.01) = 2 retries fit (checked at 0s and 0.01s,
    # both < 0.02), the 3rd check (at 0.02s) is >= budget and gives up
    # without a 3rd retry call: 1 initial + 2 retries = 3 calls.
    assert mod.calls == 3
    assert "Retried every 0.01s for 0.02s" in res.error
    assert "stale-heartbeat sweep" in res.error


def test_wait_does_not_consume_the_reclone_budget(monkeypatch, tmp_path):
    """A run that hits a connection failure and THEN a reclone-worthy failure
    must still get its full reclone budget — the wait retry above must not
    have silently eaten into `attempts`."""
    monkeypatch.setenv("RUNNER_WORKSPACE", str(tmp_path))
    import supervisor.repair as repair

    monkeypatch.setattr(repair, "_WAIT_RETRY_SECONDS", 0)
    monkeypatch.setattr(repair, "_WAIT_MAX_SECONDS", 100)

    workdir = tmp_path / "run-r1"
    workdir.mkdir()
    (workdir / "stale.txt").write_text("leftover from the failed clone")

    class TwoStageModule:
        name = "fake"
        capabilities = {"code"}

        def __init__(self):
            self.calls = 0

        async def execute(self, ctx, prim):
            self.calls += 1
            if self.calls == 1:
                return ModuleResult(outcome="failed", error="connection reset by peer")
            if self.calls == 2:
                return ModuleResult(outcome="failed", error="git clone failed")
            return ModuleResult(outcome="succeeded", branch_ref="b")

    mod = TwoStageModule()
    res = asyncio.run(execute_with_repair(mod, _ctx(), FakePrim(), max_attempts=1))
    assert res.outcome == "succeeded"
    assert mod.calls == 3  # wait retry (1) + reclone attempt (1) + success
