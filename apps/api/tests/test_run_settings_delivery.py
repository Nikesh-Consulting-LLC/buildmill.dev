"""US-32.8's server side: an unroutable model fails the run, with the reason.

us-27.8 made the gateway resolve a provider FROM the model id, by matching it
against the org's curated list. A model no provider offers therefore routes
nowhere. The failure this closes is an agent spending a whole lease discovering
that — and the trap to avoid is refusing the CLAIM instead, which would leave the
run queued and loop it forever.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import db
from app.routers import worker as worker_router


WORKER = {"id": "44444444-4444-4444-4444-444444444444", "org_id": "org-1", "name": "pod-1"}
RUN = {
    "id": "55555555-5555-5555-5555-555555555555",
    "kind": "code",
    "input_context": {},
}

DEEP = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Deep",
    "version": 3,
    "model": "claude-opus-5",
    "settings": {"effort": "high"},
}


@pytest.fixture()
def wired(monkeypatch):
    """Everything stamp_run_settings reads, with the writes captured."""
    state = {"stamped": None, "failed": None}
    monkeypatch.setattr(
        db,
        "get_runner_config",
        lambda s, wid: {"run_routes": {"code": {"preset_id": DEEP["id"]}}, "model_routes": {}},
    )
    monkeypatch.setattr(db, "presets_by_id", lambda s, org: {DEEP["id"]: DEEP})
    monkeypatch.setattr(db, "org_default_preset", lambda s, org: None)
    monkeypatch.setattr(
        db,
        "record_run_settings",
        lambda s, rid, record: state.update(stamped=record),
    )

    def fake_fail(s, rid, error, name=None):
        state["failed"] = (rid, error, name)
        return True

    monkeypatch.setattr(db, "fail_run_minimal", fake_fail)
    return state


def _providers(*models):
    return ([{"name": "Anthropic", "models": list(models), "default_model": None}], {})


def test_a_resolvable_model_stamps_and_returns(monkeypatch, wired):
    monkeypatch.setattr(
        db, "get_org_llm_config", lambda s, org: _providers("claude-opus-5")
    )
    record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert record["resolved_settings"]["model"] == "claude-opus-5"
    assert record["preset_name"] == "Deep"
    assert wired["stamped"] is not None
    assert wired["failed"] is None


def test_a_model_no_provider_offers_fails_the_run_naming_it(monkeypatch, wired):
    monkeypatch.setattr(
        db, "get_org_llm_config", lambda s, org: _providers("claude-sonnet-5")
    )
    with pytest.raises(HTTPException) as e:
        worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert e.value.status_code == 409
    assert "claude-opus-5" in e.value.detail
    assert "decides which provider" in e.value.detail
    # The run is FAILED, not left queued: refusing the claim alone would loop it.
    rid, error, name = wired["failed"]
    assert rid == RUN["id"]
    assert "claude-opus-5" in error
    assert name == "pod-1"
    # And the settings were still recorded, so the run says what it tried to be.
    assert wired["stamped"]["resolved_settings"]["model"] == "claude-opus-5"


def test_the_default_model_of_a_provider_counts_as_offered(monkeypatch, wired):
    monkeypatch.setattr(
        db,
        "get_org_llm_config",
        lambda s, org: (
            [{"name": "A", "models": ["other"], "default_model": "claude-opus-5"}],
            {},
        ),
    )
    worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert wired["failed"] is None


def test_a_run_that_resolved_no_model_is_never_refused(monkeypatch, wired):
    """Null model means inherit the org default at gateway time — there is
    nothing to check and nothing to fail."""
    monkeypatch.setattr(
        db,
        "get_runner_config",
        lambda s, wid: {"run_routes": {}, "model_routes": {}},
    )

    def boom(s, org):  # pragma: no cover — must not be reached
        raise AssertionError("providers should not be read with no model")

    monkeypatch.setattr(db, "get_org_llm_config", boom)
    record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert record["resolved_settings"] == {}
    assert wired["failed"] is None


def test_an_agent_model_pin_reaches_only_the_run_kind_it_names(monkeypatch, wired):
    """US-66.1: model_overrides is keyed by kind — stamp_run_settings must
    look up THIS run's kind, not hand the whole dict to resolve()."""
    monkeypatch.setattr(
        db,
        "get_runner_config",
        lambda s, wid: {
            "run_routes": {},
            "model_routes": {},
            "model_overrides": {
                "code": "llama-3.3-70b-versatile",
                "prd": "claude-opus-5",
            },
        },
    )
    monkeypatch.setattr(
        db,
        "get_org_llm_config",
        lambda s, org: _providers("llama-3.3-70b-versatile", "claude-opus-5"),
    )
    record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert record["resolved_settings"]["model"] == "llama-3.3-70b-versatile"
    assert record["settings_sources"]["model"] == "agent"

    run_prd = dict(RUN, kind="prd")
    record_prd = worker_router.stamp_run_settings(object(), run_prd, dict(WORKER))
    assert record_prd["resolved_settings"]["model"] == "claude-opus-5"


def test_the_manager_override_rides_in_the_run_input_context(monkeypatch, wired):
    """us-33.5 will write it; the resolver already reads it, so a dispatch-time
    choice does not need the claim path to change again."""
    monkeypatch.setattr(
        db, "get_org_llm_config", lambda s, org: _providers("claude-opus-5", "grok-4")
    )
    run = dict(RUN)
    run["input_context"] = {"settings_override": {"manager": {"model": "grok-4"}}}
    record = worker_router.stamp_run_settings(object(), run, dict(WORKER))
    assert record["resolved_settings"]["model"] == "grok-4"
    assert record["settings_sources"]["model"] == "manager"
