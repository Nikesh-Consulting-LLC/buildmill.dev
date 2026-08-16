"""US-13.4: the factory writes what it approves into the project's own
repository, under docs/factory/ — the approved PRD per feature and one
file per story (story text, acceptance criteria, approved plan) — so the
repo is readable ground truth for every agent (and human) that follows.

The app owns this tree. It is a projection of approved state, rebuilt
wholesale on every write: re-approval overwrites, outside edits vanish
on the next write, and nothing is ever read back (one writer, one
direction). Only approved artifacts land here; mutable state — status,
assignees, comments, clarifications, runs — stays in Supabase. Writing
is opt-in per project (projects.docs_tree_enabled, off by default), and
a write failure is surfaced but never fails the approval that triggered
it.
"""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Coroutine

from . import db, github, github_tokens
from .config import Settings

DOCS_ROOT = "docs/factory"

# US-69.3: approval and dispatch now run their docs syncs off the request
# path, which means two syncs for the same project can overlap. One lock per
# project serializes them; the loser lands on the winner's commit and exits
# through the existing unchanged-tree short-circuit instead of racing
# update_ref.
_project_locks: dict[str, asyncio.Lock] = {}

# Fire-and-forget tasks must be strongly referenced or the event loop may
# garbage-collect them mid-flight.
_background_tasks: set[asyncio.Task[Any]] = set()


def _lock_for(project_id: str) -> asyncio.Lock:
    return _project_locks.setdefault(project_id, asyncio.Lock())


def spawn_background(coro: Coroutine[Any, Any, Any]) -> None:
    """Run a best-effort coroutine after the response goes out (US-69.3)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

# US-22.6: one factory-owned region, in both instruction files. The markers
# are the whole contract — everything outside them belongs to whoever wrote
# it and survives every write.
BLOCK_START = "<!-- buildmill:instructions:start -->"
BLOCK_END = "<!-- buildmill:instructions:end -->"

# The pre-22.6 markers fenced a docs-tree-only section. A repo that still
# carries them gets it stripped as the new block lands, so no project ends
# up with two factory regions saying different things.
LEGACY_START = "<!-- buildmill:docs-tree:start -->"
LEGACY_END = "<!-- buildmill:docs-tree:end -->"

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
CLAUDE_MD_POINTER = "@AGENTS.md\n"

README = """# Factory documentation tree

Build Mill (the software factory) writes the project's work into this
folder: one folder per feature, named for the feature's work-item id,
holding its approved PRD (`prd.md`) and one file per story named for the
story's id.

## Finding things

- `index.json` — every item in build order, with its id, parentage,
  whether it has an approved plan, and its path. One read; no parsing of
  prose. This is what an agent should use.
- `INDEX.md` — the same list with titles and links, for humans.
- Every `.md` file opens with YAML front matter carrying the same fields,
  so `grep` over the tree works too.

Paths are **ids, not titles** (`us-4.1/us-4.2.md`). Retitling an item
changes its `H1` and its index entry and moves nothing.

## What appears here, and when

- A **feature** appears once its PRD is approved.
- A **story** appears once it is dispatched — before anyone has planned
  it. `has_plan: false` says so. This is deliberate: an agent needs to see
  the stories around it, not just the one in front of it.
- A story that has shipped gains an `## Outcome` section naming the merge
  commit, the files it touched, and what the agent said on the way out.

**Build Mill owns this tree.** It is a projection of the factory's state,
regenerated wholesale on every write:

- Edits made here by hand are overwritten on the next write and are not
  recovered — change the source of truth in Build Mill instead.
- A file the factory stops generating is deleted, so everything you find
  here is current.
- Mutable state — status, assignees, comments, run history — lives in
  Build Mill only.
- Git history carries the previous versions.
"""

DOCS_TREE_SECTION = f"""## Factory documentation tree

The project's requirements live in this repository under `{DOCS_ROOT}/`.

**It is already on disk.** A code run receives the repo as a workspace
pinned to a commit, so this is a local directory — read it with your normal
file tools. No `get_repo_tree` or `read_repo_file` call is needed.

**Addressing.** Paths are work-item ids, never titles, so a link written
today still resolves after somebody rewords a title:

```
{DOCS_ROOT}/index.json        every item, in build order
{DOCS_ROOT}/INDEX.md          the same list, for humans
{DOCS_ROOT}/us-4.1/prd.md     a feature's approved PRD
{DOCS_ROOT}/us-4.1/us-4.2.md  a story in that feature
```

**Read `index.json` first.** One read answers what exists, in what order,
and where — instead of walking the tree and parsing prose. Each entry, and
the YAML front matter at the top of every `.md` file, carries:

| key | meaning |
| --- | --- |
| `id` | the work-item display id, e.g. `US-4.2` |
| `issue_id` | the uuid, for MCP calls |
| `type` | `story` or `feature` |
| `title` | the title as approved |
| `parent` | the feature this story belongs to; `null` for a feature |
| `epic` | the epic number |
| `order` | position in build order |
| `has_plan` | an approved implementation plan is in this file |
| `has_test_plan` | an approved test plan is in this file |
| `merge_commit` | the commit that shipped it, or `null` |
| `generated_at` | when this tree was written |

A story with `has_plan: false` has been dispatched but not planned — it
carries the requirement only. It is still worth reading: it tells you what
is coming and what is not yours to build.

**Before you design, read the stories that precede yours in the same
feature.** Their approved plans and their `## Outcome` sections say what
was already decided and what actually shipped, so you extend that shape
instead of inventing a competing one.

**Build Mill owns this tree.** It is regenerated wholesale from approved
state and never read back, so edits made here are overwritten and lost —
change the source of truth in Build Mill instead."""


def _display_id(issue: dict[str, Any]) -> str:
    did = db.work_item_display_id(
        issue.get("type"),
        issue.get("epic_number"),
        issue.get("item_no"),
        issue.get("sub_no"),
    )
    return did or str(issue["id"])[:8]


def _dir_for(container: dict[str, Any]) -> str:
    """US-22.2: the display id alone. Titles get edited — a normal, frequent,
    low-stakes act — and a title-slugged path turns every one of those into a
    file move that breaks links and splits the file's git history. The id is
    already the stable key: it is what commits, branches and PR titles use.
    The title lives in the H1 and the index, where it stays readable."""
    return f"{DOCS_ROOT}/{_display_id(container).lower()}"


def _story_filename(issue: dict[str, Any]) -> str:
    return f"{_display_id(issue).lower()}.md"


def _ac_markdown(ac: Any) -> str:
    if isinstance(ac, list) and ac:
        lines = []
        for item in ac:
            text = item if isinstance(item, str) else str(item)
            lines.append(f"- {text}")
        return "\n".join(lines)
    return "_None recorded._"


def _yaml_scalar(value: Any) -> str:
    """Just enough YAML for the fixed set of front-matter values we emit:
    nulls, bools, ints, and quoted strings. Titles routinely carry colons,
    quotes and em dashes, so strings are always double-quoted and escaped."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", " ").strip()
    return f'"{text}"'


def _front_matter(fields: dict[str, Any]) -> str:
    """US-22.3: fixed, greppable identity as the file's first bytes.

    Every key is always present — an agent should never have to tell "no
    parent" apart from "this generator version didn't write parents" — and
    the `---` fence is line one, so GitHub still renders the body."""
    lines = ["---"]
    for key, value in fields.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _identity(
    issue: dict[str, Any],
    parent: dict[str, Any] | None,
    order: int,
    arts: dict[str, str],
    outcomes: list[dict[str, Any]],
    generated_at: str,
) -> dict[str, Any]:
    """The front-matter fields for one item — also the entry `index.json`
    publishes, so the two indexes cannot disagree about what exists."""
    is_feature = issue.get("type") == "feature"
    return {
        "id": _display_id(issue),
        "issue_id": str(issue["id"]),
        "type": "feature" if is_feature else "story",
        "title": issue.get("title"),
        "parent": _display_id(parent) if parent is not None else None,
        "epic": issue.get("epic_number"),
        "order": order,
        "has_plan": bool(arts.get("plan")),
        "has_test_plan": bool(arts.get("test_plan")),
        "merge_commit": (outcomes[-1].get("commit_sha") if outcomes else None),
        "generated_at": generated_at,
    }


def _prd_doc(feature: dict[str, Any], prd: str, identity: dict[str, Any]) -> str:
    return (
        _front_matter(identity)
        + f"\n# {_display_id(feature)} — {feature['title']}\n\n"
        "> Approved PRD, written by Build Mill. The app owns this file; "
        "edits here are overwritten on the next approval.\n\n"
        f"{prd.strip()}\n"
    )


def _outcome_section(outcomes: list[dict[str, Any]]) -> str:
    """US-22.5: what actually shipped, derived from the approved code runs on
    every rebuild — never appended to the file, or the repo would become the
    source of truth for one section and the one-writer property would break.

    Everything here is frozen at merge (a sha, a PR url, a file list, the
    notes the agent handed back), which is what makes it compatible with the
    US-13.4 rule that keeps mutable state out."""
    parts = ["## Outcome\n"]
    for out in outcomes:
        sha = (out.get("commit_sha") or "")[:7]
        when = str(out.get("merged_at") or "")[:10]
        line = f"Merged `{sha}`"
        pr = out.get("pr_url") or ""
        if pr.startswith("http"):
            number = pr.rstrip("/").rsplit("/", 1)[-1]
            line += f" · [PR #{number}]({pr})"
        if when:
            line += f" · {when}"
        parts.append(line + "\n")
        breakdown = out.get("change_breakdown")
        if isinstance(breakdown, list) and breakdown:
            files = ", ".join(
                str(f.get("path")) for f in breakdown if isinstance(f, dict)
            )
            if files:
                parts.append(f"Files changed: {files}\n")
        notes = (out.get("handback_notes") or "").strip()
        if notes:
            parts.append(f"**Notes from the agent:** {notes}\n")
    return "\n".join(parts)


def _story_doc(
    issue: dict[str, Any],
    arts: dict[str, str],
    identity: dict[str, Any],
    outcomes: list[dict[str, Any]],
) -> str:
    planned = bool(arts.get("plan") or arts.get("test_plan"))
    parts = [
        _front_matter(identity).rstrip("\n"),
        f"\n# {_display_id(issue)} — {issue['title']}\n",
        (
            "> Approved story and plan, written by Build Mill. The app owns "
            "this file; edits here are overwritten on the next approval.\n"
            if planned
            else
            # US-22.4: dispatched but not yet planned. Saying so plainly is
            # the point — an agent reading ahead needs to know this story is
            # coming and that nobody has decided how yet.
            "> Dispatched story, written by Build Mill. No implementation "
            "plan has been approved yet — this file carries the requirement "
            "only. The app owns it; edits here are overwritten.\n"
        ),
        f"## Story\n\n{(issue.get('body') or '').strip() or '_No story text._'}\n",
        f"## Acceptance criteria\n\n{_ac_markdown(issue.get('acceptance_criteria'))}\n",
    ]
    if arts.get("plan"):
        parts.append(f"## Approved implementation plan\n\n{arts['plan'].strip()}\n")
    if arts.get("test_plan"):
        parts.append(f"## Approved test plan\n\n{arts['test_plan'].strip()}\n")
    if outcomes:
        parts.append(_outcome_section(outcomes))
    return "\n".join(parts)


def build_tree(
    issues: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    outcomes: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> dict[str, str]:
    """The whole docs tree as {path: content} — pure, so tests need no
    GitHub.

    A feature produces a file once its PRD is approved; a story produces one
    once the manager has **dispatched** it (US-22.4), whether or not a plan
    exists yet, so the backlog ahead of an agent is visible rather than
    materialising one story at a time. `INDEX.md` and `index.json` are
    generated from the same structure in the same pass, so they cannot
    disagree."""
    stamp = generated_at or datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    arts_by_issue: dict[str, dict[str, str]] = {}
    for a in artifacts:  # ordered by version — the latest naturally wins
        arts_by_issue.setdefault(str(a["issue_id"]), {})[a["kind"]] = a["content"]
    outcomes_by_issue: dict[str, list[dict[str, Any]]] = {}
    for o in outcomes or ():  # ordered oldest first — the ledger's order
        outcomes_by_issue.setdefault(str(o["issue_id"]), []).append(o)
    issue_by_id = {str(i["id"]): i for i in issues}

    files: dict[str, str] = {}
    index: list[dict[str, Any]] = []
    # index_entries: (container, prd_path | None, [(story, path, identity), ...])
    index_entries: dict[str, dict[str, Any]] = {}

    def entry_for(container: dict[str, Any]) -> dict[str, Any]:
        key = str(container["id"])
        if key not in index_entries:
            index_entries[key] = {"container": container, "prd": None, "stories": []}
        return index_entries[key]

    position = 0
    for issue in issues:
        arts = arts_by_issue.get(str(issue["id"]), {})
        outs = outcomes_by_issue.get(str(issue["id"]), [])
        if issue.get("type") == "feature":
            if arts.get("prd"):
                position += 1
                identity = _identity(issue, None, position, arts, outs, stamp)
                path = f"{_dir_for(issue)}/prd.md"
                files[path] = _prd_doc(issue, arts["prd"], identity)
                entry_for(issue)["prd"] = path
                index.append({**identity, "path": path})
        elif arts.get("plan") or arts.get("test_plan") or issue.get("dispatched"):
            parent = issue_by_id.get(str(issue.get("parent_id") or ""))
            container = parent or issue
            position += 1
            identity = _identity(issue, parent, position, arts, outs, stamp)
            path = f"{_dir_for(container)}/{_story_filename(issue)}"
            files[path] = _story_doc(issue, arts, identity, outs)
            entry_for(container)["stories"].append((issue, path, identity))
            index.append({**identity, "path": path})

    lines = [
        "# Index — factory documentation tree",
        "",
        "Generated by Build Mill. A feature appears once its PRD is "
        "approved; a story appears once it is dispatched, in build order — "
        "read the stories above yours before designing. Stories marked "
        "_(no plan yet)_ carry the requirement only.",
        "",
        f"Machine-readable: [`index.json`](index.json) — same items, same "
        "order, one read.",
        "",
    ]
    for entry in index_entries.values():
        c = entry["container"]
        if entry["prd"]:
            lines.append(
                f"- **{_display_id(c)} — {c['title']}** · [PRD]({entry['prd'][len(DOCS_ROOT) + 1:]})"
            )
        else:
            lines.append(f"- **{_display_id(c)} — {c['title']}**")
        for story, path, identity in entry["stories"]:
            label = "story + plan" if identity["has_plan"] else "story"
            suffix = "" if identity["has_plan"] else " · _(no plan yet)_"
            lines.append(
                f"  - {_display_id(story)} — {story['title']} · "
                f"[{label}]({path[len(DOCS_ROOT) + 1:]}){suffix}"
            )
    if not index_entries:
        lines.append("_Nothing approved yet._")
    files[f"{DOCS_ROOT}/INDEX.md"] = "\n".join(lines) + "\n"
    files[f"{DOCS_ROOT}/index.json"] = (
        json.dumps(index, indent=2, ensure_ascii=False) + "\n"
    )
    files[f"{DOCS_ROOT}/README.md"] = README
    return files


def build_instruction_block(
    guidelines: str | None, docs_tree_enabled: bool
) -> str:
    """US-22.6: the one block the factory owns, in both instruction files —
    the project's assembled guidelines plus the docs-tree pointer.

    A project with the tree disabled still gets its guidelines: the two
    halves are independent, and the tree flag only decides whether there is
    a tree worth pointing at."""
    parts = [BLOCK_START, ""]
    body = (guidelines or "").strip()
    if body:
        parts.append(body)
        parts.append("")
    if docs_tree_enabled:
        parts.append(DOCS_TREE_SECTION)
        parts.append("")
    if not body and not docs_tree_enabled:
        parts.append(
            "_Build Mill has no guidelines for this project yet._"
        )
        parts.append("")
    parts.append(BLOCK_END)
    return "\n".join(parts)


def build_agents_index(
    kinds_present: list[str],
    conventions: str | None,
    docs_tree_enabled: bool,
) -> str:
    """US-99.2 / us-100.2: AGENTS.md, written WHOLE, and it IS the document.

    It used to be a shared file — the factory owned a fenced region and
    `merge_block` was careful never to touch a byte outside it. That was
    right when the block held only guidelines and a docs-tree pointer. It
    stops being right the moment AGENTS.md becomes the entry point to a set
    of files Build Mill maintains, because then half a file being
    authoritative and half being somebody's notes is a question every agent
    has to answer for itself.

    So Build Mill owns it outright. **This destroys hand-written AGENTS.md
    content on first publish** — the accepted cost of single ownership, made
    visible by happening inside a commit, on a branch with history, at a
    moment the manager chose (us-99.4) rather than silently at dispatch.
    """
    from .instruction_files import KIND_FILES, ROOT

    lines: list[str] = []
    # us-100.2: the Agent Instructions ARE the body. us-99.2 made this file an
    # index that pointed at `.buildmill/Guidelines.md`; once the conventions
    # are one document (us-100.1) that indirection is just a redirect — the
    # file every agent opens first saying "the real thing is over there".
    body = (conventions or "").strip()
    if body:
        # The rule separates the manager's document from the generated tail.
        # Only meaningful when there IS a document above it — otherwise the
        # file opens with a horizontal rule over nothing.
        lines += [body, "", "---", ""]

    lines += [
        "Build Mill owns this file and everything under "
        f"`{ROOT}/`, and rewrites them whole on each publish. Edits made "
        "here are replaced — change them in Build Mill instead.",
        "",
    ]
    if kinds_present:
        lines += [
            "## Instructions by task",
            "",
            "Read the one that matches the run you are doing. Each is the "
            "whole brief for that task.",
            "",
            "| Task | File |",
            "| --- | --- |",
        ]
        for kind in sorted(kinds_present):
            label = KIND_META.get(kind, kind)
            name = KIND_FILES[kind]
            lines.append(f"| {label} | [`{ROOT}/{name}`]({ROOT}/{name}) |")
        lines.append("")
    if not kinds_present and not body:
        lines += [
            "_Build Mill has published no instructions for this project "
            "yet._",
            "",
        ]
    if docs_tree_enabled:
        lines += [DOCS_TREE_SECTION, ""]
    return "\n".join(lines).rstrip() + "\n"


#: One line per kind, naming who receives it — the same vocabulary the
#: Settings editor uses, so the repo and the app describe a task identically.
KIND_META: dict[str, str] = {
    "prd": "Drafting a feature's PRD",
    "breakdown": "Splitting an approved PRD into stories",
    "elaborate": "Expanding a rough story",
    "wireframe": "Drawing a story's UI before it is built",
    "plan": "Planning a story that belongs to a feature",
    "standalone_plan": "Planning a standalone story",
    "bug_rca": "Diagnosing a bug (root cause analysis)",
    "code": "Building a story that belongs to a feature",
    "standalone_code": "Building a standalone story",
    "bug_fix": "Fixing a bug from an approved RCA",
    "chore": "Building a chore (single shot)",
    "merge": "Landing branches onto the default branch",
    "test": "Executing test cases and reporting results",
    "deploy": "Running and verifying one deployment",
    "release": "Preparing a release",
    "guidelines": "Proposing changes to the project's conventions",
}


def instruction_file_plan(
    instructions: dict[str, str],
    conventions: str | None,
    docs_tree_enabled: bool,
) -> tuple[dict[str, str], set[str]]:
    """(files to write, paths to delete) for one publish — pure, so the whole
    shape is testable without GitHub.

    A kind resolving to blank is DELETED rather than written empty: a
    repository must never carry an instruction the factory no longer
    believes in, and an empty file reads as "no guidance" rather than "not
    applicable".
    """
    from .instruction_files import CONVENTIONS_FILE, KIND_FILES, ROOT

    files: dict[str, str] = {}
    deletes: set[str] = set()

    for kind, name in KIND_FILES.items():
        path = f"{ROOT}/{name}"
        body = (instructions.get(kind) or "").strip()
        if body:
            files[path] = body.rstrip() + "\n"
        else:
            deletes.add(path)

    # us-100.2: `.buildmill/Guidelines.md` RETIRES. It shipped hours earlier
    # in us-99.3 and is superseded here — the conventions are AGENTS.md's body
    # now. Deleted unconditionally so a project that published under us-99.3
    # does not keep a stale conventions file that disagrees with AGENTS.md.
    # publish_hash covers deletions, so this registers as a change and the
    # next publish actually removes it.
    deletes.add(f"{ROOT}/{CONVENTIONS_FILE}")

    files["AGENTS.md"] = build_agents_index(
        [k for k in KIND_FILES if (instructions.get(k) or "").strip()],
        conventions,
        docs_tree_enabled,
    )
    # us-99.2 AC4: CLAUDE.md is the pointer, unconditionally. It was already
    # scaffolded that way; the preservation branch that merged a block into a
    # CLAUDE.md carrying other content retires with the block itself.
    files["CLAUDE.md"] = CLAUDE_MD_POINTER
    return files, deletes


def _strip_legacy_block(text: str) -> str:
    """Remove the pre-22.6 docs-tree-only region, if the repo still has one."""
    if LEGACY_START in text and LEGACY_END in text:
        pre = text.split(LEGACY_START)[0]
        post = text.split(LEGACY_END, 1)[1]
        return pre.rstrip() + ("\n" + post.lstrip("\n") if post.strip() else "\n")
    return text


def merge_block(current: str | None, block: str) -> str:
    """Replace the factory region in place when the markers exist, append it
    when they don't, and never touch a byte outside them."""
    text = _strip_legacy_block(current or "")
    if BLOCK_START in text and BLOCK_END in text:
        pre = text.split(BLOCK_START)[0]
        post = text.split(BLOCK_END, 1)[1]
        return pre + block + post
    if not text.strip():
        return block + "\n"
    return text.rstrip() + "\n\n" + block + "\n"


def _is_pointer_only(current: str | None) -> bool:
    """A CLAUDE.md that is absent, empty, or exactly the `@AGENTS.md` pointer
    is one Build Mill scaffolded, so the pointer stays the right answer. One
    that carries anything else is somebody's file, and gets the block merged
    into it instead of being flattened (US-22.6)."""
    return (current or "").strip() in ("", CLAUDE_MD_POINTER.strip())


def instruction_files(
    block: str, current_agents: str | None, current_claude: str | None
) -> dict[str, str]:
    """Both instruction files as {path: content} — pure, so the preservation
    and pointer rules are testable without GitHub."""
    return {
        "AGENTS.md": merge_block(current_agents, block),
        "CLAUDE.md": (
            CLAUDE_MD_POINTER
            if _is_pointer_only(current_claude)
            else merge_block(current_claude, block)
        ),
    }


async def _current_instruction_files(
    token: str, owner: str, repo: str, branch: str
) -> dict[str, str | None]:
    """What the branch holds for each instruction file today. A missing file
    reads as None — the same as empty, for merge purposes."""
    out: dict[str, str | None] = {}
    for path in INSTRUCTION_FILES:
        try:
            data = await github.get_content(token, owner, repo, path, branch)
            if isinstance(data, dict) and data.get("type") == "file":
                out[path] = base64.b64decode(data.get("content") or "").decode(
                    "utf-8", "replace"
                )
            else:
                out[path] = None
        except github.GitHubError:
            out[path] = None
    return out


async def build_instruction_file_contents(
    token: str,
    repo_full: str,
    branch: str,
    guidelines: str | None,
    docs_tree_enabled: bool,
) -> tuple[dict[str, str], str]:
    """The merged contents of both instruction files, plus the block they
    carry. The single path every writer goes through (US-22.6), so pressing
    Save instructions and approving a plan produce identical files."""
    owner, repo = repo_full.split("/", 1)
    block = build_instruction_block(guidelines, docs_tree_enabled)
    current = await _current_instruction_files(token, owner, repo, branch)
    return (
        instruction_files(block, current["AGENTS.md"], current["CLAUDE.md"]),
        block,
    )


async def existing_docs_paths(
    token: str, owner: str, repo: str, branch: str, root: str = DOCS_ROOT
) -> set[str]:
    """Every path currently under `root` on the branch (US-22.1).

    A missing ref — an empty repo, or a first scaffold — is a legitimately
    empty set. Any other listing failure propagates: a sync must never
    conclude "nothing to delete" because it could not look.

    US-48.2 passes `docs/wireframes` here. The blast radius of a deletion is
    whatever root the caller names, which is what keeps the two trees from
    ever being able to delete each other's files."""
    try:
        tree = await github.get_tree(token, owner, repo, branch)
    except github.GitHubError as e:
        if "not found" in str(e):
            return set()
        raise
    prefix = f"{root}/"
    return {
        entry["path"]
        for entry in tree.get("tree") or []
        if entry.get("type") == "blob"
        and str(entry.get("path", "")).startswith(prefix)
    }


async def commit_files(
    token: str,
    repo_full: str,
    branch: str,
    message: str,
    files: dict[str, str],
    deletes: set[str] | None = None,
) -> dict[str, Any]:
    """One commit on the branch head carrying every file (blobs → tree →
    commit → ref). Empty repo: seeded file-by-file via the contents API,
    which can create the first commit. Skips committing when nothing
    changed.

    US-22.1: `deletes` are paths to remove, emitted as `sha: null` entries.
    GitHub merges a tree against `base_tree`, so a path the generator has
    stopped producing survives forever unless it is deleted explicitly —
    which is how a retitled story used to fork into two greppable files."""
    owner, repo = repo_full.split("/", 1)
    ref = await github.get_ref(token, owner, repo, branch)
    if ref is None:
        last: dict[str, Any] | None = None
        for path, content in files.items():
            last = await github.create_or_update_file(
                token, owner, repo, path, content, message, branch, None
            )
        return {"commit_sha": ((last or {}).get("commit") or {}).get("sha")}
    head = ref["object"]["sha"]
    base_commit = await github.get_commit(token, owner, repo, head)
    base_tree = base_commit["commit"]["tree"]["sha"]
    entries = []
    for path, content in files.items():
        blob = await github.create_blob(
            token, owner, repo, base64.b64encode(content.encode()).decode()
        )
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob}
        )
    if deletes:
        # A `sha: null` entry for a path the base tree does not hold makes
        # GitHub refuse the whole tree with `GitRPC::BadObjectState` (found on
        # live 2026-08-15: the instruction publish deletes every kind with no
        # content, most of which never existed in the repo). Delete only what
        # is there. A truncated listing cannot prove absence, so it keeps
        # every delete rather than silently leaving stale files behind.
        listing = await github.get_tree(token, owner, repo, base_tree)
        if not listing.get("truncated"):
            present = {e["path"] for e in listing.get("tree", []) if e.get("type") == "blob"}
            deletes = {p for p in deletes if p in present}
    for path in sorted(deletes or ()):
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": None}
        )
    tree_sha = await github.create_tree(token, owner, repo, base_tree, entries)
    if tree_sha == base_tree:
        return {"unchanged": True, "commit_sha": head}
    commit_sha = await github.create_commit(
        token,
        owner,
        repo,
        message,
        tree_sha,
        head,
        author_name="Build Mill",
        author_email="factory@buildmill.dev",
    )
    await github.update_ref(token, owner, repo, branch, commit_sha)
    return {"commit_sha": commit_sha}


async def sync_tree(
    settings: Settings, project_id: str, trigger: str = "sync"
) -> dict[str, Any]:
    """Rebuild and commit the whole docs tree from approved state. The
    scaffold, the per-approval write, and the retry are all this one
    operation — a full rebuild is what makes re-approval overwrite and
    keeps the index honest."""
    async with _lock_for(project_id):
        return await _sync_tree_locked(settings, project_id, trigger)


async def _sync_tree_locked(
    settings: Settings, project_id: str, trigger: str
) -> dict[str, Any]:
    project = db.get_project_docs_config(settings, project_id)
    if not project:
        return {"skipped": "project not found"}
    if not project.get("docs_tree_enabled"):
        return {"skipped": "docs tree is not enabled for this project"}
    repo_full = project.get("repo_full_name") or ""
    if "/" not in repo_full:
        return {"skipped": "no linked repository"}
    branch = project.get("default_branch") or "main"
    owner, repo = repo_full.split("/", 1)

    issues, artifacts, outcomes = db.list_approved_docs(
        settings, project_id, str(project["org_id"])
    )
    files = build_tree(issues, artifacts, outcomes)

    token = await github_tokens.token_for_org(
        settings, str(project["org_id"]), repo_full
    )
    # US-22.6/22.7: the tree and the instruction files land in one commit, so
    # the repo never holds an AGENTS.md describing a tree the same push has
    # not yet created.
    instructions, block = await build_instruction_file_contents(
        token, repo_full, branch, project.get("guidelines"), True
    )

    # US-22.1: everything under docs/factory/ that this generation does not
    # produce is deleted. The blast radius stops at DOCS_ROOT — the
    # instruction files are merged, never generated, so they are never
    # candidates even though the same commit writes them.
    existing = await existing_docs_paths(token, owner, repo, branch)
    deletes = existing - set(files)

    files.update(instructions)
    result = await commit_files(
        token,
        repo_full,
        branch,
        f"docs: factory docs tree ({trigger})",
        files,
        deletes,
    )
    if result.get("commit_sha") and not result.get("unchanged"):
        db.record_instructions_sync(
            settings, project_id, digest, result["commit_sha"]
        )
    return {"files": sorted(files), "deleted": sorted(deletes), **result}


def block_hash(block: str) -> str:
    """US-22.7: what was last successfully committed, so a dispatch that
    changes nothing costs no GitHub call."""
    return hashlib.sha256(block.encode()).hexdigest()


def publish_hash(files: dict[str, str], deletes: set[str]) -> str:
    """US-99.2: the fingerprint of a whole publish — every file's content AND
    every deletion.

    Deletions are in the hash deliberately. The old `block_hash` could only
    see the text of one block, so a kind whose instruction was cleared
    produced no change it could detect and the stale file stayed in the
    repository forever. Sorted, so map ordering can never move the digest.
    """
    parts = [f"F:{p}\n{files[p]}" for p in sorted(files)]
    parts += [f"D:{p}" for p in sorted(deletes)]
    return hashlib.sha256("\n\x00\n".join(parts).encode()).hexdigest()


async def sync_instruction_files(
    settings: Settings, project_id: str, trigger: str = "dispatch"
) -> dict[str, Any]:
    """US-22.7: bring AGENTS.md / CLAUDE.md up to date if — and only if — the
    assembled block differs from the hash recorded for the last successful
    write. Called before a plan or code run is queued, so no agent ever
    starts against instructions the manager has already superseded.

    Never raises: dispatch must not depend on GitHub being up."""
    async with _lock_for(project_id):
        return await _sync_instruction_files_locked(settings, project_id, trigger)


async def _sync_instruction_files_locked(
    settings: Settings, project_id: str, trigger: str
) -> dict[str, Any]:
    project = db.get_project_docs_config(settings, project_id)
    if not project:
        return {"skipped": "project not found"}
    repo_full = project.get("repo_full_name") or ""
    if "/" not in repo_full:
        return {"skipped": "no linked repository"}
    branch = project.get("default_branch") or "main"

    # us-99.2: the whole published set, not a block. The hash covers every
    # file AND every deletion, so a kind going blank is a change the next
    # publish notices — the old block hash could not see that at all.
    instructions = db.get_project_instructions_for_publish(settings, project_id)
    files, deletes = instruction_file_plan(
        instructions,
        project.get("guidelines"),
        bool(project.get("docs_tree_enabled")),
    )
    digest = publish_hash(files, deletes)
    if digest == (project.get("instructions_synced_hash") or ""):
        return {"unchanged": True, "hash": digest}

    try:
        token = await github_tokens.token_for_org(
            settings, str(project["org_id"]), repo_full
        )
        result = await commit_files(
            token,
            repo_full,
            branch,
            f"docs: build mill instructions ({trigger})",
            files,
            deletes,
        )
    except Exception as e:  # noqa: BLE001 — availability beats freshness
        return {"error": str(e)}

    if result.get("commit_sha"):
        db.record_instructions_sync(
            settings, project_id, digest, result["commit_sha"]
        )
    return {"files": sorted(files), **result}
