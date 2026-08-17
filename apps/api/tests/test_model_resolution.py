"""us-116.1: the resolver's inputs are built once, and a session asks about a
kind the agent actually claims."""

from app import model_resolution as mr
from app.routers.runner_socket import ROUTE_KINDS


def _inputs(config, org_default=None, presets=None):
    return mr.ResolverInputs(
        org_id="org-1",
        config=config,
        presets_by_id=presets or {},
        org_default=org_default,
    )


def test_every_route_kind_has_a_role_name():
    """The refusal names roles in the manager's vocabulary; a kind without one
    would leak a raw slug into the sentence."""
    assert set(mr.ROLE_OF_KIND) == set(ROUTE_KINDS)


def test_claimed_kinds_follow_route_kinds_order_and_null_means_all():
    assert mr.claimed_kinds({"enabled_kinds": None}) == list(ROUTE_KINDS)
    assert mr.claimed_kinds({}) == list(ROUTE_KINDS)
    assert mr.claimed_kinds({"enabled_kinds": []}) == []
    # dictionary order in, ROUTE_KINDS order out
    assert mr.claimed_kinds({"enabled_kinds": ["deploy", "prd", "code"]}) == [
        "prd", "code", "deploy",
    ]


def test_a_session_tries_code_first_when_claimed_else_route_order():
    assert mr.session_kind_order({"enabled_kinds": ["test", "code", "prd"]}) == [
        "code", "prd", "test",
    ]
    assert mr.session_kind_order({"enabled_kinds": ["deploy", "plan"]}) == ["plan", "deploy"]
    assert mr.session_kind_order({"enabled_kinds": None})[0] == "code"


def test_resolve_session_picks_the_first_claimed_kind_with_a_model():
    picked = mr.resolve_session(
        _inputs(
            {
                "enabled_kinds": ["prd", "plan", "deploy"],
                "model_overrides": {"deploy": "claude-haiku-4-5"},
            }
        )
    )
    assert (picked.model, picked.kind) == ("claude-haiku-4-5", "deploy")
    assert picked.tried == ["prd", "plan", "deploy"]
    assert picked.resolved is not None
    assert picked.resolved.sources["model"] == "agent"


def test_resolve_session_reports_what_it_tried_when_nothing_resolves():
    picked = mr.resolve_session(_inputs({"enabled_kinds": ["release"], "model_overrides": {}}))
    assert picked.model is None and picked.kind is None
    assert picked.tried == ["release"]


def test_the_org_default_preset_model_reaches_the_first_tried_kind():
    picked = mr.resolve_session(
        _inputs(
            {"enabled_kinds": ["test"], "model_overrides": {}},
            org_default={"id": "p", "name": "Balanced", "model": "claude-sonnet-5",
                         "settings": {}, "version": 1, "tool_grants": []},
        )
    )
    assert (picked.model, picked.kind) == ("claude-sonnet-5", "test")
    assert picked.resolved.sources["model"] == "org-default"


def test_the_refusal_sentence_names_roles_and_the_default_presets_gap():
    text = mr.no_model_refusal(
        "Architect", ["prd", "plan", "guidelines"],
        {"name": "Balanced", "model": None},
    )
    assert text.startswith("Architect has no model for any of the roles it claims (Planning).")
    assert "Balanced" in text and "has none today" in text
    text2 = mr.no_model_refusal("DevOps", ["test", "release"], None)
    assert "(Testing, Deployment)" in text2
    text3 = mr.no_model_refusal("Blank", [], None)
    assert "claims no roles" in text3


def test_the_floor_is_used_only_when_nothing_above_names_a_model():
    """us-116.7."""
    floor = mr.ResolverInputs(org_id="o", config={"enabled_kinds": ["prd"]},
                              org_default_model="grok-4.6")
    picked = mr.resolve_session(floor)
    assert (picked.model, picked.kind) == ("grok-4.6", "prd")
    assert picked.resolved.sources["model"] == "org-default-provider"
    pinned = mr.ResolverInputs(
        org_id="o", config={"enabled_kinds": ["prd"], "model_overrides": {"prd": "pinned"}},
        org_default_model="grok-4.6",
    )
    assert mr.resolve_session(pinned).model == "pinned"


def test_the_refusal_names_the_default_provider_as_the_third_place():
    text = mr.no_model_refusal("DevOps", ["release"], None)
    assert "Settings → LLM providers" in text
    text2 = mr.no_model_refusal("DevOps", ["release"], {"name": "Balanced", "model": None})
    assert "Balanced" in text2 and "Settings → LLM providers" in text2
