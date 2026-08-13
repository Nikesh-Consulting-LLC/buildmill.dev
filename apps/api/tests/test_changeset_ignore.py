"""US-31.7: a changeset cannot carry build artifacts.

With a persistent workspace (us-31.8) the folder the agent works in holds
node_modules, .venv, .next and friends. There is no `git status` to filter
them and no .gitignore being applied by anything — so the factory reads the
repository's own rules and refuses.
"""

import pytest

from app.changesets import (
    build_ignore_matcher,
    ignored_paths,
    scratch_paths,
    validate_changeset,
)


def _f(path, op="add", content="x"):
    return {"path": path, "op": op, "content": content}


# --------------------------------------------------------- root patterns


def test_root_directory_pattern_ignores_everything_under_it():
    ignored = build_ignore_matcher({".gitignore": "node_modules/\n.next/\n"})
    assert ignored("node_modules/react/index.js")
    assert ignored(".next/build-manifest.json")
    assert not ignored("src/index.js")


def test_glob_and_comment_and_blank_lines():
    ignored = build_ignore_matcher(
        {".gitignore": "# comment\n\n*.log\nbuild/*.tmp\n"}
    )
    assert ignored("server.log")
    assert ignored("build/x.tmp")
    assert not ignored("build/x.js")


# --------------------------------------------------------- nested + negation


def test_nested_gitignore_applies_only_under_its_own_directory():
    ignored = build_ignore_matcher(
        {".gitignore": "", "apps/web/.gitignore": "dist/\n"}
    )
    assert ignored("apps/web/dist/app.js")
    # the same folder name elsewhere is NOT covered by the nested file
    assert not ignored("apps/api/dist/app.js")


def test_negation_unignores():
    ignored = build_ignore_matcher(
        {".gitignore": "vendor/*\n!vendor/keep.js\n"}
    )
    assert ignored("vendor/bundle.js")
    assert not ignored("vendor/keep.js")


# --------------------------------------------------------- the guard itself


def test_ignored_paths_names_every_offender():
    out = ignored_paths(
        [
            _f("node_modules/react/index.js"),
            _f("src/app.ts"),
            _f(".venv/pyvenv.cfg"),
        ],
        {".gitignore": "node_modules/\n.venv/\n"},
    )
    assert out == ["node_modules/react/index.js", ".venv/pyvenv.cfg"]


def test_already_tracked_file_still_lands():
    """git's own behaviour: a tracked file is not ignored no matter what the
    patterns say — otherwise an over-broad rule refuses a lockfile update."""
    out = ignored_paths(
        [_f("package-lock.json", op="update")],
        {".gitignore": "*.json\n"},
        tracked={"package-lock.json"},
    )
    assert out == []


def test_no_ignore_file_means_no_findings():
    assert ignored_paths([_f("anything.js")], {}) == []


def test_deletes_are_checked_too():
    """A delete of an ignored path is still nonsense to commit."""
    out = ignored_paths(
        [{"path": "node_modules/x.js", "op": "delete"}],
        {".gitignore": "node_modules/\n"},
    )
    assert out == ["node_modules/x.js"]


# --------------------------------------------------------- factory scratch


def test_factory_scratch_is_refused_unconditionally():
    out = scratch_paths(
        [
            _f(".factory-out/plan.md"),
            _f(".factory-workspace.json"),
            _f("src/ok.ts"),
        ]
    )
    assert out == [".factory-out/plan.md", ".factory-workspace.json"]


def test_token_bearing_configs_are_scratch_too():
    """2026-08-13 (FEAT-2.8): an agent submitted `.factory-mcp.json` and its
    `.grok/` translation, the factory committed them, and the worker token
    they carry landed in the project repo's history. Never again."""
    out = scratch_paths(
        [
            _f(".factory-mcp.json"),
            _f(".grok/config.toml"),
            _f(".grok/mcp_call.py"),
            _f("src/ok.ts"),
        ]
    )
    assert out == [".factory-mcp.json", ".grok/config.toml", ".grok/mcp_call.py"]


# ------------------------------------------- path shape (pre-existing rules)


def test_absolute_and_traversal_refused_with_distinct_reasons():
    findings = validate_changeset(
        [_f("/etc/passwd"), _f("../../secrets.txt"), _f(".git/config")]
    )
    joined = " ".join(findings)
    assert "absolute paths are rejected" in joined
    assert "path traversal" in joined
    assert "nothing under .git/" in joined
