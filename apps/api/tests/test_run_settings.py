"""US-32.6 + US-32.7: routes pick a preset, and a run records what it ran under.

Three layers decide how a run executes and precedence is manager > supervisor >
agent > org default. These tests pin that order, the per-setting (not per-layer)
merge, the provenance label on each value, and the route shapes the config
endpoint will and will not accept.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import run_settings
from app.routers.runner_socket import (
    ROUTE_KINDS,
    RunnerConfigBody,
    _validate_config_body,
    _validate_run_routes,
)

DEEP = {
    "id": "11111111-1111-1111-1111-111111111111",
    "name": "Deep",
    "version": 3,
    "model": "claude-opus-5",
    "settings": {"effort": "high", "max_turns": 80},
}
BALANCED = {
    "id": "22222222-2222-2222-2222-222222222222",
    "name": "Balanced",
    "version": 1,
    "model": "claude-sonnet-5",
    "settings": {"effort": "medium", "max_turns": 40},
}
BY_ID = {DEEP["id"]: DEEP, BALANCED["id"]: BALANCED}


def _resolve(**kw):
    base = dict(
        kind="code",
        run_routes={},
        presets_by_id=BY_ID,
        org_default=BALANCED,
    )
    base.update(kw)
    return run_settings.resolve(**base)


# ------------------------------------------------------------------- layer 0/1


def test_an_unset_route_inherits_the_org_default_preset():
    out = _resolve()
    assert out.values["effort"] == "medium"
    assert out.sources["effort"] == run_settings.ORG_DEFAULT
    assert out.preset_name == "Balanced"
    assert out.preset_version == 1


def test_a_route_naming_a_preset_uses_it_and_records_name_and_version():
    out = _resolve(run_routes={"code": {"preset_id": DEEP["id"]}})
    assert out.values["effort"] == "high"
    assert out.values["max_turns"] == 80
    assert out.values["model"] == "claude-opus-5"
    assert set(out.sources.values()) == {run_settings.AGENT}
    assert (out.preset_name, out.preset_version) == ("Deep", 3)


def test_a_route_only_applies_to_its_own_kind():
    routes = {"code": {"preset_id": DEEP["id"]}}
    assert _resolve(kind="plan", run_routes=routes).values["effort"] == "medium"
    assert _resolve(kind="code", run_routes=routes).values["effort"] == "high"


def test_a_custom_route_stores_its_settings_inline_with_no_preset():
    out = _resolve(
        run_routes={"code": {"custom": {"effort": "low", "model": "grok-4"}}}
    )
    assert out.values == {"effort": "low", "model": "grok-4"}
    assert out.sources["effort"] == run_settings.AGENT
    assert out.preset_id is None and out.preset_version is None


def test_a_route_pointing_at_a_deleted_preset_falls_back_and_says_so():
    """An archived preset must not leave the run unconfigured and silent."""
    out = _resolve(run_routes={"code": {"preset_id": "33333333-3333-3333-3333-333333333333"}})
    assert out.values["effort"] == "medium"
    assert out.sources["effort"] == run_settings.ORG_DEFAULT
    assert out.preset_name == "Balanced"


def test_an_org_with_no_default_preset_resolves_to_nothing_not_an_error():
    out = _resolve(org_default=None)
    assert out.values == {}
    assert out.sources == {}


def test_the_pre_preset_model_route_still_reaches_an_untuned_agent():
    """An agent nobody has re-tuned since us-32.6 keeps the model its manager
    chose — the forward migration covers stored rows, this covers the read."""
    out = _resolve(org_default=None, legacy_model="claude-haiku-4-5")
    assert out.values["model"] == "claude-haiku-4-5"
    assert out.sources["model"] == run_settings.AGENT


def test_a_preset_model_beats_the_legacy_model_route():
    out = _resolve(
        run_routes={"code": {"preset_id": DEEP["id"]}},
        legacy_model="claude-haiku-4-5",
    )
    assert out.values["model"] == "claude-opus-5"


# --------------------------------------------------------- US-66.1 model pin


def test_an_agent_model_pin_beats_the_org_default():
    out = _resolve(agent_model_override="llama-3.3-70b-versatile")
    assert out.values["model"] == "llama-3.3-70b-versatile"
    assert out.sources["model"] == run_settings.AGENT
    # every other setting still comes from the preset chain, unchanged
    assert out.values["effort"] == "medium"
    assert out.sources["effort"] == run_settings.ORG_DEFAULT


def test_an_agent_model_pin_beats_the_legacy_model_route():
    out = _resolve(
        org_default=None,
        legacy_model="claude-haiku-4-5",
        agent_model_override="llama-3.3-70b-versatile",
    )
    assert out.values["model"] == "llama-3.3-70b-versatile"


def test_an_agent_model_pin_does_not_override_an_explicit_named_preset_route():
    """The more specific choice wins: a kind explicitly routed to a preset
    keeps that preset's model, not the agent's coarser per-kind pin."""
    out = _resolve(
        run_routes={"code": {"preset_id": DEEP["id"]}},
        agent_model_override="llama-3.3-70b-versatile",
    )
    assert out.values["model"] == "claude-opus-5"


def test_resolve_applies_whatever_override_the_caller_passed_for_this_kind():
    """resolve() takes one already-scoped-to-this-kind override string; the
    per-kind lookup out of `model_overrides` is the CALLER's job
    (worker.stamp_run_settings) — covered in test_run_settings_delivery.py."""
    out = _resolve(kind="plan", agent_model_override="llama-3.3-70b-versatile")
    assert out.values["model"] == "llama-3.3-70b-versatile"


def test_the_manager_still_outranks_an_agent_model_pin():
    out = _resolve(
        agent_model_override="llama-3.3-70b-versatile",
        manager_override={"model": "claude-opus-5"},
    )
    assert out.values["model"] == "claude-opus-5"
    assert out.sources["model"] == run_settings.MANAGER


# --------------------------------------------------------------- precedence


def test_the_supervisor_outranks_the_agent_default():
    out = _resolve(
        run_routes={"code": {"preset_id": BALANCED["id"]}},
        supervisor_override={"effort": "high"},
    )
    assert out.values["effort"] == "high"
    assert out.sources["effort"] == run_settings.SUPERVISOR


def test_the_manager_outranks_the_supervisor():
    out = _resolve(
        supervisor_override={"effort": "high"},
        manager_override={"effort": "low"},
    )
    assert out.values["effort"] == "low"
    assert out.sources["effort"] == run_settings.MANAGER


def test_the_merge_is_per_setting_not_per_layer():
    """A supervisor escalating effort must not silently drop the manager's turn
    ceiling along with it."""
    out = _resolve(
        run_routes={"code": {"preset_id": BALANCED["id"]}},
        supervisor_override={"effort": "high"},
        manager_override={"max_turns": 10},
    )
    assert out.values["effort"] == "high"
    assert out.values["max_turns"] == 10
    assert out.values["model"] == "claude-sonnet-5"
    assert out.sources == {
        "effort": run_settings.SUPERVISOR,
        "max_turns": run_settings.MANAGER,
        "model": run_settings.AGENT,
    }


def test_an_override_of_nothing_does_not_erase_the_layer_below():
    out = _resolve(
        run_routes={"code": {"preset_id": DEEP["id"]}},
        supervisor_override={"effort": None, "max_turns": ""},
    )
    assert out.values["effort"] == "high"
    assert out.values["max_turns"] == 80
    assert out.sources["effort"] == run_settings.AGENT


def test_a_setting_outside_the_resolvable_set_never_reaches_a_run():
    out = _resolve(manager_override={"concurrency": 8, "effort": "high"})
    assert "concurrency" not in out.values
    assert out.values["effort"] == "high"


def test_the_record_is_flat_values_plus_provenance():
    """Not a preset reference: a run must stay explainable after the preset it
    came from has been edited or deleted."""
    record = _resolve(run_routes={"code": {"preset_id": DEEP["id"]}}).as_record()
    assert record["resolved_settings"] == {
        "effort": "high",
        "max_turns": 80,
        "model": "claude-opus-5",
    }
    assert set(record["settings_sources"]) == {"effort", "max_turns", "model"}
    assert record["preset_name"] == "Deep"
    assert record["preset_version"] == 3


def test_editing_the_preset_afterwards_cannot_change_a_stamped_record():
    record = _resolve(run_routes={"code": {"preset_id": DEEP["id"]}}).as_record()
    DEEP["settings"]["effort"] = "low"  # the preset changes under the run
    DEEP["version"] = 4
    try:
        assert record["resolved_settings"]["effort"] == "high"
        assert record["preset_version"] == 3
    finally:
        DEEP["settings"]["effort"] = "high"
        DEEP["version"] = 3


def test_preset_values_flatten_the_model_column_into_the_settings():
    assert run_settings.preset_values(DEEP)["model"] == "claude-opus-5"
    assert run_settings.preset_values({"settings": {"effort": "low"}}) == {
        "effort": "low"
    }
    assert run_settings.preset_values(None) == {}


def test_route_for_tolerates_junk():
    assert run_settings.route_for(None, "code") is None
    assert run_settings.route_for("code", "code") is None
    assert run_settings.route_for({"code": "Deep"}, "code") is None


# --------------------------------------------------- config body validation


def test_every_dispatchable_kind_is_a_valid_route_and_brain_is_not():
    for kind in ROUTE_KINDS:
        _validate_run_routes({kind: {"preset_id": DEEP["id"]}})
    with pytest.raises(HTTPException) as e:
        _validate_run_routes({"brain": {"preset_id": DEEP["id"]}})
    assert e.value.status_code == 422
    assert "brain" in e.value.detail


def test_a_route_that_is_both_a_preset_and_custom_is_refused():
    with pytest.raises(HTTPException) as e:
        _validate_run_routes(
            {"code": {"preset_id": DEEP["id"], "custom": {"effort": "low"}}}
        )
    assert "pick one" in e.value.detail


def test_a_route_that_is_neither_says_to_leave_it_out():
    with pytest.raises(HTTPException) as e:
        _validate_run_routes({"code": {}})
    assert "inherit" in e.value.detail


def test_a_preset_id_that_is_not_an_id_is_refused():
    with pytest.raises(HTTPException):
        _validate_run_routes({"code": {"preset_id": "Deep"}})


def test_custom_settings_go_through_the_preset_validator():
    _validate_run_routes({"code": {"custom": {"effort": "high", "model": "m"}}})
    with pytest.raises(HTTPException) as e:
        _validate_run_routes({"code": {"custom": {"effort": "extreme"}}})
    assert "low, medium, high" in e.value.detail
    with pytest.raises(HTTPException) as e:
        _validate_run_routes({"code": {"custom": {"concurrency": 8}}})
    assert "concurrency" in e.value.detail


def test_a_custom_model_must_be_a_string():
    with pytest.raises(HTTPException):
        _validate_run_routes({"code": {"custom": {"model": 5}}})


def test_a_null_route_is_allowed_and_means_inherit():
    _validate_run_routes({"code": None})


def test_run_routes_reach_the_config_body_and_its_validator():
    body = RunnerConfigBody(
        run_routes={"code": {"preset_id": DEEP["id"]}}, enabled_modules=["claude"]
    )
    _validate_config_body(body)  # does not raise
    with pytest.raises(HTTPException):
        _validate_config_body(RunnerConfigBody(run_routes={"code": {"custom": 5}}))


# ----------------------------------------------------------- US-66.1 config body


def test_model_overrides_is_not_a_platform_owned_field():
    """The whole point: an org member (not a platform admin) may set this,
    unlike the six fields us-57.6 locked down."""
    from app.routers.runner_socket import _PLATFORM_OWNED_FIELDS

    assert "model_overrides" not in _PLATFORM_OWNED_FIELDS


def test_model_overrides_rejects_an_unknown_kind():
    with pytest.raises(HTTPException) as e:
        _validate_config_body(RunnerConfigBody(model_overrides={"brain": "m"}))
    assert "brain" in e.value.detail


def test_model_overrides_rejects_a_non_string_model():
    with pytest.raises(HTTPException):
        _validate_config_body(RunnerConfigBody(model_overrides={"code": 5}))


def test_model_overrides_accepts_every_dispatchable_kind():
    body = RunnerConfigBody(
        model_overrides={k: "some-model" for k in ROUTE_KINDS}
    )
    _validate_config_body(body)  # does not raise
