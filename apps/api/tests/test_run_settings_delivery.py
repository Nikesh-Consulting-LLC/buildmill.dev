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
    # us-116.7: the floor. None here keeps every pre-existing case as it was;
    # the floor's own tests set it.
    monkeypatch.setattr(db, "org_default_provider_model", lambda s, org: state.get("floor"))
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


# ---------------------------------------------------------------------------
# us-116.7: the org's default model counts.
# ---------------------------------------------------------------------------


def test_the_org_default_provider_model_is_the_floor(monkeypatch, wired):
    """No route, no pin, an org default preset with model null — and the org's
    default LLM provider names a model. The run resolves to it, and the record
    says who decided."""
    monkeypatch.setattr(
        db, "get_runner_config",
        lambda s, wid: {"run_routes": {}, "model_routes": {}, "enabled_modules": ["interactive"]},
    )
    monkeypatch.setattr(
        db, "org_default_preset",
        lambda s, org: {"id": "p-bal", "name": "Balanced", "model": None,
                        "settings": {}, "version": 3, "tool_grants": []},
    )
    wired["floor"] = "grok-4.6"
    monkeypatch.setattr(db, "get_org_llm_config", lambda s, org: _providers("grok-4.6"))
    record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert record["resolved_settings"]["model"] == "grok-4.6"
    assert record["settings_sources"]["model"] == "org-default-provider"
    assert wired["failed"] is None


def test_every_tier_above_the_floor_still_wins(monkeypatch, wired):
    """A pin, a preset model, a legacy route and a manager override each beat
    the floor; the floor never overrides."""
    wired["floor"] = "floor-model"
    monkeypatch.setattr(db, "get_org_llm_config", lambda s, org: _providers(
        "floor-model", "pinned", "claude-opus-5", "legacy", "chosen"))
    cases = [
        ({"run_routes": {}, "model_routes": {}, "model_overrides": {"code": "pinned"}}, "pinned", "agent"),
        ({"run_routes": {"code": {"preset_id": DEEP["id"]}}, "model_routes": {}}, "claude-opus-5", "agent"),
        ({"run_routes": {}, "model_routes": {"code": "legacy"}}, "legacy", "agent"),
    ]
    for config, expect, source in cases:
        monkeypatch.setattr(db, "get_runner_config", lambda s, wid, c=config: c)
        record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
        assert record["resolved_settings"]["model"] == expect, config
        assert record["settings_sources"]["model"] == source
    monkeypatch.setattr(db, "get_runner_config", lambda s, wid: {"run_routes": {}, "model_routes": {}})
    run = {**RUN, "input_context": {"settings_override": {"manager": {"model": "chosen"}}}}
    record = worker_router.stamp_run_settings(object(), run, dict(WORKER))
    assert record["resolved_settings"]["model"] == "chosen"
    assert record["settings_sources"]["model"] == "manager"


def test_an_interactive_agent_with_no_model_anywhere_is_failed_at_claim(monkeypatch, wired):
    """The runner used to spend the claim to say 'nothing was spent'. Now the
    claim fails the run with the three-place sentence and the runner never
    spawns."""
    monkeypatch.setattr(
        db, "get_runner_config",
        lambda s, wid: {"run_routes": {}, "model_routes": {}, "enabled_modules": ["interactive"],
                        "enabled_kinds": ["code"]},
    )
    monkeypatch.setattr(
        db, "org_default_preset",
        lambda s, org: {"id": "p-bal", "name": "Balanced", "model": None,
                        "settings": {}, "version": 3, "tool_grants": []},
    )
    wired["floor"] = None
    with pytest.raises(HTTPException) as e:
        worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert e.value.status_code == 409
    detail = e.value.detail
    assert detail.startswith("pod-1 has no model for any of the roles it claims (Programming).")
    assert "Model per role" in detail
    assert "Balanced" in detail and "has none today" in detail
    assert "Settings → LLM providers" in detail
    rid, error, name = wired["failed"]
    assert rid == RUN["id"] and "Settings → LLM providers" in error and name == "pod-1"


def test_a_non_interactive_run_with_no_model_is_still_never_refused(monkeypatch, wired):
    """Unchanged for the CLIs that carry their own default: a null model means
    the gateway answers with the org default at call time (US-27.8)."""
    monkeypatch.setattr(
        db, "get_runner_config",
        lambda s, wid: {"run_routes": {}, "model_routes": {}, "enabled_modules": ["claude"]},
    )
    wired["floor"] = None
    record = worker_router.stamp_run_settings(object(), dict(RUN), dict(WORKER))
    assert record["resolved_settings"] == {}
    assert wired["failed"] is None
