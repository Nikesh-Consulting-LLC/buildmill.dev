"""The resolver's arguments, built once (us-116.1).

`run_settings.resolve` is the one resolver, and its docstring warns that a
second implementation of the precedence rules "would disagree". The session
path was that second implementation — `db.session_model` read
`model_overrides.code` and stopped, skipping the preset chain, the org default,
the legacy routes and every layer above the pin, and hard-coding one kind.

This module is the seam that stops it happening again: `stamp_run_settings`
(a run, at claim) and `agent_sessions.open_session` (a conversation, at open)
both build the resolver's inputs here and call the resolver through here. A
test pins that neither builds them anywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import db, run_settings
from .config import Settings

# The manager's word for each kind — the vocabulary the settings page and the
# wizard use (`apps/web/src/lib/agent-roles.ts`), so a refusal that names
# "the roles it claims" says Planning, not "prd, breakdown, plan, …".
ROLE_OF_KIND: dict[str, str] = {
    "prd": "Planning",
    "breakdown": "Planning",
    "plan": "Planning",
    "guidelines": "Planning",
    "elaborate": "Planning",
    "wireframe": "Planning",
    "code": "Programming",
    "merge": "Programming",
    "test": "Testing",
    "release": "Deployment",
    "deploy": "Deployment",
}


def route_kinds() -> tuple[str, ...]:
    """The API's own ordered list of routable kinds. Imported lazily: it lives
    beside the config validators in the runner-socket router, and two tests
    parse it off that file by name."""
    from .routers.runner_socket import ROUTE_KINDS

    return ROUTE_KINDS


@dataclass
class ResolverInputs:
    """Everything `run_settings.resolve` needs that comes from the database,
    read once per resolution — the org's presets, its default, the agent's
    config."""

    org_id: str
    config: dict[str, Any] = field(default_factory=dict)
    presets_by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    org_default: dict[str, Any] | None = None
    # us-116.7: the floor — the org's default LLM provider's default model, or
    # None when the org has none (the "nobody chose" case).
    org_default_model: str | None = None


def load_inputs(
    settings: Settings,
    org_id: str,
    *,
    worker_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> ResolverInputs:
    """Read the resolver's inputs. `config` may be passed when the caller
    already holds it; otherwise it is read for `worker_id`."""
    if config is None:
        config = db.get_runner_config(settings, str(worker_id)) if worker_id else {}
    return ResolverInputs(
        org_id=str(org_id),
        config=config or {},
        presets_by_id=db.presets_by_id(settings, str(org_id)),
        org_default=db.org_default_preset(settings, str(org_id)),
        org_default_model=db.org_default_provider_model(settings, str(org_id)),
    )


def resolve_for_kind(
    inputs: ResolverInputs,
    kind: str,
    *,
    supervisor_override: dict[str, Any] | None = None,
    manager_override: dict[str, Any] | None = None,
) -> run_settings.Resolved:
    """The resolver, called with exactly the arguments a run's claim passes.
    There is no other call site that assembles these."""
    config = inputs.config or {}
    return run_settings.resolve(
        kind=kind,
        run_routes=config.get("run_routes"),
        presets_by_id=inputs.presets_by_id,
        org_default=inputs.org_default,
        legacy_model=(config.get("model_routes") or {}).get(kind),
        # US-66.1: the agent's own pin for this kind, if it set one.
        agent_model_override=(config.get("model_overrides") or {}).get(kind),
        supervisor_override=supervisor_override,
        manager_override=manager_override,
        # us-116.7: the floor.
        org_default_model=inputs.org_default_model,
    )


def claimed_kinds(config: dict[str, Any] | None) -> list[str]:
    """The kinds this agent claims, in `ROUTE_KINDS` order.

    `enabled_kinds` is `null` for a never-saved agent — us-53.4 makes that
    unrestricted, not benched — so `null` claims every kind. `[]` claims none.
    """
    kinds = (config or {}).get("enabled_kinds")
    order = route_kinds()
    if kinds is None:
        return list(order)
    if not isinstance(kinds, list):
        return list(order)
    wanted = {str(k) for k in kinds}
    return [k for k in order if k in wanted]


def session_kind_order(config: dict[str, Any] | None) -> list[str]:
    """Which kinds a session tries, and in what order: `code` first when the
    agent claims it (the closest thing a free-form conversation has to a
    kind), then the rest of what it claims in `ROUTE_KINDS` order — stable and
    explainable rather than dictionary order."""
    kinds = claimed_kinds(config)
    if "code" in kinds:
        return ["code", *[k for k in kinds if k != "code"]]
    return kinds


@dataclass
class SessionModel:
    """What a session resolved: the model, the kind it resolved through, and
    the resolver's record for that kind — or, when nothing resolved, the kinds
    that were tried."""

    model: str | None
    kind: str | None
    resolved: run_settings.Resolved | None
    tried: list[str]


def resolve_session(inputs: ResolverInputs) -> SessionModel:
    """A session resolves like a run, for a kind the agent actually claims.
    The first claimed kind (in `session_kind_order`) that yields a model wins."""
    tried: list[str] = []
    for kind in session_kind_order(inputs.config):
        tried.append(kind)
        resolved = resolve_for_kind(inputs, kind)
        model = str(resolved.values.get("model") or "").strip()
        if model:
            return SessionModel(model=model, kind=kind, resolved=resolved, tried=tried)
    return SessionModel(model=None, kind=None, resolved=None, tried=tried)


def roles_of(kinds: list[str]) -> list[str]:
    """The distinct role names for a list of kinds, in role order."""
    seen: list[str] = []
    for kind in kinds:
        role = ROLE_OF_KIND.get(kind, kind)
        if role not in seen:
            seen.append(role)
    return seen


# us-116.7: the third place a model can come from, named in every refusal.
DEFAULT_PROVIDER_HINT = (
    "or set a default model on the org's default LLM provider "
    "(Settings → LLM providers)"
)


def no_model_refusal(agent_name: str, tried: list[str], org_default: dict[str, Any] | None) -> str:
    """The sentence a manager reads when nothing resolved. It names the agent,
    the roles actually tried, and every place a model can be set — and it says
    plainly when the org's default preset carries no model, so the manager is
    not left assuming the tier will cover it. us-116.7: with the org's default
    provider model as the resolver's floor, reaching this sentence means the
    org has no default provider model either, so it is named as the third
    place."""
    roles = roles_of(tried)
    claims = ", ".join(roles) if roles else "no role"
    who = agent_name or "This agent"
    if not tried:
        head = f"{who} claims no roles, so no model can be resolved for it."
    else:
        head = f"{who} has no model for any of the roles it claims ({claims})."
    preset = (org_default or {}).get("name")
    if org_default is None:
        tail = (
            "Set one under Model per role on its settings page, give the org "
            f"a default preset with a model, {DEFAULT_PROVIDER_HINT}."
        )
    elif not (org_default.get("model") or "").strip():
        tail = (
            "Set one under Model per role on its settings page, give the org's "
            f"default preset ({preset}) a model — it has none today — "
            f"{DEFAULT_PROVIDER_HINT}."
        )
    else:
        tail = f"Set one under Model per role on its settings page, {DEFAULT_PROVIDER_HINT}."
    return f"{head} {tail}"
