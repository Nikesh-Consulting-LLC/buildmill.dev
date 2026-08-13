"""US-81.5: the test-plan contract's automation fields.

A plan's JSON case may carry execution ('manual'|'automated') and layer
('api'|'browser'). The parser stores the valid vocabulary and drops anything
else — the columns have check constraints, and a typo'd value must not turn
a plan approval into a 500.
"""

from app.artifacts_sim import parse_test_plan_cases


def _one(case: dict) -> dict:
    return parse_test_plan_cases('{"cases": [' + __import__("json").dumps(case) + "]}")[0]


def test_valid_execution_and_layer_pass_through():
    case = _one(
        {
            "title": "login works",
            "steps": "1. open\n2. sign in",
            "expected_result": "dashboard",
            "execution": "automated",
            "layer": "browser",
        }
    )
    assert case["execution"] == "automated"
    assert case["layer"] == "browser"


def test_case_matches_uppercase_and_whitespace():
    case = _one({"title": "t", "execution": " Automated ", "layer": "API"})
    assert case["execution"] == "automated"
    assert case["layer"] == "api"


def test_invented_vocabulary_is_dropped_not_stored():
    case = _one({"title": "t", "execution": "robot", "layer": "mobile"})
    assert "execution" not in case
    assert "layer" not in case


def test_omitted_keys_stay_omitted():
    # Materialization defaults execution to 'manual'; the parser itself
    # invents nothing (the shape contract of US-42.1).
    case = _one({"title": "t", "steps": "s"})
    assert "execution" not in case
    assert "layer" not in case


def test_existing_plans_parse_unchanged():
    cases = parse_test_plan_cases(
        '```json\n[{"title": "old", "steps": ["a", "b"], '
        '"expected_result": "works", "test_types": ["regression"]}]\n```'
    )
    assert cases[0]["steps"] == "a\nb"
    assert cases[0]["test_types"] == ["regression"]
    assert "execution" not in cases[0]
