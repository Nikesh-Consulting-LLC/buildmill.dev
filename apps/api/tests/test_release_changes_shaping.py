"""us-101.1: the shape of what a release agent is handed.

`test_factory_mcp.py` covers `get_release_changes` end to end, but it is
marked `needs_db` and therefore never runs in Essential — which is how a
commit message shipped as a one-element list and stayed that way. These are
pure and run every time.
"""

from app.factory_mcp import (
    _migration_paths,
    _release_range_note,
    _subject_line,
)


def _f(*paths):
    return [{"filename": p} for p in paths]


class TestSubjectLine:
    """The bug this file exists for: `[:1]` is a list slice, not `[0]`."""

    def test_a_subject_is_a_string_not_a_list(self):
        got = _subject_line("us-98.2: a merge names its branches\n\nBody here.")
        assert got == "us-98.2: a merge names its branches"
        assert isinstance(got, str)

    def test_an_empty_message_is_an_empty_string_not_an_empty_list(self):
        assert _subject_line("") == ""
        assert _subject_line(None) == ""
        assert _subject_line("   \n  \n") == ""

    def test_a_single_line_message_survives(self):
        assert _subject_line("fix the thing") == "fix the thing"


class TestMigrationPaths:
    def test_it_finds_migrations_in_this_repo_s_layout(self):
        files = _f(
            "infra/supabase/migrations/263_agent_instructions.sql",
            "apps/api/app/db.py",
            "infra/supabase/migrations/264_the_version.sql",
        )
        assert _migration_paths(files) == [
            "infra/supabase/migrations/263_agent_instructions.sql",
            "infra/supabase/migrations/264_the_version.sql",
        ]

    def test_it_is_not_hard_coded_to_this_repo(self):
        """Every project has its own layout; a rule that only knows Build
        Mill's answers 'no migrations' for all of them."""
        assert _migration_paths(_f("db/migrate/0012_add_users.sql")) == [
            "db/migrate/0012_add_users.sql"
        ]
        assert _migration_paths(_f("migrations/001_init.sql")) == [
            "migrations/001_init.sql"
        ]

    def test_a_sql_file_outside_a_migrations_folder_is_not_a_migration(self):
        assert _migration_paths(_f("apps/api/scripts/backfill.sql")) == []

    def test_a_non_sql_file_in_a_migrations_folder_is_not_a_migration(self):
        assert _migration_paths(_f("infra/supabase/migrations/README.md")) == []

    def test_nothing_changed_is_no_migrations(self):
        assert _migration_paths([]) == []


class TestRangeNote:
    """The note is the only place a partial range admits to being partial."""

    def test_a_complete_range_says_nothing(self):
        assert (
            _release_range_note(
                first_release=False,
                range_truncated=False,
                commits_truncated=False,
                next_cursor=None,
                prefix="",
            )
            == ""
        )

    def test_a_first_release_distinguishes_unknown_from_none(self):
        note = _release_range_note(
            first_release=True,
            range_truncated=False,
            commits_truncated=False,
            next_cursor=None,
            prefix="",
        )
        assert "NOT because none ran" in note

    def test_a_capped_commit_list_says_the_count_is_the_truth(self):
        note = _release_range_note(
            first_release=False,
            range_truncated=False,
            commits_truncated=True,
            next_cursor=None,
            prefix="",
        )
        assert "true size of the range" in note

    def test_a_prefix_over_a_capped_range_refuses_to_read_as_absence(self):
        """The whole point: an empty answer to a migrations prefix must not
        be reportable as 'this release ran no migrations'."""
        note = _release_range_note(
            first_release=False,
            range_truncated=True,
            commits_truncated=False,
            next_cursor=None,
            prefix="infra/supabase/migrations/",
        )
        assert "infra/supabase/migrations/" in note
        assert "Do not report absence from it" in note

    def test_a_prefix_over_a_complete_range_carries_no_such_warning(self):
        note = _release_range_note(
            first_release=False,
            range_truncated=False,
            commits_truncated=False,
            next_cursor=None,
            prefix="infra/supabase/migrations/",
        )
        assert note == ""

    def test_paging_still_says_follow_the_cursor(self):
        note = _release_range_note(
            first_release=False,
            range_truncated=False,
            commits_truncated=False,
            next_cursor=300,
            prefix="",
        )
        assert "Follow `cursor`" in note
