"""us-101.2/101.3/101.4: the release notes vocabulary and the case rules.

Pure and Essential. The rule these enforce is the one the phase exists for:
a check is a step AND an expectation, and a release accounts for what it
shipped.
"""

import pytest

from app import release_notes as rn


def _case(**over):
    base = {
        "title": "Tick two branches and reload",
        "steps": "Tick two branches, then reload the page.",
        "expected_result": "Both are still ticked and the count reads 2.",
    }
    return {**base, **over}


def _item(display_id, issue_id="11111111-1111-1111-1111-111111111111"):
    return {"display_id": display_id, "issue_id": issue_id, "title": "A story"}


# ------------------------------------------------------------ sections


class TestSections:
    def test_the_known_sections_keep_their_order(self):
        keys = list(rn.SECTION_KEYS)
        assert keys.index("pre-flight") < keys.index("happy-path")
        assert keys.index("happy-path") < keys.index("refusals")
        assert keys.index("refusals") < keys.index("regression")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Happy Path", "happy-path"),
            ("preflight", "pre-flight"),
            ("REFUSALS", "refusals"),
            ("the happy path", "happy-path"),
            ("regressions", "regression"),
            ("", "other"),
            (None, "other"),
        ],
    )
    def test_it_recognises_what_an_agent_actually_writes(self, raw, expected):
        assert rn.normalize_section(raw) == expected

    def test_an_invented_section_survives_as_itself(self):
        """A release that genuinely needs 'Data migration' gets it. Refusing
        would cost a whole agent run over a heading."""
        assert rn.normalize_section("Data migration") == "data-migration"
        assert rn.section_label("data-migration") == "Data migration"

    def test_an_invented_section_sorts_after_every_known_one(self):
        assert rn.section_rank("data-migration") > rn.section_rank("regression")
        assert rn.section_rank("pre-flight") == 0


# --------------------------------------------------------------- checks


class TestCheckCases:
    def test_a_complete_checklist_passes(self):
        cases, errors = rn.check_cases(
            [_case(story="US-98.2", section="happy path", critical=True)],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert errors == []
        assert cases[0]["section"] == "happy-path"
        assert cases[0]["critical"] is True
        assert cases[0]["issue_id"] == "11111111-1111-1111-1111-111111111111"

    def test_a_case_with_no_steps_is_refused(self):
        _, errors = rn.check_cases(
            [_case(steps="", story="US-98.2")],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert any("no `steps`" in e for e in errors)

    def test_a_case_with_no_expectation_is_refused(self):
        _, errors = rn.check_cases(
            [_case(expected_result="", story="US-98.2")],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert any("no `expected_result`" in e for e in errors)

    def test_a_blank_title_is_named_rather_than_dropped(self):
        """It used to be silently skipped, so a release reported more checks
        than it attached."""
        _, errors = rn.check_cases(
            [_case(title="", story="US-98.2")],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert any("no title" in e for e in errors)

    def test_an_expectation_that_repeats_the_title_is_refused(self):
        _, errors = rn.check_cases(
            [
                _case(
                    title="The merge commit is a merge commit",
                    expected_result="the merge commit is a merge commit!",
                    story="US-98.2",
                )
            ],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert any("repeats its title" in e for e in errors)

    def test_zero_cases_is_refused(self):
        _, errors = rn.check_cases(
            [], included=[_item("US-98.2")], inherited_display_ids=set()
        )
        assert any("no test cases" in e for e in errors)

    def test_every_failure_arrives_at_once(self):
        """One rule per re-run is one agent session per rule."""
        _, errors = rn.check_cases(
            [_case(steps="", expected_result="", story="US-98.2")],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert len([e for e in errors if "case 1" in e]) == 2

    def test_a_story_tag_outside_the_release_is_refused(self):
        """Otherwise the page renders no provenance at all, which looks like
        a missing feature rather than a wrong tag."""
        _, errors = rn.check_cases(
            [_case(story="US-77.7")],
            included=[_item("US-98.2")],
            inherited_display_ids=set(),
        )
        assert any("US-77.7" in e and "not in this release" in e for e in errors)

    def test_an_untagged_case_is_allowed_but_covers_nothing(self):
        _, errors = rn.check_cases(
            [_case()], included=[_item("US-98.2")], inherited_display_ids=set()
        )
        assert any("Nothing accounts for US-98.2" in e for e in errors)

    def test_an_item_covered_by_inheritance_needs_no_new_case(self):
        _, errors = rn.check_cases(
            [_case(story="US-98.1")],
            included=[_item("US-98.1"), _item("US-98.2", "22222222-2222-2222-2222-222222222222")],
            inherited_display_ids={"US-98.2"},
        )
        assert errors == []

    def test_an_item_can_be_declared_uncovered_on_purpose(self):
        _, errors = rn.check_cases(
            [_case(story="US-98.1")],
            included=[_item("US-98.1"), _item("US-98.2", "22222222-2222-2222-2222-222222222222")],
            inherited_display_ids=set(),
            uncovered=["us-98.2"],
        )
        assert errors == []

    def test_uncovered_cannot_name_something_outside_the_release(self):
        _, errors = rn.check_cases(
            [_case(story="US-98.1")],
            included=[_item("US-98.1")],
            inherited_display_ids=set(),
            uncovered=["US-55.5"],
        )
        assert any("US-55.5" in e for e in errors)

    def test_sort_defaults_to_the_order_written(self):
        cases, _ = rn.check_cases(
            [_case(story="US-98.1"), _case(story="US-98.1")],
            included=[_item("US-98.1")],
            inherited_display_ids=set(),
        )
        assert [c["sort"] for c in cases] == [1, 2]


# ---------------------------------------------------------- declaration


class TestDeclaration:
    def test_a_well_formed_declaration_survives(self):
        doc, findings = rn.as_declaration(
            {
                "standfirst": "Work top to bottom.",
                "sections": {"happy path": "One merge, end to end."},
                "blocks": [{"block": "prose", "markdown": "Some detail."}],
            }
        )
        assert doc["standfirst"] == "Work top to bottom."
        assert doc["sections"]["happy-path"] == "One merge, end to end."
        assert findings == []

    def test_nothing_is_an_empty_document_not_an_error(self):
        assert rn.as_declaration(None) == ({}, [])
        assert rn.as_declaration({}) == ({}, [])

    def test_a_document_sent_as_markdown_is_kept_as_prose(self):
        """US-42.1: coerce the shape, never reject the payload over it."""
        doc, findings = rn.as_declaration("# Notes\n\nSomething happened.")
        assert doc["blocks"][0]["markdown"].startswith("# Notes")
        assert findings

    def test_sections_sent_as_a_list_are_keyed(self):
        doc, findings = rn.as_declaration(
            {"sections": [{"key": "refusals", "note": "Each guard."}]}
        )
        assert doc["sections"] == {"refusals": "Each guard."}
        assert findings

    def test_an_unknown_block_is_kept_as_prose_with_advice(self):
        doc, findings = rn.as_declaration(
            {"blocks": [{"block": "table", "markdown": "| a |"}]}
        )
        assert doc["blocks"][0]["block"] == "prose"
        assert any("not in the vocabulary" in f for f in findings)

    def test_a_bad_callout_tone_is_shown_as_info_not_refused(self):
        doc, findings = rn.as_declaration(
            {"blocks": [{"block": "callout", "tone": "danger", "body": "Careful."}]}
        )
        assert doc["blocks"][0]["tone"] == "info"
        assert findings

    def test_a_declaration_that_is_not_an_object_is_ignored_not_fatal(self):
        doc, findings = rn.as_declaration(42)
        assert doc == {}
        assert findings


class TestVocabularyBrief:
    def test_the_brief_names_every_section_and_block_it_renders(self):
        brief = rn.vocabulary_brief()
        for key in rn.SECTION_KEYS:
            assert f"`{key}`" in brief
        for kind in rn.BLOCK_KINDS:
            assert f"`{kind}`" in brief

    def test_the_brief_forbids_the_facts_that_do_not_exist_yet(self):
        """The instruction quotes this; it is where the fabrication is
        forbidden at its source."""
        brief = rn.vocabulary_brief()
        assert "deploy result" in brief
        assert "do not exist" in brief.lower() or "None of them exist" in brief


class TestRenderMarkdown:
    def test_the_export_carries_the_checks_in_section_order(self):
        md = rn.render_markdown(
            "2026.08.15.1",
            "2026.08.15.1 — six stories",
            "Two migrations.",
            {"standfirst": "Work top to bottom.", "sections": {}, "blocks": []},
            [
                {"title": "Regress", "steps": "do", "expected_result": "see", "section": "regression", "sort": 1},
                {"title": "Preflight", "steps": "do", "expected_result": "see", "section": "pre-flight", "sort": 1},
            ],
        )
        assert md.index("Pre-flight") < md.index("Regression")
        assert "Expect: see" in md

    def test_a_critical_check_is_marked_in_the_export(self):
        md = rn.render_markdown(
            "1.0",
            "1.0",
            "",
            {},
            [{"title": "Check the diff", "steps": "d", "expected_result": "e", "section": "happy-path", "critical": True}],
        )
        assert "(critical)" in md
