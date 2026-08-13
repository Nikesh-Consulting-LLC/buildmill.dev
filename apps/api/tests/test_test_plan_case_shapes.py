"""A parsed test case is stored in the shape a human reads.

The sibling of US-42.1's boundary coercion (tests/test_handback_test_case_shapes.py):
that one covers cases POSTED to the worker endpoint, this one covers cases
PARSED out of a test plan artifact. Both end up in the same `test_cases` rows,
so both need the same treatment — and for a while only one had it. An agent
writing `steps` as a JSON list (which they do about as often as a string) had
the list stored verbatim, so the manager's UAT checklist read
`["Sign in with a valid account.", ...]` instead of the steps. Observed on
US-2.5 in the Demo project, 2026-08-10.
"""

from app import artifacts_sim


def _one(plan):
    cases = artifacts_sim.parse_test_plan_cases(plan)
    assert len(cases) == 1
    return cases[0]


LIST_STEPS = """Prose about coverage.

```json
{"cases": [{"title": "Buttons recolored",
            "steps": ["Sign in with a valid account.", "Open Profile."],
            "expected_result": ["Buttons are green.", "Nothing else moved."],
            "test_types": "acceptance",
            "environments": ["dev"]}]}
```
"""


def test_list_steps_become_readable_lines():
    case = _one(LIST_STEPS)
    assert case["steps"] == "Sign in with a valid account.\nOpen Profile."
    assert case["expected_result"] == "Buttons are green.\nNothing else moved."
    # A bare tag where a list is expected is wrapped, not dropped.
    assert case["test_types"] == ["acceptance"]
    assert case["environments"] == ["dev"]


def test_string_steps_are_untouched():
    """The shape that already worked keeps working, byte for byte."""
    plan = (
        '```json\n{"cases": [{"title": "T", "steps": "1. Sign in\\n2. Look",'
        ' "expected_result": "ok"}]}\n```\n'
    )
    case = _one(plan)
    assert case["steps"] == "1. Sign in\n2. Look"
    assert case["expected_result"] == "ok"


def test_absent_keys_stay_absent():
    """Coercion normalizes what is there; it does not invent fields, so the
    insert's own defaults still decide what an omitted key becomes."""
    case = _one('```json\n{"cases": [{"title": "T"}]}\n```\n')
    assert case == {"title": "T"}


def test_bare_list_of_cases_is_normalized_too():
    """A test plan may hold a bare JSON array rather than {"cases": [...]}."""
    plan = '```json\n[{"title": "T", "steps": ["a", "b"]}]\n```\n'
    assert _one(plan)["steps"] == "a\nb"


def test_whole_document_json_is_normalized_too():
    """...or no fence at all, when the artifact is JSON end to end."""
    plan = '{"cases": [{"title": "T", "steps": ["a", "b"]}]}'
    assert _one(plan)["steps"] == "a\nb"


def test_nothing_is_dropped_from_an_odd_shape():
    """Unreadable beats lost: a dict where text was expected lands as compact
    JSON rather than a Python repr, and a number stringifies."""
    plan = '```json\n[{"title": "T", "steps": {"do": "x"}, "expected_result": 7}]\n```\n'
    case = _one(plan)
    assert case["steps"] == '{"do": "x"}'
    assert case["expected_result"] == "7"
