"""app/changesets.py — US-5.26 changeset validation and server-side git."""

import asyncio
import base64

import pytest

from app import changesets
from app.github import GitHubError


def _file(path="src/app.py", op="update", content="print('hi')", **kw):
    return {"path": path, "op": op, "content": content, **kw}


def test_valid_changeset_has_no_findings():
    files = [
        _file("src/app.py", "update"),
        _file("docs/new.md", "add", "hello"),
        {"path": "old.txt", "op": "delete"},
    ]
    assert changesets.validate_changeset(files) == []


def test_empty_changeset_rejected():
    assert changesets.validate_changeset([]) == [
        "changeset is empty — nothing to commit"
    ]


@pytest.mark.parametrize(
    "path",
    ["/etc/passwd", "C:/windows/evil", "..\\up", "a/../../b", ".git/config", "src/.git/hooks"],
)
def test_dangerous_paths_rejected(path):
    findings = changesets.validate_changeset([_file(path)])
    assert findings, path


def test_op_content_mismatches_rejected():
    findings = changesets.validate_changeset(
        [
            {"path": "a.txt", "op": "delete", "content": "leftover"},
            {"path": "b.txt", "op": "add"},
            {"path": "c.txt", "op": "rename", "content": "x"},
        ]
    )
    assert any("delete carries no content" in f for f in findings)
    assert any("requires content" in f for f in findings)
    assert any("op must be add, update, or delete" in f for f in findings)


def test_bad_base64_and_duplicate_paths_rejected():
    findings = changesets.validate_changeset(
        [
            _file("bin.dat", "add", "!!!not-base64!!!", encoding="base64"),
            _file("dup.txt"),
            _file("dup.txt"),
        ]
    )
    assert any("not valid base64" in f for f in findings)
    assert any("duplicate path" in f for f in findings)


# US-72.1 ---------------------------------------------------------------------
#
# A real agent handed back base64 content while declaring text; the factory
# committed the encoded strings verbatim and a live repo's default branch
# carried 14 unreadable blobs. The guard refuses exactly that shape and
# nothing near it.

# ~40 lines of plausible source, well past the 200-char floor once encoded.
_REAL_SOURCE = "\n".join(
    f"def handler_{i}(request):\n    return process(request, retries={i})"
    for i in range(20)
)
_ENCODED_SOURCE = base64.b64encode(_REAL_SOURCE.encode()).decode()


def test_base64_content_declared_text_is_refused():
    findings = changesets.validate_changeset(
        [_file("src/app.py", "update", _ENCODED_SOURCE)]
    )
    assert any(
        "resubmit it with encoding: 'base64'" in f for f in findings
    ), findings


def test_same_payload_declared_base64_is_accepted():
    files = [_file("src/app.py", "update", _ENCODED_SOURCE, encoding="base64")]
    assert changesets.validate_changeset(files) == []


def test_plain_source_and_short_tokens_still_pass():
    files = [
        # Real multi-line source: fails the single-line test.
        _file("src/app.py", "update", _REAL_SOURCE),
        # A base64-looking token under the length floor.
        _file("src/token.txt", "add", base64.b64encode(b"short\nvalue").decode()),
        # An encoded *binary* declared base64: allowed as ever.
        _file("img.png", "add", base64.b64encode(b"\x89PNG\x00\x01").decode(), encoding="base64"),
    ]
    assert changesets.validate_changeset(files) == []


def test_single_line_base64_of_binary_declared_text_passes():
    # Decodes fine but not UTF-8 — could be a legitimate opaque text token.
    blob = base64.b64encode(bytes(range(200)) * 3).decode()
    assert changesets.validate_changeset([_file("blob.txt", "add", blob)]) == []


def test_file_count_cap():
    files = [_file(f"f{i}.txt", "add", "x") for i in range(changesets.MAX_FILES + 1)]
    findings = changesets.validate_changeset(files)
    assert any("file cap" in f for f in findings)


def test_total_size_cap(monkeypatch):
    monkeypatch.setattr(changesets, "MAX_TOTAL_BYTES", 10)
    findings = changesets.validate_changeset([_file("a.txt", "add", "x" * 11)])
    assert any("above the" in f for f in findings)


def test_summarize_never_carries_content():
    out = changesets.summarize(
        [_file("a.txt", "add", "secret"), {"path": "b.txt", "op": "delete"}]
    )
    assert out == [
        {"path": "a.txt", "op": "add", "size": 6},
        {"path": "b.txt", "op": "delete", "size": None},
    ]


# --- apply_changeset: the git-data flow with GitHub stubbed -----------------


class _GitHubStub:
    """Records the git-data calls apply_changeset makes."""

    def __init__(self, existing_head=None):
        self.existing_head = existing_head
        self.calls = []

    def install(self, monkeypatch):
        async def get_commit(token, owner, repo, ref):
            self.calls.append(("get_commit", ref))
            if ref == "missing":
                raise GitHubError(f"ref '{ref}' not found in this repo")
            return {"sha": ref, "commit": {"tree": {"sha": "tree-base"}}}

        async def get_ref(token, owner, repo, branch):
            self.calls.append(("get_ref", branch))
            if self.existing_head is None:
                return None
            return {"object": {"sha": self.existing_head}}

        async def create_blob(token, owner, repo, content_b64):
            self.calls.append(("create_blob", content_b64))
            return f"blob-{len(self.calls)}"

        async def create_tree(token, owner, repo, base_tree, entries):
            self.calls.append(("create_tree", base_tree, entries))
            return "tree-new"

        async def create_commit(
            token, owner, repo, message, tree_sha, parent_sha,
            author_name, author_email,
        ):
            self.calls.append(
                ("create_commit", message, tree_sha, parent_sha, author_name)
            )
            return "commit-new"

        async def create_ref(token, owner, repo, branch, sha):
            self.calls.append(("create_ref", branch, sha))

        async def update_ref(token, owner, repo, branch, sha):
            self.calls.append(("update_ref", branch, sha))

        for name, fn in {
            "get_commit": get_commit,
            "get_ref": get_ref,
            "create_blob": create_blob,
            "create_tree": create_tree,
            "create_commit": create_commit,
            "create_ref": create_ref,
            "update_ref": update_ref,
        }.items():
            monkeypatch.setattr(f"app.changesets.github.{name}", fn)


def _apply(files, base_sha="base-1", existing_head=None, monkeypatch=None):
    stub = _GitHubStub(existing_head=existing_head)
    stub.install(monkeypatch)
    result = asyncio.run(
        changesets.apply_changeset(
            "tok", "acme/webshop", "factory/issue-1", base_sha,
            "feat: do the thing\n\nFactory-Run: run-1",
            files, author_name="Claude Code",
        )
    )
    return stub, result


def test_apply_first_submit_creates_branch(monkeypatch):
    stub, result = _apply(
        [_file("src/app.py", "update"), {"path": "old.txt", "op": "delete"}],
        monkeypatch=monkeypatch,
    )
    assert result == {"commit_sha": "commit-new"}
    kinds = [c[0] for c in stub.calls]
    assert "create_ref" in kinds and "update_ref" not in kinds
    # deletion rides the tree as a null-sha entry
    tree_call = next(c for c in stub.calls if c[0] == "create_tree")
    delete_entry = next(e for e in tree_call[2] if e["path"] == "old.txt")
    assert delete_entry["sha"] is None
    # worker attribution on the commit
    commit_call = next(c for c in stub.calls if c[0] == "create_commit")
    assert commit_call[4] == "Claude Code via Build Mill"
    assert commit_call[3] == "base-1"  # parent = declared base


def test_apply_second_submit_appends_on_matching_head(monkeypatch):
    stub, result = _apply(
        [_file()], base_sha="head-A", existing_head="head-A",
        monkeypatch=monkeypatch,
    )
    assert result == {"commit_sha": "commit-new"}
    kinds = [c[0] for c in stub.calls]
    assert "update_ref" in kinds and "create_ref" not in kinds


def test_apply_stale_base_returns_current_head(monkeypatch):
    stub, result = _apply(
        [_file()], base_sha="head-OLD", existing_head="head-B",
        monkeypatch=monkeypatch,
    )
    assert result == {"stale": True, "current_head": "head-B"}
    # nothing was written
    kinds = [c[0] for c in stub.calls]
    assert "create_blob" not in kinds and "create_commit" not in kinds


def test_apply_unknown_base_sha_is_actionable(monkeypatch):
    with pytest.raises(GitHubError) as ei:
        _apply([_file()], base_sha="missing", monkeypatch=monkeypatch)
    assert "base_sha" in ei.value.message
    assert "get_workspace" in ei.value.message


def test_apply_encodes_text_content_as_base64_blob(monkeypatch):
    stub, _ = _apply([_file("a.txt", "add", "hello")], monkeypatch=monkeypatch)
    blob_call = next(c for c in stub.calls if c[0] == "create_blob")
    assert base64.b64decode(blob_call[1]) == b"hello"


# ---------------------------------------------------------------- us-96.8
# Scratch is filtered, not fatal: the security property (scratch never
# lands) is unchanged; the real files no longer fail with it.


def test_split_scratch_filters_scratch_and_keeps_the_work():
    files = [
        {"path": "src/app.py", "op": "update", "content": "x"},
        {"path": ".factory-out/test_cases.json", "op": "add", "content": "[]"},
        {"path": ".grok/config.toml", "op": "add", "content": ""},
    ]
    kept, dropped = changesets.split_scratch(files)
    assert [f["path"] for f in kept] == ["src/app.py"]
    assert dropped == [".factory-out/test_cases.json", ".grok/config.toml"]


def test_split_scratch_only_scratch_keeps_nothing():
    files = [{"path": ".factory-out/plan.md", "op": "add", "content": ""}]
    kept, dropped = changesets.split_scratch(files)
    assert kept == []
    assert dropped == [".factory-out/plan.md"]


def test_split_scratch_clean_changeset_is_untouched():
    files = [{"path": "a.py", "op": "add", "content": ""}]
    kept, dropped = changesets.split_scratch(files)
    assert kept == files
    assert dropped == []
