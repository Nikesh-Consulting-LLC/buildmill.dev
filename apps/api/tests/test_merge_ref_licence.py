"""US-98.3: a merge run may read its own branches, and nothing else.

These are deliberately pure-function tests. `test_factory_mcp.py` is marked
`needs_db` and skips in Essential, so the licence that decides which refs a
claim can reach would otherwise have **no** coverage in the suite that
actually gets run after a change.

The rule being pinned: a merge is the first kind of work that is inherently
about several refs, and the obvious way to serve it — `get_project_workspace`,
which already takes any ref — is gated on the standing `no_claim_checkout`
capability, i.e. permission to browse any ref of any visible project. The
claim is used as the authority instead, and it licenses exactly the branches
the manager named plus the base.
"""

from __future__ import annotations

from app.factory_mcp import _merge_refs, _refused_ref


def _merge_run(branches=("feat/a", "feat/b"), base="main", base_head="base0"):
    return {
        "kind": "merge",
        "_repo_ic": {
            "default_branch": base,
            "merge_base": {"branch": base, "head_sha": base_head},
            "merge_branches": [
                {"branch": b, "head_sha": f"sha-{b}"} for b in branches
            ],
        },
    }


def test_a_non_merge_run_has_no_licence():
    for kind in ("code", "plan", "test", "release", "deploy", "chore"):
        assert _merge_refs({"kind": kind, "_repo_ic": {}}) is None


def test_the_licence_is_the_base_plus_the_named_branches():
    base, branches = _merge_refs(_merge_run())
    assert base == "main"
    assert branches == ["feat/a", "feat/b"]


def test_a_declared_branch_is_allowed():
    run = _merge_run()
    assert _refused_ref(run, "feat/a") is None
    assert _refused_ref(run, "feat/b") is None


def test_the_base_is_allowed():
    assert _refused_ref(_merge_run(), "main") is None


def test_an_undeclared_ref_is_refused_and_says_what_is_allowed():
    err = _refused_ref(_merge_run(), "feat/somebody-elses-work")
    assert err is not None
    assert "not licensed" in err["error"]
    # The refusal has to be actionable — an agent that cannot see the allowed
    # set just guesses again.
    assert "feat/a" in err["hint"] and "feat/b" in err["hint"]
    assert "main" in err["hint"]


def test_an_arbitrary_sha_is_refused():
    """Licensing by branch NAME means a raw sha is not a way around it."""
    assert _refused_ref(_merge_run(), "0123456789abcdef") is not None


def test_an_empty_ref_is_always_allowed():
    """Empty means 'use the default resolution', which every kind may do."""
    assert _refused_ref(_merge_run(), "") is None
    assert _refused_ref(_merge_run(), "   ") is None
    assert _refused_ref({"kind": "code", "_repo_ic": {}}, "") is None


def test_a_non_merge_run_is_never_refused_a_ref():
    """No existing kind's reach changes — get_repo_tree and read_repo_file
    have always taken a ref and must keep doing so."""
    code = {"kind": "code", "_repo_ic": {"default_branch": "main"}}
    assert _refused_ref(code, "any/branch/at/all") is None


def test_whitespace_around_a_declared_branch_still_matches():
    assert _refused_ref(_merge_run(), "  feat/a  ") is None


def test_a_merge_with_no_branches_licenses_only_the_base():
    run = _merge_run(branches=())
    base, branches = _merge_refs(run)
    assert base == "main" and branches == []
    assert _refused_ref(run, "main") is None
    assert _refused_ref(run, "feat/a") is not None


def test_the_licence_falls_back_to_default_branch_when_no_merge_base():
    """A run dispatched before merge_base existed must not become unbounded."""
    run = {
        "kind": "merge",
        "_repo_ic": {
            "default_branch": "trunk",
            "merge_branches": [{"branch": "feat/a", "head_sha": "x"}],
        },
    }
    base, branches = _merge_refs(run)
    assert base == "trunk"
    assert _refused_ref(run, "trunk") is None
    assert _refused_ref(run, "main") is not None


def test_malformed_branch_entries_are_ignored_not_trusted():
    run = {
        "kind": "merge",
        "_repo_ic": {
            "default_branch": "main",
            "merge_branches": ["feat/a", {"no_branch_key": 1}, {"branch": ""}],
        },
    }
    _, branches = _merge_refs(run)
    assert branches == []
    assert _refused_ref(run, "feat/a") is not None
