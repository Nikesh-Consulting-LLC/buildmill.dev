"""US-40.2: a branch hand-back attributes the stories it landed.

Before this, `run_item_commits` was written only by the MCP `submit_changeset`
tool, so an agent that pushed a branch produced a PR with no coverage at all —
and `_fan_out_issue_status`, which deliberately asks the record rather than the
agent, returned every story in the batch to the pool. The table had never held
a single row in production.
"""

import asyncio
import uuid

from app import changesets, github
from app.routers import worker

RUN_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())
STORY_A = str(uuid.uuid4())
STORY_B = str(uuid.uuid4())

MEMBERS = [
    {"issue_id": STORY_A, "display_id": "US-1.1", "position": 1},
    {"issue_id": STORY_B, "display_id": "US-1.2", "position": 2},
]


# --------------------------------------------------- trailer parsing


def test_single_trailer():
    assert changesets.story_trailers(
        "Add the user model\n\nFactory-Story: US-1.1"
    ) == ["US-1.1"]


def test_several_trailers_and_comma_separated_are_the_same_thing():
    several = changesets.story_trailers(
        "Wire login\n\nFactory-Story: US-1.1\nFactory-Story: US-1.2"
    )
    comma = changesets.story_trailers("Wire login\n\nFactory-Story: US-1.1, US-1.2")
    assert several == comma == ["US-1.1", "US-1.2"]


def test_trailer_is_case_insensitive_and_tolerates_spacing():
    assert changesets.story_trailers("x\n\n  factory-story :   US-1.1  ") == [
        "US-1.1"
    ]


def test_duplicates_collapse():
    assert changesets.story_trailers(
        "x\n\nFactory-Story: US-1.1\nFactory-Story: US-1.1"
    ) == ["US-1.1"]


def test_no_trailer_is_empty():
    assert changesets.story_trailers("Just a commit message") == []
    assert changesets.story_trailers("") == []


def test_factory_run_trailer_is_not_a_story_trailer():
    """`apply_changeset` appends `Factory-Run:` to every commit it makes."""
    assert changesets.story_trailers("x\n\nFactory-Run: abc-123") == []


# --------------------------------------------------- attribution


def _run_attribute(monkeypatch, commits, recorder=None):
    async def fake_compare(token, owner, repo, base, head):
        return {"commits": commits}

    written = []

    def fake_record(settings, run_id, org_id, issue_ids, sha, message, **kw):
        written.append(
            {"issue_ids": list(issue_ids), "sha": sha, "message": message}
        )
        return len(issue_ids)

    monkeypatch.setattr(worker.github, "compare_commits", fake_compare)
    monkeypatch.setattr(
        worker.db, "record_changeset_coverage", recorder or fake_record
    )
    count = asyncio.run(
        worker._attribute_branch_coverage(
            None, "tok", "acme", "demo", "main", "factory/x",
            RUN_ID, ORG_ID, MEMBERS,
        )
    )
    return count, written


def test_attribution_records_one_row_per_story(monkeypatch):
    count, written = _run_attribute(
        monkeypatch,
        [
            {
                "sha": "aaa111",
                "commit": {"message": "User model\n\nFactory-Story: US-1.1"},
            },
            {
                "sha": "bbb222",
                "commit": {"message": "Login route\n\nFactory-Story: US-1.2"},
            },
        ],
    )
    assert count == 2
    assert written[0]["issue_ids"] == [STORY_A]
    assert written[0]["sha"] == "aaa111"
    # The subject line is kept, not the whole message with its trailers.
    assert written[0]["message"] == "User model"
    assert written[1]["issue_ids"] == [STORY_B]


def test_one_commit_can_land_two_stories(monkeypatch):
    count, written = _run_attribute(
        monkeypatch,
        [
            {
                "sha": "aaa111",
                "commit": {"message": "Both\n\nFactory-Story: US-1.1, US-1.2"},
            }
        ],
    )
    assert count == 2
    assert written[0]["issue_ids"] == [STORY_A, STORY_B]


def test_commits_without_trailers_are_skipped(monkeypatch):
    count, written = _run_attribute(
        monkeypatch,
        [
            {"sha": "aaa111", "commit": {"message": "wip"}},
            {
                "sha": "bbb222",
                "commit": {"message": "real\n\nFactory-Story: US-1.1"},
            },
        ],
    )
    assert count == 1
    assert [w["sha"] for w in written] == ["bbb222"]


def test_a_story_outside_this_run_is_dropped_not_guessed(monkeypatch):
    """Mis-attribution is the one thing the fan-out exists to prevent."""
    count, written = _run_attribute(
        monkeypatch,
        [
            {
                "sha": "aaa111",
                "commit": {"message": "x\n\nFactory-Story: US-9.9"},
            }
        ],
    )
    assert count == 0
    assert written == []


def test_no_attribution_anywhere_returns_zero(monkeypatch):
    """Zero is what makes the hand-back refuse — the 2026-07-28 case."""
    count, _ = _run_attribute(
        monkeypatch,
        [
            {"sha": "aaa111", "commit": {"message": "first"}},
            {"sha": "bbb222", "commit": {"message": "second"}},
        ],
    )
    assert count == 0


def test_github_failure_is_zero_not_an_exception(monkeypatch):
    """A hand-back must not die because the commit listing did."""

    async def boom(token, owner, repo, base, head):
        raise github.GitHubError("rate limited")

    monkeypatch.setattr(worker.github, "compare_commits", boom)
    count = asyncio.run(
        worker._attribute_branch_coverage(
            None, "tok", "acme", "demo", "main", "factory/x",
            RUN_ID, ORG_ID, MEMBERS,
        )
    )
    assert count == 0


def test_uuids_are_accepted_as_well_as_display_ids(monkeypatch):
    count, written = _run_attribute(
        monkeypatch,
        [
            {
                "sha": "aaa111",
                "commit": {"message": f"x\n\nFactory-Story: {STORY_B}"},
            }
        ],
    )
    assert count == 1
    assert written[0]["issue_ids"] == [STORY_B]
