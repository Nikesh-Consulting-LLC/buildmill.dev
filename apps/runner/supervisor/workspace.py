"""Per-project workspace identity and lifecycle (US-31.8).

`gitwork.prepare_checkout` derived the working folder from the WORK ITEM:

    workdir = workspace_root() / f"issue-{str(issue)[:8]}"

So ten stories on one project meant ten clones, ten dependency installs, ten
build caches warmed from cold — and nothing ever pruned them, so a box
accumulated one tree per work item it had ever touched until the disk was the
thing that failed.

The unit of a workspace is the PROJECT. What persists here is expensive and
disposable — `node_modules`, `.venv`, `.next`, `target` — while the tracked
source is reconciled against the factory every run (US-31.6, including
deletions). So persistence can never make a run wrong, only faster: the
persisted half is precisely the half no correctness depends on.

One agent runs one thing at a time (US-32.3 makes that the rule; the runner
has never honoured `concurrency`), so a single folder per project is
uncontended by construction. If that ever changes, a lane suffix belongs in
`workspace_for` and this module is where to look.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from . import gitwork

logger = logging.getLogger("supervisor.workspace")

# The bookkeeping file lives inside the workspace and is never committed —
# `changesets.FACTORY_SCRATCH` refuses it server-side (US-31.7).
STATE_FILE = ".factory-workspace.json"

# A workspace untouched for this long is reclaimable. Deliberately generous:
# the whole point is to keep dependencies, and re-installing them is the cost
# of being wrong here.
RECLAIM_AFTER_SECONDS = 14 * 24 * 60 * 60


def workspace_for(project_id: str | None, fallback: str) -> Path:
    """The folder this project's work happens in.

    `fallback` (a run id) is used only when a run carries no project — an
    older server, or an issue-less run kind. It keeps the old
    one-folder-per-thing behaviour for that case rather than colliding
    every project-less run into one directory.
    """
    root = gitwork.workspace_root()
    if project_id:
        return root / f"project-{str(project_id)[:8]}"
    return root / f"run-{str(fallback)[:8]}"


def ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_state(path: Path) -> dict[str, Any]:
    """The workspace's own record: what base it holds, when it was last used.

    The factory holds the authoritative delivery record (US-31.6) — this is
    local convenience for logging and reclamation, so a corrupt or missing
    file is not an error.
    """
    f = path / STATE_FILE
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_state(path: Path, **fields: Any) -> None:
    state = read_state(path)
    state.update(fields)
    state["touched_at"] = time.time()
    try:
        (path / STATE_FILE).write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
    except OSError as e:  # noqa: BLE001 — bookkeeping must not fail a run
        logger.warning("could not write workspace state in %s: %s", path, e)


def touch(path: Path) -> None:
    write_state(path)


# --------------------------------------------------------------------------
# Repair: two tiers, cheapest first (US-31.8)
# --------------------------------------------------------------------------
# Deleting the folder used to be the only answer to a broken tree. Against a
# persistent workspace that also throws away the dependencies, which is a
# heavy price for what is usually a stale source tree. Tier one invalidates
# the delivery record so the next run re-establishes the SOURCE and keeps the
# artifacts; tier two removes the folder. Escalation is ordered and recorded,
# in keeping with US-27.12 — the cheapest action that could explain the
# symptom, first.


def invalidate(path: Path) -> str | None:
    """Tier one: forget the base we believe we hold, keep the artifacts."""
    if not path.exists():
        return None
    state = read_state(path)
    had = state.get("base_sha")
    state.pop("base_sha", None)
    state.pop("paths", None)
    try:
        (path / STATE_FILE).write_text(
            json.dumps({**state, "touched_at": time.time()}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return None
    return (
        "invalidated the workspace's source record "
        f"(was {str(had)[:8]}) — the next run refetches the tree and keeps "
        "installed dependencies"
        if had
        else None
    )


def wipe(path: Path) -> str | None:
    """Tier two: remove the folder, dependencies and all."""
    if not path.exists():
        return None
    shutil.rmtree(path, ignore_errors=True)
    return "removed the whole workspace, including installed dependencies"


# --------------------------------------------------------------------------
# Reclamation
# --------------------------------------------------------------------------


def dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def usage() -> dict[str, Any]:
    """Per-workspace disk usage, for the agent-server probe."""
    root = gitwork.workspace_root()
    items = []
    for child in sorted(root.iterdir() if root.exists() else []):
        if not child.is_dir():
            continue
        state = read_state(child)
        items.append(
            {
                "name": child.name,
                "bytes": dir_size_bytes(child),
                "touched_at": state.get("touched_at"),
            }
        )
    return {"root": str(root), "workspaces": items,
            "total_bytes": sum(i["bytes"] for i in items)}


def reclaim(older_than_seconds: int = RECLAIM_AFTER_SECONDS) -> dict[str, Any]:
    """Remove workspaces unused beyond a threshold, and SAY what went.

    Silent reclamation of a folder holding a slow dependency install is the
    kind of thing that reads as a mysterious slowdown later.
    """
    root = gitwork.workspace_root()
    cutoff = time.time() - older_than_seconds
    removed: list[dict[str, Any]] = []
    for child in sorted(root.iterdir() if root.exists() else []):
        if not child.is_dir():
            continue
        state = read_state(child)
        touched = state.get("touched_at")
        if touched is None:
            # No state file: fall back to the folder's own mtime, so
            # pre-US-31.8 per-issue directories are reclaimable too.
            try:
                touched = child.stat().st_mtime
            except OSError:
                continue
        if touched >= cutoff:
            continue
        size = dir_size_bytes(child)
        shutil.rmtree(child, ignore_errors=True)
        removed.append({"name": child.name, "bytes": size})
        logger.info("reclaimed workspace %s (%d bytes)", child.name, size)
    return {
        "removed": removed,
        "freed_bytes": sum(r["bytes"] for r in removed),
    }


def reclaim_legacy_issue_dirs() -> dict[str, Any]:
    """US-31.8: the per-issue folders this story replaces, on a live machine.

    They are orphaned the moment the path scheme changes — nothing will ever
    look in them again — so they are removed on sight rather than waiting out
    the reclaim age.
    """
    root = gitwork.workspace_root()
    removed: list[dict[str, Any]] = []
    for child in sorted(root.iterdir() if root.exists() else []):
        if child.is_dir() and child.name.startswith("issue-"):
            size = dir_size_bytes(child)
            shutil.rmtree(child, ignore_errors=True)
            removed.append({"name": child.name, "bytes": size})
    if removed:
        logger.info(
            "reclaimed %d legacy per-issue workspace(s), %d bytes",
            len(removed),
            sum(r["bytes"] for r in removed),
        )
    return {
        "removed": removed,
        "freed_bytes": sum(r["bytes"] for r in removed),
    }
