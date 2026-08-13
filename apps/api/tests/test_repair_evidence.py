"""US-27.12: repair reads the error before it theorizes.

On 2026-07-26 the CLI printed the exact cause on stderr:

    There's an issue with the selected model (openai/gpt-oss-120b). It may not
    exist or you may not have access to it.

and the brain's diagnosis — recorded on the run, copied into a runner_incidents
row and into a notification — was:

    The CLI timed out because the command exceeded the default execution limit
    — extend the timeout (e.g. --timeout 300) or optimize the command to run
    faster.

Nothing timed out; the command exited in eight seconds. The brain then spent
two repair attempts on its timeout theory. These tests use those exact strings
as fixtures (runs 802506c9 and a7244f6c).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# The supervisor ships from apps/runner and is not on the API's path.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "runner"))

from supervisor import repair  # noqa: E402
from supervisor.modules.base import ModuleResult  # noqa: E402


REAL_CLI_OUTPUT = (
    "There's an issue with the selected model (openai/gpt-oss-120b). "
    "It may not exist or you may not have access to it."
)
REAL_DIAGNOSIS = (
    "The CLI timed out because the command exceeded the default execution "
    "limit — extend the timeout (e.g. `--timeout 300`) or optimize the "
    "command to run faster."
)


def _failure(**over):
    result = ModuleResult(
        outcome="failed",
        stdout=REAL_CLI_OUTPUT,
        error=f"the claude CLI exited 1 after 8s. Its last output:\n{REAL_CLI_OUTPUT}",
        exit_code=1,
        duration_seconds=8.0,
    )
    for k, v in over.items():
        setattr(result, k, v)
    return result


# --- the named causes ------------------------------------------------------


def test_a_model_rejection_is_named_not_diagnosed():
    named = repair.named_failure(_failure())
    assert named
    assert "model this agent is routed to" in named
    assert "configuration problem" in named


def test_an_auth_failure_is_named():
    r = _failure(stdout="authentication_error: invalid api key", error="exited 1")
    assert "credential" in (repair.named_failure(r) or "")


def test_a_missing_binary_is_named():
    r = _failure(stdout="bash: claude: command not found", error="exited 127")
    assert "not installed" in (repair.named_failure(r) or "")


def test_an_ambiguous_failure_gets_no_named_cause():
    r = _failure(stdout="Traceback: ValueError in user code", error="exited 1")
    assert repair.named_failure(r) is None


# --- the contradiction heuristics ------------------------------------------


def test_a_timeout_claim_is_dropped_on_a_fast_non_zero_exit():
    """The 2026-07-26 invention, refuted by the run's own numbers."""
    why = repair.diagnosis_contradicts(_failure(), REAL_DIAGNOSIS)
    assert why
    assert "did not time out" in why


def test_a_timeout_claim_survives_when_the_command_really_was_killed():
    r = _failure(exit_code=None, duration_seconds=600.0, stdout="killed after 600s")
    assert repair.diagnosis_contradicts(r, REAL_DIAGNOSIS) is None


def test_a_network_claim_is_dropped_when_nothing_mentions_the_network():
    why = repair.diagnosis_contradicts(
        _failure(), "The runner could not reach the network."
    )
    assert why and "network" in why


def test_a_network_claim_survives_when_the_output_supports_it():
    r = _failure(stdout="curl: (6) Could not resolve host: api.example.com")
    assert repair.diagnosis_contradicts(r, "a network problem") is None


def test_a_permission_claim_is_dropped_without_permission_text():
    why = repair.diagnosis_contradicts(_failure(), "permission denied on the workdir")
    assert why and "permissions" in why


def test_an_unrelated_diagnosis_is_left_alone():
    assert (
        repair.diagnosis_contradicts(_failure(), "the model id is wrong") is None
    )


# --- the loop --------------------------------------------------------------


class FakeModule:
    """Fails identically every time, like the real one did."""

    def __init__(self):
        self.runs = 0

    async def execute(self, ctx, prim):
        self.runs += 1
        return _failure()


class Ctx:
    run_id = "802506c9"
    context = {"issue_id": "issue-1"}


def _run(coro):
    return asyncio.run(coro)


def test_repair_that_changes_nothing_is_not_attempted_twice(monkeypatch):
    """Both attempts on 2026-07-26 ran the identical command and got the
    identical rejection. Two identical failures are not resilience."""
    module = FakeModule()
    monkeypatch.setattr(repair, "classify", lambda r: "reclone")

    async def nothing_changed(action, ctx, prim):
        return None

    monkeypatch.setattr(repair, "apply_repair", nothing_changed)

    result = _run(
        repair.execute_with_repair(module, Ctx(), None, max_attempts=2)
    )
    assert module.runs == 1, "the command must not be re-run unchanged"
    assert "Repair had nothing to change" in result.error
    assert REAL_CLI_OUTPUT in result.error


def test_the_evidence_comes_first_and_the_diagnosis_is_labelled(monkeypatch):
    module = FakeModule()
    monkeypatch.setattr(repair, "classify", lambda r: "unrecoverable")
    # a genuinely ambiguous failure — where a brain reading earns its place
    monkeypatch.setattr(repair, "named_failure", lambda r: None)

    async def diagnose(result):
        return "the model id is not offered by the provider"

    result = _run(
        repair.execute_with_repair(
            module, Ctx(), None, max_attempts=2, diagnose=diagnose
        )
    )
    # the command's own output leads; anything brain-authored comes after it
    assert result.error.index(REAL_CLI_OUTPUT) < result.error.find("Diagnosis")


def test_a_named_cause_replaces_the_brain_entirely(monkeypatch):
    """A model rejection is recognisable. Nothing needs to be inferred, so
    nothing is — the brain is not even asked."""
    module = FakeModule()
    monkeypatch.setattr(repair, "classify", lambda r: "unrecoverable")
    asked = []

    async def diagnose(result):
        asked.append(True)
        return REAL_DIAGNOSIS

    result = _run(
        repair.execute_with_repair(
            module, Ctx(), None, max_attempts=2, diagnose=diagnose
        )
    )
    assert not asked
    assert "Cause:" in result.error
    assert "timed out" not in result.error


def test_a_contradicted_diagnosis_never_reaches_the_run(monkeypatch):
    """This is the whole story: the invention must not be what gets stored."""
    module = FakeModule()
    monkeypatch.setattr(repair, "classify", lambda r: "unrecoverable")
    monkeypatch.setattr(repair, "named_failure", lambda r: None)

    async def diagnose(result):
        return REAL_DIAGNOSIS

    result = _run(
        repair.execute_with_repair(
            module, Ctx(), None, max_attempts=2, diagnose=diagnose
        )
    )
    assert "exceeded the default execution limit" not in result.error
    assert "discarded" in result.error
    assert REAL_CLI_OUTPUT in result.error


def test_every_attempt_is_reported_to_the_caller(monkeypatch):
    module = FakeModule()
    monkeypatch.setattr(repair, "classify", lambda r: "reclone")
    seen: list[tuple] = []

    async def changed_something(action, ctx, prim):
        return "removed the checkout"

    monkeypatch.setattr(repair, "apply_repair", changed_something)
    _run(
        repair.execute_with_repair(
            module,
            Ctx(),
            None,
            max_attempts=2,
            on_attempt=lambda *a: seen.append(a),
        )
    )
    assert len(seen) == 2
    assert seen[0][1] == "reclone"
    assert seen[0][2] == "removed the checkout"


@pytest.mark.parametrize("action", ["reclone", "reinstall", "wait"])
def test_classify_still_maps_the_old_signals(action):
    """The pre-existing classification is untouched — this story changes what
    is REPORTED, not what is attempted."""
    text = {"reclone": "index.lock", "reinstall": "no module named x", "wait": "rate limit"}[
        action
    ]
    assert repair.classify(ModuleResult(outcome="failed", error=text)) == action
