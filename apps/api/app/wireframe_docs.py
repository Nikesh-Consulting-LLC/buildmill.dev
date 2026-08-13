"""Writing the wireframe tree into a project's repository — US-48.2.

`wireframes.py` is pure: declarations in, files out. This module is the half
that talks to GitHub — reading a project's token source, deciding whether the
kit needs pushing, and committing. It reuses `repo_docs.commit_files`, so a
wireframe write and a docs-tree write build their commits exactly the same way.

The write is **best-effort and never blocks a hand-back**. An agent that drew
the screen has done its work; if GitHub is down, the artifact is already stored
and US-48.5's sync is the retry. A failure here returns a reason, it does not
raise into the submit path.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from . import db, github, github_tokens, repo_docs, wireframes
from .config import Settings

KIT_MARKER = f"{wireframes.KIT_ROOT}/VERSION"


def _marker(kit_hash: str, tokens_source: str | None) -> str:
    """What the repo records about the kit it is holding.

    The repository is the register rather than a column on `projects`: it is
    where the files actually are, so it cannot disagree with itself, and a
    project whose repo was rebuilt from scratch is detected instead of assumed
    current."""
    return (
        json.dumps(
            {
                "kit": kit_hash,
                "tokens_source": tokens_source,
                "written_by": "Build Mill · US-48.1",
            },
            indent=2,
        )
        + "\n"
    )


async def _read_text(
    token: str, owner: str, repo: str, path: str, ref: str
) -> str | None:
    try:
        data = await github.get_content(token, owner, repo, path, ref)
    except github.GitHubError:
        return None
    if not isinstance(data, dict) or data.get("type") != "file":
        return None
    try:
        return base64.b64decode(data.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


async def resolve_tokens(
    token: str, owner: str, repo: str, ref: str
) -> tuple[str, str]:
    """`(tokens.css, provenance)` — the project's own design tokens.

    Walks TOKEN_SOURCE_CANDIDATES most-specific first and takes the first file
    that actually defines the contract. A repo with no stylesheet at all costs
    one 404 per candidate and lands on the neutral default, which is the right
    answer rather than a failure."""
    for path in wireframes.TOKEN_SOURCE_CANDIDATES:
        text = await _read_text(token, owner, repo, path, ref)
        if not text:
            continue
        light, _ = wireframes.read_tokens(text)
        if len(light) >= 6:
            return wireframes.build_tokens_css(text, path)
    return wireframes.build_tokens_css(None, None)


async def kit_state(
    token: str, owner: str, repo: str, ref: str, *, force: bool = False
) -> tuple[dict[str, str], str]:
    """`(files_to_push, provenance)` for the kit — empty when the repo already
    holds this exact kit.

    US-22.7's shape: compare a hash, and when it matches do no further work at
    all. Reading the token source is the expensive part (up to one contents
    call per candidate path), so an unchanged kit skips that too — which is
    what keeps a fifteen-story fan-out from doing fifteen redundant repo
    reads."""
    static_hash = wireframes.kit_code_hash()
    if not force:
        current = await _read_text(token, owner, repo, KIT_MARKER, ref)
        if current:
            try:
                held = json.loads(current)
            except ValueError:
                held = {}
            if held.get("kit") == static_hash:
                return {}, f"kit already current ({held.get('tokens_source')})"

    tokens_css, provenance = await resolve_tokens(token, owner, repo, ref)
    files = wireframes.kit_files(tokens_css)
    files[KIT_MARKER] = _marker(static_hash, provenance)
    return files, provenance


async def write_wireframe(
    settings: Settings, issue_id: str, *, trigger: str = "hand-back"
) -> dict[str, Any]:
    """Commit one story's wireframe (and the kit, if the repo needs it).

    Never raises: every failure path returns `{"skipped": reason}` or
    `{"error": reason}` so a hand-back is never lost to a GitHub problem."""
    try:
        issue = db.get_issue_for_wireframe(settings, issue_id)
        if not issue:
            return {"skipped": "work item not found"}
        project = db.get_project_docs_config(settings, str(issue["project_id"]))
        if not project:
            return {"skipped": "project not found"}
        repo_full = project.get("repo_full_name") or ""
        if "/" not in repo_full:
            return {"skipped": "no linked repository"}

        artifact = db.get_current_wireframe(settings, issue_id)
        if not artifact:
            return {"skipped": "no wireframe artifact"}
        declaration = declaration_of(artifact)
        display = db.work_item_display_id(
            issue.get("type"),
            issue.get("epic_number"),
            issue.get("item_no"),
            issue.get("sub_no"),
        ) or str(issue["id"])[:8]
        path = wireframes.page_path(display)

        branch = project.get("default_branch") or "main"
        owner, repo = repo_full.split("/", 1)
        token = await github_tokens.token_for_org(
            settings, str(project["org_id"]), repo_full
        )

        files, provenance = await kit_state(token, owner, repo, branch)
        deletes: set[str] = set()

        if declaration.get("no_ui_surface"):
            # Nothing to draw. If a previous version DID draw something, the
            # file must go: leaving it would leave the repository asserting a
            # screen the current answer says does not exist.
            existing = await repo_docs.existing_docs_paths(
                token, owner, repo, branch, root=wireframes.WIREFRAMES_ROOT
            )
            if path in existing:
                deletes.add(path)
            if not files and not deletes:
                return {"skipped": "no UI surface, nothing in the repo to remove"}
        else:
            files[path] = wireframes.build_page(
                display, issue.get("title") or "", declaration
            )

        result = await repo_docs.commit_files(
            token,
            repo_full,
            branch,
            f"docs: wireframe for {display} ({trigger})",
            files,
            deletes,
        )
        return {
            "path": None if declaration.get("no_ui_surface") else path,
            "files": sorted(files),
            "deleted": sorted(deletes),
            "tokens": provenance,
            **result,
        }
    except Exception as exc:  # noqa: BLE001 - a write must never fail a submit
        return {"error": f"{type(exc).__name__}: {exc}"}


def _entry_from_row(row: dict[str, Any]) -> dict[str, Any]:
    """One `list_project_wireframes` row as a `build_tree` entry."""
    display = db.work_item_display_id(
        row.get("type"),
        row.get("epic_number"),
        row.get("item_no"),
        row.get("sub_no"),
    )
    feature = None
    if row.get("parent_item_no") is not None:
        feature_id = db.work_item_display_id(
            row.get("parent_type"),
            row.get("parent_epic_number"),
            row.get("parent_item_no"),
            row.get("parent_sub_no"),
        )
        feature = (
            f"{feature_id} — {row.get('parent_title')}"
            if feature_id
            else row.get("parent_title")
        )
    return {
        "display_id": display,
        "title": row.get("title") or "",
        "feature": feature,
        "declaration": declaration_of(row),
    }


async def sync_tree(
    settings: Settings, project_id: str, *, trigger: str = "sync"
) -> dict[str, Any]:
    """Rebuild and commit the whole wireframe tree from stored artifacts.

    US-13.4's shape, for the same reasons: the per-hand-back write is
    best-effort, so there must be a path that makes the repository current
    again; a kit upgrade has to reach a project drawn against an older one;
    and an abandoned story's file has to be able to go away.

    One commit, or none. A half-written tree is worse than a stale one."""
    project = db.get_project_docs_config(settings, project_id)
    if not project:
        return {"skipped": "project not found"}
    repo_full = project.get("repo_full_name") or ""
    if "/" not in repo_full:
        return {"skipped": "no linked repository"}
    branch = project.get("default_branch") or "main"
    owner, repo = repo_full.split("/", 1)

    rows = db.list_project_wireframes(settings, project_id, str(project["org_id"]))
    entries = [
        _entry_from_row(row) for row in rows if row.get("abandoned_at") is None
    ]
    files = wireframes.build_tree(entries)

    token = await github_tokens.token_for_org(
        settings, str(project["org_id"]), repo_full
    )
    # A sync is how a kit upgrade reaches a project, and how a token change is
    # picked up — so it always regenerates rather than trusting the marker.
    kit, provenance = await kit_state(token, owner, repo, branch, force=True)
    files.update(kit)
    forget_tokens(project_id)

    # US-22.1's rule: everything under this root the generation does not
    # produce is deleted, so everything found here is current. The blast radius
    # stops at docs/wireframes/ — docs/factory/ has its own writer and neither
    # can reach the other's files.
    existing = await repo_docs.existing_docs_paths(
        token, owner, repo, branch, root=wireframes.WIREFRAMES_ROOT
    )
    deletes = existing - set(files)

    result = await repo_docs.commit_files(
        token,
        repo_full,
        branch,
        f"docs: wireframes ({trigger})",
        files,
        deletes,
    )
    return {
        "files": sorted(files),
        "deleted": sorted(deletes),
        "drawn": sum(1 for e in entries if not e["declaration"].get("no_ui_surface")),
        "no_ui_surface": sum(
            1 for e in entries if e["declaration"].get("no_ui_surface")
        ),
        "tokens": provenance,
        **result,
    }


_TOKEN_CACHE: dict[str, tuple[str, str]] = {}


async def tokens_for_project(
    settings: Settings, project: dict[str, Any], *, refresh: bool = False
) -> tuple[str, str]:
    """A project's tokens, cached for the API process's lifetime.

    The preview panel renders on every visit to a work item, and reading the
    token source costs up to one GitHub call per candidate path — far too much
    to pay per page view. A project's design tokens change about as often as
    its design system does, and the cache is keyed on the project so a restart
    or an explicit sync (US-48.5) picks up a change. A stale palette in the
    preview is a cosmetic error for one process's lifetime; the committed
    file, which is what anyone reviews closely, is regenerated on every
    sync."""
    key = str(project["id"])
    if not refresh and key in _TOKEN_CACHE:
        return _TOKEN_CACHE[key]
    repo_full = project.get("repo_full_name") or ""
    if "/" not in repo_full:
        result = wireframes.build_tokens_css(None, None)
    else:
        owner, repo = repo_full.split("/", 1)
        try:
            token = await github_tokens.token_for_org(
                settings, str(project["org_id"]), repo_full
            )
            result = await resolve_tokens(
                token, owner, repo, project.get("default_branch") or "main"
            )
        except Exception:  # noqa: BLE001 - a preview must still render
            result = wireframes.build_tokens_css(None, None)
    _TOKEN_CACHE[key] = result
    return result


def forget_tokens(project_id: str) -> None:
    """Drop a project's cached tokens — called by the sync, which has just
    read the real ones."""
    _TOKEN_CACHE.pop(str(project_id), None)


def declaration_of(artifact: dict[str, Any]) -> dict[str, Any]:
    content = artifact.get("content")
    if isinstance(content, dict):
        return content
    try:
        parsed = json.loads(content or "{}")
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
