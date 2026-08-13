"""US-5.26: server-side commit construction from an agent's changeset.

The agent hands back changed files over MCP; the factory does all the
git — blobs → tree → commit → ref via GitHub's git-data API with the
org's credential — so a worker needs no git binary, no GitHub account,
and no credential beyond its worker token, and the factory constructs
(and therefore fully audits) every commit an agent lands.
"""

import base64
import binascii
import re
from typing import Any, Callable

from . import github

MAX_FILES = 200
MAX_TOTAL_BYTES = 10 * 1024 * 1024
VALID_OPS = ("add", "update", "delete")

# US-31.7: the factory's own scratch never lands, whatever any .gitignore
# says. `.factory-out/` is where a module writes plan.md / test_cases.json;
# the workspace bookkeeping file is us-31.8's.
# 2026-08-13 (FEAT-2.8): `.factory-mcp.json` and `.grok/` joined the list the
# hard way — an agent submitted them, the factory committed them, and the
# worker token they carry landed in the project repo's history (rotation
# required). The MCP config is written per run and removed after it
# (US-31.9); nothing legitimate ever commits it.
FACTORY_SCRATCH = (
    ".factory-out/",
    ".factory-workspace.json",
    ".factory-mcp.json",
    ".grok/",
)

COMMIT_AUTHOR_EMAIL = "workers@buildmill.dev"

_ABSOLUTE = re.compile(r"^([A-Za-z]:|/|\\)")

# US-40.2 --------------------------------------------------------------------
#
# A branch hand-back has no per-commit MCP call to carry attribution, so the
# commit message carries it: one `Factory-Story:` trailer per story the commit
# lands. `apply_changeset` already appends `Factory-Run:`, so a trailer is the
# established convention here rather than a new invention.
#
# Deliberately lenient about shape and strict about meaning. The value is taken
# verbatim and resolved against the run's membership by the caller, so a typo
# surfaces as "not in this run" rather than as silent mis-attribution — the one
# outcome us-27.1's fan-out exists to prevent.
_STORY_TRAILER = re.compile(
    r"^[ \t]*Factory-Story[ \t]*:[ \t]*(?P<value>.+?)[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)


def story_trailers(message: str) -> list[str]:
    """Every `Factory-Story:` value in a commit message, in order, de-duped.

    One trailer may name several stories comma-separated
    (`Factory-Story: US-1.1, US-1.2`) and a message may carry several
    trailers; both mean the same thing and both are accepted.
    """
    out: list[str] = []
    for match in _STORY_TRAILER.finditer(message or ""):
        for part in match.group("value").split(","):
            value = part.strip()
            if value and value not in out:
                out.append(value)
    return out


def _decode(f: dict[str, Any]) -> bytes | None:
    """The file's content as bytes, or None when base64 doesn't parse."""
    content = f.get("content") or ""
    if (f.get("encoding") or "text") == "base64":
        try:
            return base64.b64decode(content, validate=True)
        except (binascii.Error, ValueError):
            return None
    return content.encode()


def _looks_misdeclared_base64(content: str) -> bool:
    """US-72.1: a "text" file whose whole body is one long base64 line that
    decodes to multi-line UTF-8 text is an encoded file mis-declared as text.

    Agents sometimes hand back base64 content without setting
    `encoding: "base64"`; committed verbatim, that merged 14 unreadable blobs
    into a live repo's default branch. All four conditions must hold, so a
    real text file essentially cannot trip this: source code has newlines
    (fails the single-line test), an encoded binary fails the UTF-8 test,
    and short base64-looking tokens fail the length floor.
    """
    s = (content or "").strip()
    if len(s) < 200 or "\n" in s:
        return False
    try:
        decoded = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return False
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return "\n" in text


def build_ignore_matcher(
    ignore_files: dict[str, str]
) -> "Callable[[str], bool]":
    """US-31.7: a predicate over repo-relative paths, from the repository's
    own `.gitignore` files.

    `ignore_files` maps each ignore file's repo-relative path to its contents,
    so `{".gitignore": ..., "apps/web/.gitignore": ...}`. A nested file's
    patterns apply only under its own directory — that is what makes
    `node_modules/` at the root and `!vendor/lib.js` deeper down both mean
    what a developer expects.

    Uses pathspec's `gitignore` factory — git's own pattern semantics. A
    hand-rolled partial matcher produces refusals developers reasonably call
    wrong, and the workaround for a wrong refusal is to stop trusting the
    guard. (`gitwildmatch` is the older name for the same thing and is
    deprecated in pathspec 1.x.)
    """
    import pathspec

    compiled: list[tuple[str, Any]] = []
    for ignore_path, body in sorted(ignore_files.items()):
        prefix = ignore_path.rsplit("/", 1)[0] if "/" in ignore_path else ""
        lines = [ln for ln in (body or "").splitlines()]
        if not lines:
            continue
        spec = pathspec.PathSpec.from_lines("gitignore", lines)
        compiled.append((prefix, spec))

    def ignored(path: str) -> bool:
        norm = path.replace("\\", "/").lstrip("/")
        result = False
        for prefix, spec in compiled:
            if prefix:
                if not (norm == prefix or norm.startswith(prefix + "/")):
                    continue
                relative = norm[len(prefix) + 1 :]
            else:
                relative = norm
            if not relative:
                continue
            # Later/most-specific match wins, and a negation un-ignores —
            # which is why this walks every spec instead of short-circuiting
            # on the first hit.
            for pattern in spec.patterns:
                if pattern.include is None or pattern.regex is None:
                    continue
                if pattern.regex.match(relative):
                    result = pattern.include
        return result

    return ignored


def ignored_paths(
    files: list[dict[str, Any]],
    ignore_files: dict[str, str],
    tracked: set[str] | None = None,
) -> list[str]:
    """US-31.7: which of this changeset's paths the repository ignores.

    `tracked` is the set of paths already in the tree at base_sha. A file git
    already tracks is not ignored no matter what the patterns say — that is
    git's own behaviour, and it is what keeps a lockfile update from being
    refused because someone's `.gitignore` is over-broad.
    """
    if not ignore_files:
        return []
    ignored = build_ignore_matcher(ignore_files)
    already = tracked or set()
    out: list[str] = []
    for f in files:
        path = str(f.get("path") or "").strip().replace("\\", "/")
        if not path or path in already:
            continue
        if ignored(path):
            out.append(path)
    return out


def scratch_paths(files: list[dict[str, Any]]) -> list[str]:
    """US-31.7: the factory's own scratch, refused unconditionally rather than
    by hoping it appears in someone's .gitignore."""
    out: list[str] = []
    for f in files:
        path = str(f.get("path") or "").strip().replace("\\", "/").lstrip("/")
        if not path:
            continue
        if any(
            path == s.rstrip("/") or path.startswith(s) if s.endswith("/")
            else path == s
            for s in FACTORY_SCRATCH
        ):
            out.append(path)
    return out


def validate_changeset(files: list[dict[str, Any]]) -> list[str]:
    """US-5.21-style findings — all of them, checked before anything
    touches GitHub."""
    if not files:
        return ["changeset is empty — nothing to commit"]
    findings: list[str] = []
    if len(files) > MAX_FILES:
        findings.append(
            f"{len(files)} files exceeds the {MAX_FILES}-file cap — "
            "split the work or use the factory git remote"
        )
    total = 0
    seen: set[str] = set()
    for i, f in enumerate(files, 1):
        raw_path = str(f.get("path") or "").strip()
        label = f"file {i} ({raw_path or 'no path'})"
        if not raw_path:
            findings.append(f"file {i} has no path")
            continue
        path = raw_path.replace("\\", "/")
        if _ABSOLUTE.match(raw_path):
            findings.append(f"{label}: absolute paths are rejected")
        if ".." in path.split("/"):
            findings.append(f"{label}: path traversal ('..') is rejected")
        if path == ".git" or path.startswith(".git/") or "/.git/" in path:
            findings.append(f"{label}: nothing under .git/ is writable")
        if path in seen:
            findings.append(f"{label}: duplicate path in one changeset")
        seen.add(path)
        op = f.get("op")
        if op not in VALID_OPS:
            findings.append(
                f"{label}: op must be add, update, or delete (got {op!r})"
            )
            continue
        if op == "delete":
            if f.get("content"):
                findings.append(
                    f"{label}: a delete carries no content — drop it or "
                    "change the op"
                )
            continue
        if "content" not in f:
            findings.append(f"{label}: op '{op}' requires content")
            continue
        if (f.get("encoding") or "text") not in ("text", "base64"):
            findings.append(
                f"{label}: encoding must be text or base64"
            )
            continue
        if (f.get("encoding") or "text") == "text" and _looks_misdeclared_base64(
            str(f.get("content") or "")
        ):
            findings.append(
                f"{label}: content is one long base64 line that decodes to "
                "multi-line text — if you encoded this file, resubmit it "
                "with encoding: 'base64'; declared as text it would be "
                "committed as the encoded string verbatim"
            )
            continue
        data = _decode(f)
        if data is None:
            findings.append(f"{label}: content is not valid base64")
            continue
        total += len(data)
    if total > MAX_TOTAL_BYTES:
        findings.append(
            f"changeset content is {total} bytes — above the "
            f"{MAX_TOTAL_BYTES}-byte cap; split the work or use the "
            "factory git remote"
        )
    return findings


def summarize(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The audit record: paths, ops, sizes — never content."""
    out = []
    for f in files:
        data = _decode(f) if f.get("op") != "delete" else None
        out.append(
            {
                "path": str(f.get("path") or "").replace("\\", "/"),
                "op": f.get("op"),
                "size": len(data) if data is not None else None,
            }
        )
    return out


async def apply_changeset(
    token: str,
    repo_full_name: str,
    branch: str,
    base_sha: str,
    message: str,
    files: list[dict[str, Any]],
    author_name: str,
) -> dict[str, Any]:
    """Build one commit from base_sha and land it on the branch.

    First submit: the branch doesn't exist yet — it's created at the new
    commit. Iteration: the branch head must equal base_sha, or the stale
    base comes back ({stale, current_head}) so the caller can refetch and
    reapply — never a silent overwrite (the ref update is fast-forward
    only, so even a race loses cleanly)."""
    owner, repo = repo_full_name.split("/", 1)
    try:
        base_commit = await github.get_commit(token, owner, repo, base_sha)
    except github.GitHubError as e:
        if "not found" in e.message:
            raise github.GitHubError(
                f"base_sha '{base_sha}' is not a commit in this repo — "
                "get_workspace answers the current base"
            )
        raise
    base_tree = base_commit["commit"]["tree"]["sha"]

    ref = await github.get_ref(token, owner, repo, branch)
    if ref is not None:
        current_head = ref["object"]["sha"]
        if current_head != base_sha:
            return {"stale": True, "current_head": current_head}

    entries: list[dict[str, Any]] = []
    for f in files:
        path = str(f["path"]).replace("\\", "/").strip("/")
        if f["op"] == "delete":
            entries.append(
                {"path": path, "mode": "100644", "type": "blob", "sha": None}
            )
            continue
        data = _decode(f) or b""
        blob_sha = await github.create_blob(
            token, owner, repo, base64.b64encode(data).decode()
        )
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob_sha}
        )

    tree_sha = await github.create_tree(token, owner, repo, base_tree, entries)
    commit_sha = await github.create_commit(
        token,
        owner,
        repo,
        message,
        tree_sha,
        base_sha,
        author_name=f"{author_name} via Build Mill",
        author_email=COMMIT_AUTHOR_EMAIL,
    )
    if ref is None:
        await github.create_ref(token, owner, repo, branch, commit_sha)
    else:
        await github.update_ref(token, owner, repo, branch, commit_sha)
    return {"commit_sha": commit_sha}
