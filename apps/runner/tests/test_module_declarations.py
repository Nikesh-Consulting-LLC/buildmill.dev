"""US-32.4: a module declares what it can be told.

Every tuning dial worth having is CLI-specific. These tests pin that each
shipped module declares its own vocabulary, that the MCP gate us-31.9 introduced
as a one-off boolean now reads from that declaration instead of a second source
of truth, and that a resolved setting a module cannot express is reported rather
than silently dropped.
"""

from __future__ import annotations

from supervisor import modules
from supervisor.modules import base
from supervisor.modules.base import Knob, declaration, supports
from supervisor.workloop import undeliverable_settings


def _module(name):
    m = modules.get(name)
    assert m is not None, f"{name} is not registered"
    return m


# ------------------------------------------------------------- declarations


def test_every_shipped_module_declares_only_canonical_settings():
    """A knob named something nothing else understands renders as a field that
    saves and never arrives anywhere."""
    for name in ("claude", "grok", "opencode", "sim"):
        for knob in _module(name).settings:
            assert knob.name in base.KNOWN_SETTINGS, (name, knob.name)
            assert knob.delivery in base.DELIVERIES, (name, knob.delivery)


def test_claude_declares_the_full_vocabulary():
    names = {k.name for k in _module("claude").settings}
    assert names == {
        "model",
        "fallback_model",
        "effort",
        "max_turns",
        "standing_instructions",
        "mcp",
        # US-52.1: the billing mode — api (metered gateway) | subscription.
        "auth",
    }
    flags = {k.name: k.flag for k in _module("claude").settings}
    assert flags["effort"] == "--effort"
    assert flags["fallback_model"] == "--fallback-model"
    assert flags["standing_instructions"] == "--append-system-prompt"


def test_effort_declares_every_level_the_cli_takes():
    """US-32.10: `claude --help` — `--effort <level>` takes
    `low, medium, high, xhigh, max`. Declared as three, the app's whole tuning
    surface stopped one level below `xhigh`, the level recommended for coding
    work, with no way to reach it. The declaration is what the settings page
    renders, so it is the list that has to be right."""
    knob = next(k for k in _module("claude").settings if k.name == "effort")
    assert knob.choices == ("low", "medium", "high", "xhigh", "max")


def test_grok_and_opencode_declare_less_and_say_so():
    """The point of the declaration: neither has an effort level, and the
    settings page must be able to show that rather than make every module
    look identical. Grok's real CLI (unlike the npm impostor it was first
    measured against) does support MCP, so it alone also declares that knob."""
    for name in ("grok", "opencode"):
        assert not supports(_module(name), "effort")
    opencode_names = {k.name for k in _module("opencode").settings}
    assert opencode_names == {"model", "standing_instructions"}
    assert not supports(_module("opencode"), "mcp")
    grok_names = {k.name for k in _module("grok").settings}
    assert grok_names == {"model", "standing_instructions", "mcp"}
    assert supports(_module("grok"), "mcp")


def test_a_module_with_no_flag_can_still_be_told_via_the_prompt():
    """OpenCode's positional-prompt shape is why `prompt` is a real delivery."""
    knob = next(
        k for k in _module("opencode").settings if k.name == "standing_instructions"
    )
    assert knob.delivery == "prompt"
    assert knob.flag == ""


def test_sim_declares_one_knob_and_needs_no_repo():
    """The proof that adding a module makes it configurable with no frontend
    change: `sim` grew a settings field by declaring it and nothing else."""
    sim = _module("sim")
    assert [k.name for k in sim.settings] == ["standing_instructions"]
    assert declaration(sim)["needs_repo"] is False


def test_declaration_shape_is_what_the_hello_carries():
    d = declaration(_module("claude"))
    assert d["module"] == "claude"
    assert "code" in d["capabilities"] and "plan" in d["capabilities"]
    assert d["needs_repo"] is True
    knob = next(k for k in d["settings"] if k["name"] == "effort")
    assert knob["kind"] == "enum"
    assert "high" in knob["choices"]
    assert knob["help"]


def test_the_permission_mode_is_not_declared_by_anything():
    """US-47.1. Measured against the real CLI: under `default`, `acceptEdits`
    and `plan`, zero MCP calls reach the server, because a headless run has no
    approval channel and only `bypassPermissions` pre-approves the tools. Every
    code and plan run is handed --mcp-config, so three of the four choices
    produce a run that cannot read its own work item — and `plan` does it while
    exiting 0. The module states the mode itself instead."""
    from supervisor.modules.claude import PERMISSION_MODE

    assert "permission_mode" not in base.KNOWN_SETTINGS
    for name in ("claude", "grok", "opencode", "sim"):
        assert not supports(_module(name), "permission_mode")
    assert PERMISSION_MODE == ["--permission-mode", "bypassPermissions"]


def test_a_preset_still_carrying_it_is_reported_not_dropped():
    """An org's presets predate this change. The setting must surface as
    undeliverable rather than looking like it arrived somewhere."""
    lines = undeliverable_settings(_module("claude"), {"permission_mode": "plan"})
    assert len(lines) == 1
    assert "permission_mode" in lines[0]


def test_declarations_are_reported_per_module_not_merged():
    """A merged answer would put the union of Claude's and Grok's knobs on
    both."""
    decls = {d["module"]: d for d in modules.declarations()}
    assert {"claude", "grok", "opencode", "sim"} <= set(decls)
    claude = {k["name"] for k in decls["claude"]["settings"]}
    grok = {k["name"] for k in decls["grok"]["settings"]}
    assert "effort" in claude and "effort" not in grok


# ------------------------------------------- us-31.9's MCP gate, generalized


def test_mcp_support_now_reads_from_the_declaration():
    """One source of truth. Before this story it was a separate boolean that
    could disagree with the flags the module actually passes."""
    assert _module("claude").supports_mcp is True
    assert supports(_module("claude"), "mcp") is True
    # Grok's real CLI supports MCP via its own config.toml (US-10.5
    # follow-up) — the npm impostor it was first measured against did not.
    assert _module("grok").supports_mcp is True
    assert _module("opencode").supports_mcp is False


def test_the_mcp_gate_still_behaves_exactly_as_us_31_9_left_it():
    from supervisor.workloop import module_can_do

    ok, why = module_can_do("claude", "code")
    assert ok and why is None
    ok, why = module_can_do("grok", "code")
    assert ok and why is None
    # `sim` opens no checkout, so the requirement must not reach it.
    ok, why = module_can_do("sim", "code")
    assert ok and why is None
    # prd/breakdown answer in stdout and never needed MCP.
    ok, why = module_can_do("grok", "prd")
    assert ok and why is None


def test_a_new_knob_makes_a_module_stop_being_flagged(monkeypatch):
    """The mechanism is general: nothing about `effort` is special-cased."""
    grok = _module("grok")
    assert undeliverable_settings(grok, {"effort": "high"})
    monkeypatch.setattr(
        type(grok),
        "settings",
        (*grok.settings, Knob("effort", kind="enum", delivery="argv", flag="--effort")),
    )
    assert undeliverable_settings(grok, {"effort": "high"}) == []


# ------------------------------------------- undeliverable settings reporting


def test_a_setting_the_module_cannot_express_is_named_with_the_module():
    lines = undeliverable_settings(_module("grok"), {"effort": "high"})
    assert len(lines) == 1
    assert "grok" in lines[0] and "effort" in lines[0]


def test_a_setting_nothing_understands_is_reported_too():
    lines = undeliverable_settings(_module("claude"), {"telepathy": "on"})
    assert len(lines) == 1
    assert "telepathy" in lines[0]


def test_supported_and_unset_settings_are_not_reported():
    resolved = {
        "effort": "high",  # claude supports it
        "model": "claude-sonnet-5",  # claude supports it
        "fallback_model": None,  # never asked for
        "standing_instructions": "",  # never asked for
        "max_turns": 0,  # zero is a value, not an absence — see below
    }
    lines = undeliverable_settings(_module("claude"), resolved)
    assert lines == []


def test_no_resolved_settings_is_not_a_problem():
    assert undeliverable_settings(_module("claude"), None) == []
    assert undeliverable_settings(_module("claude"), {}) == []
