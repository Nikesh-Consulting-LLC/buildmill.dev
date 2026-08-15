"""US-98.4/98.5: a merge lands every branch it was given, or none of them.

Pure-function tests, and deliberately so — `test_factory_mcp.py` is
`needs_db` and skips in Essential, so the all-or-nothing rule would otherwise
have no coverage in the suite anyone runs after a change.

The rule matters more than it looks. A partial merge is not a smaller
success; it is a different result nobody asked for, and the pull request in
front of the manager silently changes meaning. The gate therefore runs before
a single blob is written — "nothing partial survives a failure" is only true
if nothing was created in the first place.
"""

from __future__ import annotations

from app.factory_mcp import _merge_declaration_error


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


def _decl(*branches, outcome="clean"):
    return [
        {"branch": b, "head_sha": f"sha-{b}", "outcome": outcome}
        for b in branches
    ]


def test_a_complete_declaration_passes():
    assert (
        _merge_declaration_error(_run(), _decl("feat/a", "feat/b"), False)
        is None
    )


def test_a_missing_branch_is_refused_and_named():
    err = _merge_declaration_error(_run(), _decl("feat/a"), False)
    assert err is not None
    assert "feat/b" in err["error"]
    assert "all or nothing" in err["hint"]


def test_every_missing_branch_is_named():
    run = _run(branches=("a", "b", "c", "d"))
    err = _merge_declaration_error(run, _decl("a"), False)
    for b in ("b", "c", "d"):
        assert b in err["error"], err


def test_an_undeclared_extra_branch_is_refused():
    err = _merge_declaration_error(
        _run(), _decl("feat/a", "feat/b", "feat/sneaky"), False
    )
    assert err is not None
    assert "feat/sneaky" in err["error"]


def test_allow_partial_is_not_available_on_a_merge():
    """The escape hatch that exists for multi-story code runs must not be a
    way to smuggle a partial merge past the completeness check."""
    err = _merge_declaration_error(_run(), _decl("feat/a", "feat/b"), True)
    assert err is not None
    assert "allow_partial" in err["error"]


def test_allow_partial_is_refused_even_when_the_declaration_is_complete():
    """Order matters: if completeness were checked first, a complete
    declaration plus allow_partial would sail through and the flag would be
    silently meaningless rather than refused."""
    err = _merge_declaration_error(_run(), _decl("feat/a", "feat/b"), True)
    assert err is not None and "allow_partial" in err["error"]


def test_no_declaration_at_all_is_refused():
    err = _merge_declaration_error(_run(), None, False)
    assert err is not None
    assert "must declare" in err["error"]
    # The hint has to name what was asked for, or the agent just guesses.
    assert "feat/a" in err["hint"] and "feat/b" in err["hint"]


def test_an_empty_declaration_is_refused():
    assert _merge_declaration_error(_run(), [], False) is not None


def test_a_blank_outcome_is_refused():
    """"Clean" is a fine answer; silence is not. The account is the only
    place a silently dropped change would show."""
    err = _merge_declaration_error(
        _run(), _decl("feat/a", "feat/b", outcome="   "), False
    )
    assert err is not None
    assert "outcome" in err["error"]


def test_a_missing_outcome_key_is_refused():
    decl = [
        {"branch": "feat/a", "head_sha": "x"},
        {"branch": "feat/b", "head_sha": "y", "outcome": "clean"},
    ]
    assert _merge_declaration_error(_run(), decl, False) is not None


def test_a_non_merge_run_is_untouched():
    for kind in ("code", "plan", "test"):
        assert _merge_declaration_error({"kind": kind}, None, False) is None
        assert _merge_declaration_error({"kind": kind}, None, True) is None


def test_declaring_merges_on_a_code_run_is_refused():
    """Not silently ignored: a code run passing this has misunderstood
    something, and quietly accepting it hides that."""
    err = _merge_declaration_error({"kind": "code"}, _decl("feat/a"), False)
    assert err is not None
    assert "means nothing" in err["error"]


def test_malformed_entries_do_not_satisfy_the_check():
    """A list of junk must not count as having declared anything."""
    err = _merge_declaration_error(
        _run(), ["feat/a", {"nope": 1}, {"branch": "  "}], False
    )
    assert err is not None
    assert "must declare" in err["error"]


def test_whitespace_in_a_declared_branch_still_matches():
    decl = [
        {"branch": " feat/a ", "head_sha": "x", "outcome": "clean"},
        {"branch": "feat/b", "head_sha": "y", "outcome": "clean"},
    ]
    assert _merge_declaration_error(_run(), decl, False) is None
