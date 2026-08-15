"""US-100.6: an agent proposes a version, and the factory stays safe.

CLAUDE.md says "The factory computes the version — you never hand-pick one
mid-flight." Right when versioning was arithmetic; wrong once the rules live
in the project's Agent Instructions, because then a project has written down
how it wants to be versioned and the participant reading that document is not
allowed to act on it.

The validator deliberately does NOT check the format. Validating against
`YYYY.MM.DD.N` would re-impose exactly the constraint this story removes — a
project may now define semantic versioning, a train, or anything else. What
is checked is what is true of a version in ANY scheme.
"""

from __future__ import annotations

from app.factory_mcp import version_proposal_error


def test_a_reasoned_proposal_is_accepted():
    assert version_proposal_error("2.1.0", "minor: new feature, no breaks") is None


def test_any_scheme_is_accepted_because_the_project_defines_it():
    """The point of the story. None of these are YYYY.MM.DD.N."""
    for v in ("2.1.0", "v3", "2026.3", "release-42", "1.0.0-rc.1", "24w33a"):
        assert version_proposal_error(v, "per the project's rules") is None, v


def test_the_computed_scheme_is_still_accepted():
    assert version_proposal_error("2026.08.15.2", "second cut today") is None


def test_an_empty_proposal_is_refused_and_points_at_the_fallback():
    err = version_proposal_error("", "reason")
    assert err is not None
    assert "omit the field" in err["hint"]


def test_a_whitespace_only_proposal_is_refused():
    assert version_proposal_error("   ", "reason") is not None


def test_a_version_with_whitespace_is_refused():
    """It becomes a git tag."""
    err = version_proposal_error("2.1.0 final", "reason")
    assert err is not None and "whitespace" in err["error"]


def test_characters_git_refuses_in_a_tag_are_refused_here():
    for bad in ("2.1.0~1", "2.1.0^", "a:b", "v?", "v*", "a[b", "a\\b", "1..2"):
        assert version_proposal_error(bad, "reason") is not None, bad


def test_an_absurdly_long_version_is_refused():
    err = version_proposal_error("x" * 65, "reason")
    assert err is not None and "64" in err["hint"]


def test_a_colliding_version_is_refused():
    """A version names exactly one build, forever."""
    err = version_proposal_error("2.1.0", "reason", taken=["2.0.0", "2.1.0"])
    assert err is not None
    assert "already exists" in err["error"]


def test_a_non_colliding_version_passes_the_same_check():
    assert version_proposal_error("2.2.0", "reason", taken=["2.0.0", "2.1.0"]) is None


def test_a_proposal_without_a_reason_is_refused():
    """The rationale is read months later to explain the number; a version
    with no reasoning is a number nobody can defend."""
    err = version_proposal_error("2.1.0", "")
    assert err is not None
    assert "why this is the next version" in err["error"]
    assert version_proposal_error("2.1.0", "   ") is not None


def test_empty_taken_list_and_none_behave_the_same():
    assert version_proposal_error("2.1.0", "r", taken=[]) is None
    assert version_proposal_error("2.1.0", "r", taken=None) is None


def test_blank_entries_in_taken_never_match():
    """A release row with a null version must not collide with everything."""
    assert version_proposal_error("2.1.0", "r", taken=["", None]) is None  # type: ignore[list-item]
