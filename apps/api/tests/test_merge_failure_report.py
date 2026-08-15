"""US-98.5 AC3/AC5: a merge that cannot finish says so, usefully.

Before `report_merge_failure` an MCP agent holding a merge had no way to end
it honestly: `release_work` puts the claim back in the pool saying nothing,
and `request_clarification` parks the run waiting for a human. Neither is
"this cannot be done, here is why" — so a merge that hit a real conflict
looked, from the manager's side, like an agent that wandered off.

The text is pinned rather than the plumbing, because the text IS the feature:
it reaches the retry run verbatim as feedback, so anything it leaves out the
next agent rediscovers from scratch.
"""

from __future__ import annotations

from app.factory_mcp import _merge_failure_report


def _run(branches=("feat/a", "feat/b"), kind="merge"):
    return {
        "kind": kind,
        "_repo_ic": {
            "default_branch": "main",
            "merge_base": {"branch": "main", "head_sha": "base0"},
            "merge_branches": [
                {"branch": b, "head_sha": f"sha-{b}"} for b in branches
            ],
        },
    }


def test_a_named_failure_produces_a_usable_report():
    text, err = _merge_failure_report(
        _run(), "feat/a", ["src/app.ts", "src/db.ts"], "took both hunks; the "
        "types then contradicted"
    )
    assert err is None
    assert "feat/a" in text
    assert "src/app.ts" in text and "src/db.ts" in text
    assert "took both hunks" in text
    assert "main" in text


def test_the_report_says_nothing_else_was_landed():
    """The manager must not read a failure as 'one branch is left to do'."""
    text, _ = _merge_failure_report(_run(), "feat/a", [], "tried")
    assert "all-or-nothing" in text
    assert "1 branch(es) were not landed" in text


def test_missing_paths_are_stated_not_faked():
    text, err = _merge_failure_report(_run(), "feat/a", None, "tried a rebase")
    assert err is None
    assert "No specific paths were named" in text


def test_a_branch_outside_the_run_is_refused():
    _, err = _merge_failure_report(_run(), "feat/elsewhere", [], "tried")
    assert err is not None
    assert "not one of this run's branches" in err["error"]
    assert "feat/a" in err["hint"]


def test_a_blank_branch_is_refused():
    _, err = _merge_failure_report(_run(), "   ", [], "tried")
    assert err is not None
    assert "which branch" in err["error"]


def test_a_blank_attempt_is_refused():
    """"It conflicted" costs the next agent the same discovery again."""
    _, err = _merge_failure_report(_run(), "feat/a", [], "   ")
    assert err is not None
    assert "what you tried" in err["error"]


def test_a_non_merge_run_cannot_report_a_merge_failure():
    _, err = _merge_failure_report(_run(kind="code"), "feat/a", [], "tried")
    assert err is not None
    assert "not a merge" in err["error"]


def test_blank_paths_are_dropped_rather_than_rendered_empty():
    text, err = _merge_failure_report(
        _run(), "feat/a", ["", "   ", "src/real.ts"], "tried"
    )
    assert err is None
    assert "src/real.ts" in text
    assert "- ``" not in text
