"""The one resolver for a run's effective settings (US-32.7).

Three layers get to decide how a run executes — the agent's standing default,
the supervisor when it retries a failure, and the manager at dispatch. That is
the model asked for: defaults set once, an override the supervisor may take when
necessary, and the manager's own choice at the moment of dispatch.

Precedence is **manager > supervisor > agent model pin > agent route/org default
> org default provider model** (the last for `model` only, us-116.7).
A manager's explicit choice is the strongest signal available and outranks a
supervisor escalation; the supervisor's override outranks the standing default
because it is reacting to a failure the default already failed at.

US-66.1: an agent's own `model_overrides[kind]` sits just above the route/
org-default tier, for the `model` setting only — it wins over the org's default
preset and the legacy platform fallback, but a kind explicitly routed to a named
preset keeps that preset's model (the more specific choice). This is deliberately
narrower than us-57.6's "no per-agent overrides": which model an agent's CLI talks
to is a property of the agent/module pairing, not a platform policy question the
way autonomy limits are — every other setting still comes from the preset chain.

Resolution happens ONCE, here, server-side — never in the runner. A runner that
resolved its own settings would be a second implementation of these rules, and
the two would disagree.
"""

from __future__ import annotations

from typing import Any

# The layer that supplied a value, strongest last. Stored per setting on the run
# so a finished run can say who decided, not just what happened.
AGENT = "agent"
ORG_DEFAULT = "org-default"
# us-116.7: the floor for `model` only — the org's default LLM provider's own
# default model, chosen on Settings → LLM providers. Weaker than every layer
# above; recorded so a finished run says the org's default provider decided.
ORG_DEFAULT_PROVIDER = "org-default-provider"
SUPERVISOR = "supervisor"
MANAGER = "manager"

# Only these reach a run. `model` is included because a route may set one
# inline; `mcp` is not a tunable (us-31.9 / us-32.4).
# US-47.1 dropped `permission_mode`: the runner states it itself, because only
# one of its values produces a run that can call an MCP tool at all. A layer
# that can still set it here would resolve a value with nowhere to go.
RESOLVABLE = (
    "model",
    "fallback_model",
    "effort",
    "max_turns",
    "max_minutes",
    "standing_instructions",
)
# US-53.1 removed `auth`: billing never belonged in the resolver. It is a
# property of the agent (`runner_config.claude_billing`), pushed with the
# config, stamped from the config — never layered.


class Resolved:
    """The effective settings, where each came from, and the preset behind them."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}
        self.sources: dict[str, str] = {}
        self.preset_id: str | None = None
        self.preset_name: str | None = None
        self.preset_version: int | None = None
        self.tool_grants: list[str] = []

    # US-34.3: the tool grants that came with the winning preset. Tracked
    # separately from `values` because a grant is a list of ids rather than a
    # scalar to override — merging them per-setting would produce a surface
    # nobody asked for.
    tool_grants: list[str]

    def apply(self, layer: str, incoming: dict[str, Any] | None) -> None:
        """Later layers win, per setting — not wholesale.

        Per-setting rather than per-layer because a supervisor escalating effort
        must not silently drop the manager's turn ceiling along with it.
        """
        for key, value in (incoming or {}).items():
            if key not in RESOLVABLE:
                continue
            if value is None or value == "":
                continue
            self.values[key] = value
            self.sources[key] = layer

    def as_record(self) -> dict[str, Any]:
        return {
            "resolved_settings": dict(self.values),
            "settings_sources": dict(self.sources),
            "preset_id": self.preset_id,
            "preset_name": self.preset_name,
            "preset_version": self.preset_version,
        }


def preset_values(preset: dict[str, Any] | None) -> dict[str, Any]:
    """A preset flattened into settings: its own bundle plus its model column."""
    if not preset:
        return {}
    out = dict(preset.get("settings") or {})
    if preset.get("model"):
        out["model"] = preset["model"]
    return out


def route_for(run_routes: Any, kind: str) -> dict[str, Any] | None:
    """The agent's route for this run kind, if it set one."""
    if not isinstance(run_routes, dict):
        return None
    entry = run_routes.get(kind)
    return entry if isinstance(entry, dict) else None


def resolve(
    *,
    kind: str,
    run_routes: Any,
    presets_by_id: dict[str, dict[str, Any]],
    org_default: dict[str, Any] | None,
    legacy_model: str | None = None,
    agent_model_override: str | None = None,
    supervisor_override: dict[str, Any] | None = None,
    manager_override: dict[str, Any] | None = None,
    org_default_model: str | None = None,
) -> Resolved:
    """Effective settings for one run.

    `presets_by_id` and `org_default` are read by the caller so this stays pure
    and testable — the precedence rules are the thing worth pinning, and a
    resolver that opens its own connection cannot be pinned cheaply.

    us-116.7: `org_default_model` is the floor for `model` — the org's default
    LLM provider's default model, which the gateway already answers with when
    a key carries no model. It is applied only when nothing above it named a
    model, and before the supervisor and manager layers, so those still win.
    US-78.5's refusal ("never a CLI falling back to a default nobody chose")
    now fires only when there is no pin, no preset model AND no default
    provider model: the org's default provider model IS chosen — by the
    manager, on the page whose purpose is choosing it.
    """
    out = Resolved()

    # --- layer 0/1: the agent's own route for this kind, else the org default.
    route = route_for(run_routes, kind)
    # US-66.1: a kind explicitly routed to a named preset keeps that preset's
    # model — the more specific choice wins over the agent's coarser pin.
    explicit_preset_route = False
    if route and route.get("preset_id"):
        preset = presets_by_id.get(str(route["preset_id"]))
        if preset:
            out.apply(AGENT, preset_values(preset))
            out.preset_id = str(preset["id"])
            out.preset_name = preset.get("name")
            out.preset_version = preset.get("version")
            out.tool_grants = [str(g) for g in (preset.get("tool_grants") or [])]
            explicit_preset_route = True
        elif org_default:
            # A preset that was archived or deleted must not leave the run
            # unconfigured and silent about it.
            out.apply(ORG_DEFAULT, preset_values(org_default))
            out.preset_id = str(org_default["id"])
            out.preset_name = org_default.get("name")
            out.preset_version = org_default.get("version")
    elif route and isinstance(route.get("custom"), dict):
        out.apply(AGENT, route["custom"])
    elif org_default:
        out.apply(ORG_DEFAULT, preset_values(org_default))
        out.preset_id = str(org_default["id"])
        out.preset_name = org_default.get("name")
        out.preset_version = org_default.get("version")
        out.tool_grants = [str(g) for g in (org_default.get("tool_grants") or [])]

    # US-66.1: the agent's own per-kind model pin — wins over the org default
    # (and the legacy fallback below) for `model` specifically, but never over
    # an explicit named-preset route (the more specific choice).
    if agent_model_override and not explicit_preset_route:
        out.values["model"] = agent_model_override
        out.sources["model"] = AGENT

    # The pre-preset `model_routes` value, for an agent nobody has re-tuned
    # since us-32.6. It is weaker than anything above it that named a model.
    if legacy_model and "model" not in out.values:
        out.values["model"] = legacy_model
        out.sources["model"] = AGENT

    # us-116.7: the floor. The org's default provider's own default model —
    # the manager's chosen default, and what the gateway would use anyway.
    if org_default_model and "model" not in out.values:
        out.values["model"] = str(org_default_model).strip()
        out.sources["model"] = ORG_DEFAULT_PROVIDER

    # --- layer 2: the supervisor, reacting to a failure the default failed at.
    out.apply(SUPERVISOR, supervisor_override)

    # --- layer 3: the manager, whose explicit choice outranks everything.
    out.apply(MANAGER, manager_override)

    return out
