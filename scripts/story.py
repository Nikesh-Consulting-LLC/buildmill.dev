#!/usr/bin/env python3
"""Story lifecycle tool — keeps a story file's **Status:** line and its
stories/users.md rows in sync, in one command.

  python scripts/story.py set us-48.1 Testing    # update file + every index row
  python scripts/story.py check                  # audit the whole tree (CI runs this)
  python scripts/story.py list --status Testing  # surface stragglers

Statuses: New -> Testing -> Completed. Only the user confirms Completed —
the tool prints a reminder but does not enforce it, because the human (or an
agent acting on an explicit go-ahead) is the one running it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STORIES = REPO / "stories"
USERS_MD = STORIES / "users.md"
STATUSES = ("New", "Testing", "Completed")
STATUS_RE = re.compile(r"^\*\*Status:\*\*\s*(.+?)\s*$", re.MULTILINE)


def story_files() -> list[Path]:
    return sorted(p for p in STORIES.glob("us-*.md"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def file_status(path: Path) -> str | None:
    m = STATUS_RE.search(read(path))
    return m.group(1) if m else None


def resolve(story_id: str) -> Path:
    story_id = story_id.lower()
    matches = [p for p in story_files() if p.name.lower().startswith(story_id + "-")]
    if not matches:
        sys.exit(f"error: no file stories/{story_id}-*.md")
    if len(matches) > 1:
        sys.exit(f"error: ambiguous id {story_id}: " + ", ".join(p.name for p in matches))
    return matches[0]


def index_rows(text: str, filename: str) -> list[tuple[int, str]]:
    """(line_no, line) for every users.md table row linking to filename."""
    rows = []
    for i, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("|") and f"]({filename})" in line:
            rows.append((i, line))
    return rows


def row_status(line: str) -> str | None:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells[-1] if cells else None


def set_row_status(line: str, status: str) -> str:
    # replace the content of the last cell, preserving the trailing pipe
    return re.sub(r"\|[^|]*\|\s*$", f"| {status} |", line)


def cmd_set(args: argparse.Namespace) -> int:
    if args.status not in STATUSES:
        sys.exit(f"error: status must be one of {', '.join(STATUSES)}")
    path = resolve(args.id)
    text = read(path)
    if not STATUS_RE.search(text):
        sys.exit(f"error: {path.name} has no **Status:** line")
    old = STATUS_RE.search(text).group(1)
    write(path, STATUS_RE.sub(f"**Status:** {args.status}", text, count=1))

    users = read(USERS_MD)
    lines = users.splitlines()
    rows = index_rows(users, path.name)
    for i, line in rows:
        lines[i] = set_row_status(line, args.status)
    write(USERS_MD, "\n".join(lines) + "\n")

    print(f"{path.name}: {old} -> {args.status} ({len(rows)} users.md row(s) updated)")
    if not rows:
        print("warning: no users.md row links to this file — add one", file=sys.stderr)
    if args.status == "Completed":
        print("reminder: only the user confirms Completed; when the phase closes, "
              "delete its story files and condense the essence into APPLICATION.md's "
              "Delivery history (a separate, deliberate step)")
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    users = read(USERS_MD)
    problems = []
    linked: set[str] = set(re.findall(r"\]\((us-[^)/]+\.md)\)", users))

    for path in story_files():
        status = file_status(path)
        if status is None:
            problems.append(f"{path.name}: no **Status:** line")
            continue
        if status not in STATUSES and status != "Standing":
            problems.append(f"{path.name}: unknown status {status!r}")
        rows = index_rows(users, path.name)
        if not rows and f"]({path.name})" not in users:
            problems.append(f"{path.name}: not indexed in users.md")
        for i, line in rows:
            rs = row_status(line)
            if rs != status:
                problems.append(
                    f"users.md:{i + 1}: row says {rs!r} but {path.name} says {status!r}")

    existing = {p.name for p in story_files()}
    for name in sorted(linked - existing):
        problems.append(f"users.md links to missing file {name}")

    if problems:
        print("story sync check FAILED:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"story sync check OK ({len(existing)} stories, users.md consistent)")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for path in story_files():
        status = file_status(path)
        if args.status is None or status == args.status:
            print(f"{status or '(no status)':<10} {path.name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="set a story's status everywhere")
    p_set.add_argument("id", help="story id, e.g. us-48.1")
    p_set.add_argument("status", choices=STATUSES)
    p_set.set_defaults(func=cmd_set)

    p_check = sub.add_parser("check", help="verify story files and users.md agree")
    p_check.set_defaults(func=cmd_check)

    p_list = sub.add_parser("list", help="list stories by status")
    p_list.add_argument("--status", choices=STATUSES, default=None)
    p_list.set_defaults(func=cmd_list)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
