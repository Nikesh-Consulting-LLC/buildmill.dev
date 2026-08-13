"""US-13.4: approved work lands in the repo, owned by the app — layout,
index generation, opt-in gating, and the write-failure-never-fails-the-
approval contract.

Phase 22 extends this: stable id-keyed paths (us-22.2), front matter and
index.json (us-22.3), dispatched-but-unplanned stories (us-22.4), the
Outcome section (us-22.5), one writer for both instruction files (us-22.6),
the pre-dispatch hash check (us-22.7), and what the block teaches (us-22.8).
"""

import asyncio
import json
import uuid

import pytest

from app import repo_docs

FEATURE_ID = str(uuid.uuid4())
STORY_ID = str(uuid.uuid4())
LONER_ID = str(uuid.uuid4())

FEATURE = {
    "id": FEATURE_ID,
    "title": "Login flow",
    "type": "feature",
    "parent_id": None,
    "item_no": 4,
    "sub_no": None,
    "epic_number": 1,
    "body": "",
    "acceptance_criteria": [],
    "dispatched": False,
}
STORY = {
    "id": STORY_ID,
    "title": "Session cookie",
    "type": "story",
    "parent_id": FEATURE_ID,
    "item_no": 4,
    "sub_no": 1,
    "epic_number": 1,
    "body": "As a user I stay signed in.",
    "acceptance_criteria": ["Cookie is HttpOnly", "Session survives reload"],
    "dispatched": True,
}
LONER = {
    "id": LONER_ID,
    "title": "Fix header",
    "type": "story",
    "parent_id": None,
    "item_no": 5,
    "sub_no": None,
    "epic_number": 1,
    "body": "Standalone story.",
    "acceptance_criteria": [],
    "dispatched": True,
}

FEATURE_DIR = "docs/factory/feat-1.4"
STORY_PATH = f"{FEATURE_DIR}/us-1.4.1.md"
PRD_PATH = f"{FEATURE_DIR}/prd.md"
ARTIFACTS = [
    {"issue_id": FEATURE_ID, "kind": "prd", "content": "## Problem\n\nP", "version": 1},
    {"issue_id": STORY_ID, "kind": "plan", "content": "Step 1", "version": 1},
    {"issue_id": STORY_ID, "kind": "test_plan", "content": "Case 1", "version": 1},
    {"issue_id": LONER_ID, "kind": "plan", "content": "Loner plan", "version": 1},
]

STAMP = "2026-07-25T12:00:00+00:00"


def front_matter(doc: str) -> dict[str, object]:
    """Parse the fixed subset of YAML the generator emits, so the tests read
    the file the way an agent's grep would rather than trusting the writer."""
    assert doc.startswith("---\n"), "front matter must be the first bytes"
    body = doc.split("---\n", 2)[1]
    out: dict[str, object] = {}
    for line in body.strip().splitlines():
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw == "null":
            out[key] = None
        elif raw in ("true", "false"):
            out[key] = raw == "true"
        elif raw.startswith('"'):
            out[key] = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        else:
            out[key] = int(raw)
    return out


# --- us-22.2: paths are ids ------------------------------------------------


def test_build_tree_layout_and_index():
    files = repo_docs.build_tree([FEATURE, STORY, LONER], ARTIFACTS, [], STAMP)
    assert "docs/factory/README.md" in files
    assert "docs/factory/INDEX.md" in files
    assert PRD_PATH in files
    assert STORY_PATH in files
    # Standalone story: its own folder, still keyed by id.
    assert "docs/factory/us-1.5/us-1.5.md" in files

    doc = files[STORY_PATH]
    assert "As a user I stay signed in." in doc
    assert "Cookie is HttpOnly" in doc
    assert "Step 1" in doc and "Case 1" in doc

    index = files["docs/factory/INDEX.md"]
    assert "FEAT-1.4 — Login flow" in index
    assert "US-1.4.1 — Session cookie" in index
    assert "feat-1.4/us-1.4.1.md" in index
    assert "Build Mill owns" in files["docs/factory/README.md"]


def test_retitling_moves_nothing():
    """The whole point of us-22.2: a title edit changes the H1 and the index
    entry and produces no path change and no second file."""
    before = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [], STAMP)
    renamed_story = {**STORY, "title": "Session cookie, but better"}
    renamed_feature = {**FEATURE, "title": "Sign-in flow"}
    after = repo_docs.build_tree(
        [renamed_feature, renamed_story], ARTIFACTS, [], STAMP
    )

    assert set(before) == set(after)
    assert "Session cookie, but better" in after[STORY_PATH]
    assert "Sign-in flow" in after[PRD_PATH]
    assert "Session cookie, but better" in after["docs/factory/INDEX.md"]
    # No slug anywhere in a path.
    assert not [p for p in after if "session-cookie" in p or "login-flow" in p]


# --- us-22.3: front matter and index.json ----------------------------------


def test_every_generated_file_carries_complete_front_matter():
    files = repo_docs.build_tree([FEATURE, STORY, LONER], ARTIFACTS, [], STAMP)
    expected = {
        "id",
        "issue_id",
        "type",
        "title",
        "parent",
        "epic",
        "order",
        "has_plan",
        "has_test_plan",
        "merge_commit",
        "generated_at",
    }
    for path, doc in files.items():
        if not path.endswith(".md") or path.endswith(("INDEX.md", "README.md")):
            continue
        fm = front_matter(doc)
        # Every key present on every file — an agent must never have to tell
        # "no parent" apart from "this generator didn't write parents".
        assert set(fm) == expected, path
        assert fm["generated_at"] == STAMP

    story_fm = front_matter(files[STORY_PATH])
    assert story_fm["id"] == "US-1.4.1"
    assert story_fm["issue_id"] == STORY_ID
    assert story_fm["type"] == "story"
    assert story_fm["parent"] == "FEAT-1.4"
    assert story_fm["epic"] == 1
    assert story_fm["has_plan"] is True
    assert story_fm["has_test_plan"] is True
    assert story_fm["merge_commit"] is None

    prd_fm = front_matter(files[PRD_PATH])
    assert prd_fm["type"] == "feature"
    assert prd_fm["has_plan"] is False
    assert prd_fm["parent"] is None


def test_index_json_matches_index_md():
    files = repo_docs.build_tree([FEATURE, STORY, LONER], ARTIFACTS, [], STAMP)
    entries = json.loads(files["docs/factory/index.json"])

    generated = [
        p
        for p in files
        if p.endswith(".md") and not p.endswith(("INDEX.md", "README.md"))
    ]
    assert {e["path"] for e in entries} == set(generated)
    # Same order as INDEX.md, so "the stories before mine" means one thing.
    assert [e["order"] for e in entries] == sorted(e["order"] for e in entries)
    index_md = files["docs/factory/INDEX.md"]
    for entry in entries:
        assert entry["id"] in index_md


def test_titles_with_yaml_metacharacters_stay_parseable():
    nasty = {**STORY, "title": 'Fix: the "quoted" thing — now'}
    files = repo_docs.build_tree([FEATURE, nasty], ARTIFACTS, [], STAMP)
    assert front_matter(files[STORY_PATH])["title"] == 'Fix: the "quoted" thing — now'
    json.loads(files["docs/factory/index.json"])  # still valid JSON


# --- us-22.4: the backlog is visible ---------------------------------------


def test_dispatched_story_without_a_plan_gets_a_file():
    unplanned = {**LONER, "dispatched": True}
    files = repo_docs.build_tree(
        [FEATURE, STORY, unplanned],
        [a for a in ARTIFACTS if a["issue_id"] != LONER_ID],
        [],
        STAMP,
    )
    path = "docs/factory/us-1.5/us-1.5.md"
    assert path in files
    fm = front_matter(files[path])
    assert fm["has_plan"] is False
    assert "Standalone story." in files[path]
    assert "No implementation plan has been approved yet" in files[path]
    # And it is visibly distinguished in the index.
    assert "_(no plan yet)_" in files["docs/factory/INDEX.md"]


def test_draft_story_never_dispatched_has_no_file():
    draft = {**LONER, "dispatched": False}
    files = repo_docs.build_tree(
        [FEATURE, draft],
        [a for a in ARTIFACTS if a["issue_id"] == FEATURE_ID],
        [],
        STAMP,
    )
    assert "docs/factory/us-1.5/us-1.5.md" not in files


def test_only_approved_artifacts_produce_files():
    undispatched = [{**FEATURE}, {**STORY, "dispatched": False}]
    files = repo_docs.build_tree(undispatched, [], [], STAMP)
    assert PRD_PATH not in files
    assert [p for p in files if p.endswith(".md")] == [
        "docs/factory/INDEX.md",
        "docs/factory/README.md",
    ]
    assert "_Nothing approved yet._" in files["docs/factory/INDEX.md"]


def test_latest_approved_version_wins():
    files = repo_docs.build_tree(
        [FEATURE],
        [
            {"issue_id": FEATURE_ID, "kind": "prd", "content": "old", "version": 1},
            {"issue_id": FEATURE_ID, "kind": "prd", "content": "new", "version": 2},
        ],
        [],
        STAMP,
    )
    assert "new" in files[PRD_PATH]
    assert "old" not in files[PRD_PATH]


# --- us-22.5: what actually got built --------------------------------------


OUTCOME = {
    "issue_id": STORY_ID,
    "commit_sha": "a1b2c3d4e5f6",
    "pr_url": "https://github.com/acme/webshop/pull/47",
    "handback_notes": "Chose Argon2id over bcrypt.",
    "change_breakdown": [
        {"path": "apps/api/app/auth.py", "added": 10, "removed": 2},
        {"path": "apps/api/app/db.py", "added": 4, "removed": 0},
    ],
    "merged_at": "2026-07-25T09:30:00+00:00",
}


def test_outcome_section_records_the_merge():
    files = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [OUTCOME], STAMP)
    doc = files[STORY_PATH]
    assert "## Outcome" in doc
    assert "`a1b2c3d`" in doc
    assert "[PR #47](https://github.com/acme/webshop/pull/47)" in doc
    assert "2026-07-25" in doc
    assert "apps/api/app/auth.py" in doc
    assert "Chose Argon2id over bcrypt." in doc
    assert front_matter(doc)["merge_commit"] == "a1b2c3d4e5f6"


def test_two_approved_runs_show_two_entries_oldest_first():
    second = {
        **OUTCOME,
        "commit_sha": "ffffff9",
        "handback_notes": "Follow-up fix.",
        "merged_at": "2026-07-26T09:30:00+00:00",
    }
    files = repo_docs.build_tree(
        [FEATURE, STORY], ARTIFACTS, [OUTCOME, second], STAMP
    )
    doc = files[STORY_PATH]
    assert doc.index("Chose Argon2id") < doc.index("Follow-up fix.")
    # The front matter names the latest.
    assert front_matter(doc)["merge_commit"] == "ffffff9"


def test_direct_mode_records_the_commit_with_no_dead_pr_link():
    direct = {**OUTCOME, "pr_url": None}
    files = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [direct], STAMP)
    doc = files[STORY_PATH]
    assert "`a1b2c3d`" in doc
    assert "PR #" not in doc
    assert "](" not in doc.split("## Outcome")[1]


def test_missing_metrics_and_notes_omit_their_lines():
    bare = {**OUTCOME, "change_breakdown": None, "handback_notes": None}
    files = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [bare], STAMP)
    outcome = files[STORY_PATH].split("## Outcome")[1]
    assert "Files changed:" not in outcome
    assert "Notes from the agent" not in outcome


def test_story_with_no_approved_run_has_no_outcome():
    files = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [], STAMP)
    assert "## Outcome" not in files[STORY_PATH]
    assert front_matter(files[STORY_PATH])["merge_commit"] is None


# --- us-22.6: one writer for AGENTS.md and CLAUDE.md -----------------------


BLOCK = repo_docs.build_instruction_block("## House style\n\nUse tabs.", True)


def test_content_outside_the_markers_survives_byte_for_byte():
    seeded = "# Our own AGENTS.md\n\nBEFORE\n\n" + BLOCK + "\n\nAFTER\n"
    merged = repo_docs.merge_block(seeded, BLOCK)
    assert merged.startswith("# Our own AGENTS.md\n\nBEFORE\n\n")
    assert merged.endswith("\n\nAFTER\n")
    assert merged.count(repo_docs.BLOCK_START) == 1


def test_a_file_with_no_markers_gains_the_block_appended():
    merged = repo_docs.merge_block("# Existing guidelines\n\nKeep me.", BLOCK)
    assert merged.startswith("# Existing guidelines")
    assert "Keep me." in merged
    assert repo_docs.BLOCK_START in merged


def test_the_legacy_docs_tree_block_is_replaced_not_duplicated():
    legacy = (
        "keep\n\n"
        + repo_docs.LEGACY_START
        + "\nOLD TREE SECTION\n"
        + repo_docs.LEGACY_END
        + "\n\ntail\n"
    )
    merged = repo_docs.merge_block(legacy, BLOCK)
    assert "OLD TREE SECTION" not in merged
    assert repo_docs.LEGACY_START not in merged
    assert merged.count(repo_docs.BLOCK_START) == 1
    assert "keep" in merged and "tail" in merged


@pytest.mark.parametrize("current", [None, "", "   ", "@AGENTS.md", "@AGENTS.md\n"])
def test_claude_md_stays_a_pointer_when_it_is_one(current):
    files = repo_docs.instruction_files(BLOCK, None, current)
    assert files["CLAUDE.md"] == repo_docs.CLAUDE_MD_POINTER


def test_claude_md_with_real_content_keeps_it_and_gains_the_block():
    files = repo_docs.instruction_files(
        BLOCK, None, "# My CLAUDE.md\n\nHand-written rules.\n"
    )
    assert "Hand-written rules." in files["CLAUDE.md"]
    assert repo_docs.BLOCK_START in files["CLAUDE.md"]


def test_pointer_only_claude_md_is_stable_across_writes():
    once = repo_docs.instruction_files(BLOCK, None, None)["CLAUDE.md"]
    twice = repo_docs.instruction_files(BLOCK, None, once)["CLAUDE.md"]
    assert once == twice


def test_the_block_holds_guidelines_and_the_tree_pointer():
    assert "Use tabs." in BLOCK
    assert repo_docs.DOCS_TREE_SECTION in BLOCK
    assert BLOCK.startswith(repo_docs.BLOCK_START)
    assert BLOCK.rstrip().endswith(repo_docs.BLOCK_END)


def test_a_project_with_the_tree_disabled_still_gets_its_guidelines():
    block = repo_docs.build_instruction_block("## House style\n\nUse tabs.", False)
    assert "Use tabs." in block
    assert "Factory documentation tree" not in block


# --- us-22.8: what the block teaches ---------------------------------------


def test_the_block_documents_the_query_surface():
    section = repo_docs.DOCS_TREE_SECTION
    for key in (
        "id",
        "issue_id",
        "type",
        "title",
        "parent",
        "epic",
        "order",
        "has_plan",
        "has_test_plan",
        "merge_commit",
        "generated_at",
    ):
        assert f"`{key}`" in section, key
    assert "index.json" in section
    assert "docs/factory/us-4.1/us-4.2.md" in section
    # It says the tree is already on disk, and to read what precedes you.
    assert "already on disk" in section
    assert "No `get_repo_tree`" in section
    assert "precede yours" in section


# --- us-22.7: the hash check -----------------------------------------------


def test_block_hash_is_stable_and_content_addressed():
    a = repo_docs.build_instruction_block("one", True)
    b = repo_docs.build_instruction_block("one", True)
    c = repo_docs.build_instruction_block("two", True)
    assert repo_docs.block_hash(a) == repo_docs.block_hash(b)
    assert repo_docs.block_hash(a) != repo_docs.block_hash(c)


def _project(**over):
    base = {
        "id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "repo_full_name": "acme/webshop",
        "default_branch": "main",
        "docs_tree_enabled": True,
        "guidelines": "## House style\n\nUse tabs.",
        "instructions_synced_hash": None,
    }
    base.update(over)
    return base


def test_matching_hash_makes_no_github_call(monkeypatch):
    block = repo_docs.build_instruction_block("## House style\n\nUse tabs.", True)
    monkeypatch.setattr(
        "app.repo_docs.db.get_project_docs_config",
        lambda s, p: _project(instructions_synced_hash=repo_docs.block_hash(block)),
    )

    async def boom(*a, **k):
        raise AssertionError("GitHub must not be touched when the hash matches")

    monkeypatch.setattr("app.repo_docs.github_tokens.token_for_org", boom)
    out = asyncio.run(repo_docs.sync_instruction_files(None, str(uuid.uuid4())))
    assert out == {"unchanged": True, "hash": repo_docs.block_hash(block)}


def test_differing_hash_writes_and_records(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        "app.repo_docs.db.get_project_docs_config",
        lambda s, p: _project(instructions_synced_hash="stale"),
    )

    async def token(*a, **k):
        return "gh-token"

    async def current(*a, **k):
        return {"AGENTS.md": None, "CLAUDE.md": None}

    async def commit(tok, repo_full, branch, message, files, deletes=None):
        recorded["files"] = sorted(files)
        return {"commit_sha": "deadbee"}

    monkeypatch.setattr("app.repo_docs.github_tokens.token_for_org", token)
    monkeypatch.setattr("app.repo_docs._current_instruction_files", current)
    monkeypatch.setattr("app.repo_docs.commit_files", commit)
    monkeypatch.setattr(
        "app.repo_docs.db.record_instructions_sync",
        lambda s, p, h, sha: recorded.update(hash=h, sha=sha),
    )

    out = asyncio.run(repo_docs.sync_instruction_files(None, str(uuid.uuid4())))
    assert out["commit_sha"] == "deadbee"
    assert recorded["files"] == ["AGENTS.md", "CLAUDE.md"]
    assert recorded["sha"] == "deadbee"
    assert recorded["hash"] != "stale"


def test_write_failure_leaves_the_hash_untouched_so_the_next_dispatch_retries(
    monkeypatch,
):
    monkeypatch.setattr(
        "app.repo_docs.db.get_project_docs_config",
        lambda s, p: _project(instructions_synced_hash="stale"),
    )

    async def boom(*a, **k):
        raise RuntimeError("GitHub is down")

    monkeypatch.setattr("app.repo_docs.github_tokens.token_for_org", boom)

    def must_not_record(*a, **k):
        raise AssertionError("a failed write must not record a hash")

    monkeypatch.setattr(
        "app.repo_docs.db.record_instructions_sync", must_not_record
    )
    out = asyncio.run(repo_docs.sync_instruction_files(None, str(uuid.uuid4())))
    assert "GitHub is down" in out["error"]


def test_no_linked_repo_writes_nothing(monkeypatch):
    monkeypatch.setattr(
        "app.repo_docs.db.get_project_docs_config",
        lambda s, p: _project(repo_full_name=""),
    )
    out = asyncio.run(repo_docs.sync_instruction_files(None, str(uuid.uuid4())))
    assert out == {"skipped": "no linked repository"}


# --- us-22.1: the tree deletes what it stops generating --------------------


def test_delete_set_is_what_survives_from_a_previous_generation():
    files = repo_docs.build_tree([FEATURE, STORY], ARTIFACTS, [], STAMP)
    existing = set(files) | {
        "docs/factory/feat-1.4/us-1.4.1-old-slugged-name.md",
        "docs/factory/hand-added.md",
    }
    deletes = existing - set(files)
    assert deletes == {
        "docs/factory/feat-1.4/us-1.4.1-old-slugged-name.md",
        "docs/factory/hand-added.md",
    }


def test_existing_docs_paths_scopes_to_the_root(monkeypatch):
    async def tree(*a, **k):
        return {
            "tree": [
                {"path": "docs/factory/INDEX.md", "type": "blob"},
                {"path": "docs/factory/feat-1.4/prd.md", "type": "blob"},
                {"path": "docs/factory", "type": "tree"},
                {"path": "AGENTS.md", "type": "blob"},
                {"path": "CLAUDE.md", "type": "blob"},
                {"path": "docs/factory-notes.md", "type": "blob"},
                {"path": "src/app.py", "type": "blob"},
            ]
        }

    monkeypatch.setattr("app.repo_docs.github.get_tree", tree)
    paths = asyncio.run(
        repo_docs.existing_docs_paths("t", "acme", "webshop", "main")
    )
    assert paths == {"docs/factory/INDEX.md", "docs/factory/feat-1.4/prd.md"}
    # AGENTS.md/CLAUDE.md are merged files, never generated — so never
    # candidates for deletion even though the same commit writes them.
    assert "AGENTS.md" not in paths and "CLAUDE.md" not in paths
    # A sibling path that merely starts with the same letters is not inside.
    assert "docs/factory-notes.md" not in paths


def test_a_missing_ref_is_an_empty_set_not_a_failure(monkeypatch):
    async def missing(*a, **k):
        raise repo_docs.github.GitHubError("ref 'main' not found in this repo")

    monkeypatch.setattr("app.repo_docs.github.get_tree", missing)
    assert (
        asyncio.run(repo_docs.existing_docs_paths("t", "acme", "webshop", "main"))
        == set()
    )


def test_a_listing_failure_propagates_rather_than_deleting_nothing(monkeypatch):
    """A sync must never conclude "nothing to delete" because it could not
    look — that would silently leave stale files forever."""

    async def broken(*a, **k):
        raise repo_docs.github.GitHubError("could not list tree (500)")

    monkeypatch.setattr("app.repo_docs.github.get_tree", broken)
    with pytest.raises(repo_docs.github.GitHubError):
        asyncio.run(repo_docs.existing_docs_paths("t", "acme", "webshop", "main"))


def test_commit_files_emits_null_shas_for_deletes(monkeypatch):
    captured = {}

    async def get_ref(*a, **k):
        return {"object": {"sha": "head"}}

    async def get_commit(*a, **k):
        return {"commit": {"tree": {"sha": "base"}}}

    async def create_blob(*a, **k):
        return "blob"

    async def create_tree(tok, owner, repo, base_tree, entries):
        captured["entries"] = entries
        return "newtree"

    async def create_commit(*a, **k):
        return "newcommit"

    async def update_ref(*a, **k):
        return None

    for name, fn in [
        ("get_ref", get_ref),
        ("get_commit", get_commit),
        ("create_blob", create_blob),
        ("create_tree", create_tree),
        ("create_commit", create_commit),
        ("update_ref", update_ref),
    ]:
        monkeypatch.setattr(f"app.repo_docs.github.{name}", fn)

    asyncio.run(
        repo_docs.commit_files(
            "t",
            "acme/webshop",
            "main",
            "msg",
            {"docs/factory/INDEX.md": "x"},
            {"docs/factory/gone.md"},
        )
    )
    deletes = [e for e in captured["entries"] if e["sha"] is None]
    assert [e["path"] for e in deletes] == ["docs/factory/gone.md"]


# --- unchanged contracts ---------------------------------------------------


def test_sync_tree_respects_the_opt_in(monkeypatch):
    monkeypatch.setattr(
        "app.repo_docs.db.get_project_docs_config",
        lambda s, p: _project(docs_tree_enabled=False),
    )

    async def boom(*a, **k):
        raise AssertionError("GitHub must not be touched when disabled")

    monkeypatch.setattr("app.repo_docs.github_tokens.token_for_org", boom)
    out = asyncio.run(repo_docs.sync_tree(None, str(uuid.uuid4())))
    assert "skipped" in out


def test_write_failure_never_fails_the_approval(monkeypatch):
    """The us-13.4 contract: a docs write failure surfaces as an event and
    a response field — the approval endpoint's helper must never raise."""
    from app.routers import workflow

    events = []

    async def boom(settings, project_id, trigger="sync"):
        raise RuntimeError("GitHub is down")

    async def fake_event(settings, token, org_id, issue_id, event_type, payload):
        events.append((event_type, payload))

    monkeypatch.setattr("app.routers.workflow.repo_docs.sync_tree", boom)
    monkeypatch.setattr("app.routers.workflow._event", fake_event)
    out = asyncio.run(
        workflow._sync_docs_tree(
            None,
            "token",
            {"id": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "project_id": str(uuid.uuid4())},
            "plan approved",
        )
    )
    assert out["error"].startswith("GitHub is down")
    assert events and events[0][0] == "docs-write-failed"


def test_dispatch_never_fails_when_the_repo_write_does(monkeypatch):
    """US-22.7: a GitHub outage must not stop work being dispatched."""
    from app.routers import issues as issues_router

    async def boom(*a, **k):
        raise RuntimeError("GitHub is down")

    monkeypatch.setattr("app.routers.issues.repo_docs.sync_instruction_files", boom)
    monkeypatch.setattr("app.routers.issues.repo_docs.sync_tree", boom)
    # Returns normally — the caller goes on to report the run as queued.
    asyncio.run(issues_router._sync_repo_before_dispatch(None, str(uuid.uuid4())))
