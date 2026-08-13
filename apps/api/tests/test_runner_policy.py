"""US-10.7: runner shell policy evaluation."""

from app.runner_policy import evaluate


def test_default_allows_everything():
    allow, reason = evaluate({}, ["rm", "-rf", "build"])
    assert allow is True and reason is None


def test_deny_mode_blocks_all():
    allow, reason = evaluate({"mode": "deny"}, ["ls"])
    assert allow is False and "denies" in reason


def test_deny_patterns_block_matching_commands():
    policy = {"mode": "allow", "deny_patterns": [r"\bcurl\b", r"rm\s+-rf\s+/"]}
    assert evaluate(policy, ["curl", "http://x"])[0] is False
    assert evaluate(policy, ["git", "status"])[0] is True


def test_require_approval_blocks_unless_allowlisted():
    policy = {"mode": "require-approval", "allow_patterns": [r"^git "]}
    assert evaluate(policy, ["git", "status"])[0] is True
    blocked, reason = evaluate(policy, ["python", "script.py"])
    assert blocked is False and "approval" in reason
