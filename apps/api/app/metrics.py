"""Change metrics parsed from a run's stored unified diff (US-1.17).

Shared by db.complete_run (Python) so the frontend never re-implements the
area heuristic — it just reads the `area` field already stored per file.
"""

import re
from typing import Any

FRONTEND_MARKERS = ("apps/web/", "src/components/", "src/app/")
FRONTEND_EXTS = (".tsx", ".jsx", ".css", ".scss")
BACKEND_MARKERS = ("api/", "server/", "infra/")
BACKEND_EXTS = (".sql", ".py")


def classify_area(path: str) -> str:
    """frontend | backend | other, by path/extension heuristic."""
    lower = path.lower()
    if any(m in lower for m in FRONTEND_MARKERS) or lower.endswith(FRONTEND_EXTS):
        return "frontend"
    if any(m in lower for m in BACKEND_MARKERS) or lower.endswith(BACKEND_EXTS):
        return "backend"
    return "other"


def _file_path(lines: list[str]) -> str | None:
    for line in lines:
        if line.startswith("+++ "):
            p = line[4:].strip()
            if p != "/dev/null":
                return p[2:] if p.startswith("b/") else p
    for line in lines:
        if line.startswith("--- "):
            p = line[4:].strip()
            if p != "/dev/null":
                return p[2:] if p.startswith("a/") else p
    return None


def compute_diff_metrics(diff: str | None) -> dict[str, Any] | None:
    """Parse a stored unified diff into lines_added/removed, files_changed,
    and a per-file change_breakdown. None if there's no diff to parse —
    callers must leave the run's metric columns null, not 0."""
    if not diff or not diff.strip():
        return None

    chunks = [c for c in re.split(r"(?m)^(?=diff --git )", diff) if c.strip()]
    if not chunks:
        return None

    per_file: dict[str, dict[str, int]] = {}
    order: list[str] = []
    for chunk in chunks:
        lines = chunk.splitlines()
        path = _file_path(lines) or lines[0].replace("diff --git ", "").strip()
        added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
        if path not in per_file:
            per_file[path] = {"added": 0, "removed": 0}
            order.append(path)
        per_file[path]["added"] += added
        per_file[path]["removed"] += removed

    breakdown = [
        {
            "path": p,
            "added": per_file[p]["added"],
            "removed": per_file[p]["removed"],
            "area": classify_area(p),
        }
        for p in order
    ]
    return {
        "lines_added": sum(f["added"] for f in per_file.values()),
        "lines_removed": sum(f["removed"] for f in per_file.values()),
        "files_changed": len(per_file),
        "change_breakdown": breakdown,
    }
