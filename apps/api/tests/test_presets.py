"""US-32.5: a preset is a named bundle of run settings.

The load-bearing constraint is us-27.8's: `llm_providers` is org-scoped with a
curated model list, and the model is what resolves a call's provider. So a
preset naming a model the org does not offer routes nowhere — and must be
refused at Save, not discovered ninety seconds into a run on a remote machine.
These tests pin that refusal, the settings validation, the module-support
warning from us-32.4's declarations, and the re-seed diff that keeps a
superadmin's template edit from silently rewriting how every org works.
"""

from __future__ import annotations

import pytest

from app import presets


def _providers(*models, name="Anthropic", default=None):
    return [
        {
            "name": name,
            "provider_type": "anthropic",
            "models": list(models),
            "default_model": default,
        }
    ]


# ------------------------------------------------------------------- settings


def test_a_full_bundle_survives_validation():
    out = presets.clean_settings(
        {
            "effort": "high",
            "max_turns": 40,
            "max_minutes": 30,
            "fallback_model": "claude-haiku-4-5",
            "standing_instructions": "  find the root cause  ",
        }
    )
    assert out == {
        "effort": "high",
        "max_turns": 40,
        "max_minutes": 30,
        "fallback_model": "claude-haiku-4-5",
        "standing_instructions": "find the root cause",
    }


def test_an_unknown_setting_is_refused_by_name_not_dropped():
    """us-32.3's lesson: a stored setting nothing reads is a control that
    appears to work."""
    with pytest.raises(presets.PresetInvalid) as e:
        presets.clean_settings({"concurrency": 8})
    assert "concurrency" in str(e.value)
    assert "effort" in str(e.value)  # says what IS accepted


def test_the_model_is_not_a_preset_setting_it_is_a_column():
    """It has to be validated against the org's providers, which a jsonb blob
    cannot be."""
    with pytest.raises(presets.PresetInvalid):
        presets.clean_settings({"model": "claude-sonnet-5"})


def test_mcp_is_not_a_tunable():
    """A module either takes an MCP config or it does not (us-31.9/us-32.4)."""
    with pytest.raises(presets.PresetInvalid):
        presets.clean_settings({"mcp": True})


@pytest.mark.parametrize("bad", ["extreme", "HIGH", 3, True])
def test_effort_must_be_one_of_the_levels(bad):
    with pytest.raises(presets.PresetInvalid) as e:
        presets.clean_settings({"effort": bad})
    assert "low, medium, high" in str(e.value)


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_every_level_the_cli_takes_is_accepted(level):
    """US-32.10: `claude --help` says `low, medium, high, xhigh, max`. The
    allow-list was written against the first three, so the two levels a hard
    code run most wants were not merely unset — they could not be saved."""
    assert presets.clean_settings({"effort": level}) == {"effort": level}


@pytest.mark.parametrize("mode", ["plan", "bypassPermissions", "default"])
def test_the_permission_mode_is_gone_entirely(mode):
    """US-47.1: it was overridden by the machine default before it ever reached
    the CLI, and measured against the real CLI only `bypassPermissions` lets a
    headless run call an MCP tool at all — so the runner states that itself.
    Refused as unknown, like any other name a preset may not carry, rather than
    accepted and ignored."""
    assert "permission_mode" not in presets.PRESET_SETTINGS
    with pytest.raises(presets.PresetInvalid, match="permission_mode"):
        presets.clean_settings({"permission_mode": mode})


@pytest.mark.parametrize("bad", [0, -1, 501, "many", None if False else "x"])
def test_turn_ceiling_is_bounded(bad):
    with pytest.raises(presets.PresetInvalid):
        presets.clean_settings({"max_turns": bad})


def test_the_spend_ceiling_is_gone_entirely():
    """US-37.2: money is bounded per project, before a run is created. The
    per-run ceiling is removed rather than left switched off, so the key is not
    merely unbounded here — it is refused as unknown, like any other name a
    preset may not carry."""
    assert "max_budget_usd" not in presets.PRESET_SETTINGS
    with pytest.raises(presets.PresetInvalid, match="max_budget_usd"):
        presets.clean_settings({"max_budget_usd": 8})


def test_unset_means_inherit_and_is_dropped_not_stored_as_null():
    out = presets.clean_settings(
        {
            "effort": None,
            "max_minutes": "",
            "standing_instructions": "   ",
            "fallback_model": "",
            "max_turns": None,
        }
    )
    assert out == {}


def test_settings_must_be_an_object():
    with pytest.raises(presets.PresetInvalid):
        presets.clean_settings(["effort"])
    assert presets.clean_settings(None) == {}


def test_instructions_are_bounded_rather_than_refused():
    out = presets.clean_settings({"standing_instructions": "x" * 9000})
    assert len(out["standing_instructions"]) == presets.MAX_INSTRUCTIONS


# ----------------------------------------------------------------------- name


def test_a_name_is_required_and_trimmed():
    assert presets.clean_name("  Deep  ") == "Deep"
    for bad in ("", "   ", None):
        with pytest.raises(presets.PresetInvalid):
            presets.clean_name(bad)
    with pytest.raises(presets.PresetInvalid):
        presets.clean_name("x" * 61)


# ---------------------------------------------------------------------- model


def test_a_model_the_org_offers_is_accepted():
    assert (
        presets.validate_model("claude-sonnet-5", _providers("claude-sonnet-5"))
        == "claude-sonnet-5"
    )


def test_the_default_model_counts_as_offered():
    assert (
        presets.validate_model(
            "claude-opus-5", _providers("other", default="claude-opus-5")
        )
        == "claude-opus-5"
    )


def test_no_model_means_inherit_the_org_default():
    assert presets.validate_model(None, _providers("m")) is None
    assert presets.validate_model("", _providers("m")) is None
    assert presets.validate_model("   ", []) is None


def test_a_model_no_provider_offers_is_refused_naming_what_was_checked():
    with pytest.raises(presets.PresetInvalid) as e:
        presets.validate_model(
            "gpt-9",
            _providers("claude-sonnet-5", name="Anthropic")
            + _providers("grok-4", name="xAI"),
        )
        # us-27.8: the model decides the provider, so an unlisted one routes
        # nowhere — the refusal has to say that, not just "invalid".
    msg = str(e.value)
    assert "gpt-9" in msg
    assert "Anthropic" in msg and "xAI" in msg
    assert "decides which provider" in msg


def test_an_org_with_no_providers_is_told_to_add_one():
    with pytest.raises(presets.PresetInvalid) as e:
        presets.validate_model("claude-sonnet-5", [])
    assert "no LLM providers" in str(e.value)


# ------------------------------------------ module support (us-32.4 feeding in)


def test_a_setting_no_enabled_module_supports_is_flagged_with_the_modules():
    support = {"grok": {"model", "standing_instructions"}}
    warnings = presets.unsupported_settings({"effort": "high"}, support)
    assert len(warnings) == 1
    assert "effort" in warnings[0] and "grok" in warnings[0]


def test_a_setting_one_module_supports_is_not_flagged():
    support = {
        "grok": {"model", "standing_instructions"},
        "claude": {"effort", "model"},
    }
    assert presets.unsupported_settings({"effort": "high"}, support) == []


def test_nothing_is_flagged_when_no_module_has_declared_yet():
    """Silence, not noise: a machine that has never connected has declared
    nothing, and warning about every setting would be warning about none."""
    assert presets.unsupported_settings({"effort": "high"}, {}) == []


def test_every_unsupported_setting_gets_its_own_line():
    support = {"grok": {"model"}}
    warnings = presets.unsupported_settings(
        {"effort": "high", "max_turns": 10, "fallback_model": "x"}, support
    )
    assert len(warnings) == 3


# --------------------------------------------------------------- re-seed diff


def test_the_reseed_diff_names_each_changed_setting_and_both_values():
    preset = {
        "settings": {"effort": "medium", "max_turns": 40},
        "description": "old",
    }
    template = {
        "settings": {"effort": "high", "max_turns": 40, "max_minutes": 5},
        "description": "new",
    }
    diff = presets.reseed_diff(preset, template)
    by_setting = {d["setting"]: d for d in diff}
    assert by_setting["effort"] == {
        "setting": "effort",
        "from": "medium",
        "to": "high",
    }
    # A setting the template adds shows as unset → value.
    assert by_setting["max_minutes"]["from"] is None
    assert by_setting["max_minutes"]["to"] == 5
    # An unchanged setting is not noise in the offer.
    assert "max_turns" not in by_setting
    assert by_setting["description"]["to"] == "new"


def test_an_identical_template_offers_nothing():
    same = {"settings": {"effort": "high"}, "description": "d"}
    assert presets.reseed_diff(dict(same), dict(same)) == []


def test_a_setting_the_template_dropped_shows_as_going_unset():
    diff = presets.reseed_diff(
        {"settings": {"effort": "high"}, "description": ""},
        {"settings": {}, "description": ""},
    )
    assert diff == [{"setting": "effort", "from": "high", "to": None}]


# ------------------------------------------------------------------ seeded set


def test_the_seeded_templates_are_valid_by_their_own_rules():
    """The migration's four templates go through the same validator a manager's
    edit does — a seed the API would refuse is a seed that cannot be edited."""
    import pathlib
    import re

    sql = (
        pathlib.Path(__file__).resolve().parents[3]
        / "infra"
        / "supabase"
        / "migrations"
        / "157_agent_presets.sql"
    ).read_text(encoding="utf-8")
    keys = re.findall(r"^\s+'(fast|balanced|deep|investigate)',$", sql, re.M)
    assert set(keys) == {"fast", "balanced", "deep", "investigate"}

    def migration(name):
        return (
            pathlib.Path(__file__).resolve().parents[3]
            / "infra" / "supabase" / "migrations" / name
        ).read_text(encoding="utf-8")

    # Every setting name the seed uses must be one a preset may carry -- except
    # the two later stories retired: US-37.2's `max_budget_usd` and US-47.1's
    # `permission_mode`. 157 is history and must not be rewritten; a later
    # migration correcting it is the pattern, and a fresh database replaying all
    # of them ends up correct. Asserted, not assumed -- and asserted for BOTH
    # tables, because 164 stripped `max_budget_usd` from `agent_presets` and not
    # from `preset_templates`, so every org seeded between 164 and 181 got a key
    # the API refuses.
    retired = {"max_budget_usd": "164_project_budgets.sql",
               "permission_mode": "181_permission_mode_is_not_a_preset_setting.sql"}
    used = set(re.findall(
        r"'(effort|permission_mode|max_turns|max_budget_usd|standing_instructions|fallback_model)'",
        sql,
    ))
    assert used - set(retired) <= set(presets.PRESET_SETTINGS)
    corpus = "\n".join(migration(f) for f in sorted(set(retired.values())))
    for key in used & set(retired):
        assert f"settings - '{key}'" in migration(retired[key])
        for table in ("agent_presets", "preset_templates"):
            swept = re.search(
                rf"update public\.{table}\s+set settings = settings - '{key}'", corpus
            )
            assert swept, f"{key} is left behind in {table}"

    # And every value it names must pass.
    for effort in re.findall(r"'effort', '(\w+)'", sql):
        presets.clean_settings({"effort": effort})
