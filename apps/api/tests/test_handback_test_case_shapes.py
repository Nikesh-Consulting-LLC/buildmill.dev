"""US-42.1: a hand-back is not refused over a field's shape.

The 2026-07-28 fifteen-story plan batch logged `hand-back refused (422):
test_cases[N].steps — Input should be a valid string` on all fifteen runs: the
agents wrote `steps` as a list of steps, `AgentTestCase.steps` is a `str`, and
a request-body validation error throws away the whole hand-back — plan, test
plan, notes and token counts included. Two runs lost their lease during the
retry and one was double-claimed.

Pins the coercion at the boundary: a list of steps and a newline-joined string
mean the same thing and both are accepted; a bare string where a list is
expected is wrapped; the shape that already worked is untouched; and `title`
is still required.
"""

import uuid

import pytest

from app.routers.worker import AgentTestCase, Submit

RUN_ID = str(uuid.uuid4())
ISSUE_ID = str(uuid.uuid4())
ORG_ID = str(uuid.uuid4())

WORKER = {
    "id": str(uuid.uuid4()),
    "org_id": ORG_ID,
    "name": "Runner (Claude Code)",
    "type": "autonomous",
    "status": "active",
}
HDR = {"X-Worker-Token": "sfw_testtoken"}


@pytest.fixture
def worker_auth(monkeypatch):
    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_by_token",
        lambda settings, token: dict(WORKER) if token == "sfw_testtoken" else None,
    )
    return WORKER


@pytest.fixture
def simulated_code_run(monkeypatch):
    """A claimed `code` run taking the simulated:// success path — the
    shortest route on which `test_cases` reach `complete_run`."""
    state = {"test_cases": None}

    monkeypatch.setattr(
        "app.routers.worker.db.get_worker_run",
        lambda s, rid, org: {
            "id": RUN_ID,
            "org_id": ORG_ID,
            "issue_id": ISSUE_ID,
            "kind": "code",
            "status": "running",
            "worker_id": WORKER["id"],
        },
    )

    def fake_complete(
        settings, run_id, outcome, stdout, diff, branch, pr, error, **kw
    ):
        state["test_cases"] = kw.get("test_cases")
        return True

    monkeypatch.setattr("app.routers.worker.db.complete_run", fake_complete)
    monkeypatch.setattr(
        "app.routers.worker._store_handback_notes",
        lambda *a, **kw: None,
    )
    return state


def _submit(client, cases):
    return client.post(
        f"/api/v1/worker/runs/{RUN_ID}/submit",
        headers=HDR,
        json={"pr_url": "simulated://pr/1", "test_cases": cases},
    )


# ---------------------------------------------------------------- the model


def test_steps_as_list_is_joined_in_order():
    tc = AgentTestCase(
        title="Upload rejects a non-audio file",
        steps=["Sign in", "POST /api/recordings with a .txt", "Read the response"],
    )
    assert tc.steps == (
        "Sign in\nPOST /api/recordings with a .txt\nRead the response"
    )


def test_expected_result_as_list_is_joined_in_order():
    tc = AgentTestCase(
        title="t", expected_result=["422 is returned", "no recording row exists"]
    )
    assert tc.expected_result == "422 is returned\nno recording row exists"


def test_a_string_is_passed_through_byte_identical():
    original = "  1. Sign in\n  2. Upload\n"
    tc = AgentTestCase(title="t", steps=original)
    assert tc.steps == original


def test_list_fields_accept_a_bare_string():
    tc = AgentTestCase(title="t", test_types="integration", environments="uat")
    assert tc.test_types == ["integration"]
    assert tc.environments == ["uat"]


def test_nested_list_is_flattened_and_blanks_dropped():
    tc = AgentTestCase(title="t", steps=["a", ["b", ""], None, "c"])
    assert tc.steps == "a\nb\nc"


def test_dict_in_a_list_lands_as_compact_json_not_a_repr():
    tc = AgentTestCase(title="t", steps=[{"action": "click", "target": "Save"}])
    assert tc.steps == '{"action": "click", "target": "Save"}'


def test_non_string_scalars_are_stringified():
    tc = AgentTestCase(title="t", steps=1, expected_result=True)
    assert tc.steps == "1"
    assert tc.expected_result == "True"


def test_none_becomes_the_field_default():
    tc = AgentTestCase(title="t", steps=None, test_types=None)
    assert tc.steps == ""
    assert tc.test_types == []


def test_title_is_still_required():
    with pytest.raises(ValueError) as e:
        AgentTestCase(steps=["a"])
    assert "title" in str(e.value)


def test_nul_stripping_still_applies_through_the_coercion():
    """US-31.1 composes with this: the joined value must be NUL-free."""
    s = Submit(test_cases=[{"title": "t\x00", "steps": ["a\x00b", "c"]}])
    assert s.test_cases[0].steps == "ab\nc"
    assert s.test_cases[0].title == "t"


# ------------------------------------------------------------- the endpoint


def test_the_batch_shape_that_was_refused_now_lands_200(
    client, worker_auth, simulated_code_run
):
    """The exact shape of the 2026-07-28 refusal: every case's `steps` a list."""
    r = _submit(
        client,
        [
            {"title": "case one", "steps": ["step a", "step b"]},
            {"title": "case two", "steps": ["step c"], "test_types": "unit"},
        ],
    )
    assert r.status_code == 200, r.text
    stored = simulated_code_run["test_cases"]
    assert [c["steps"] for c in stored] == ["step a\nstep b", "step c"]
    assert stored[1]["test_types"] == ["unit"]


def test_a_missing_title_is_still_a_422(client, worker_auth, simulated_code_run):
    r = _submit(client, [{"steps": ["step a"]}])
    assert r.status_code == 422
    assert "title" in r.text
