"""Run-setting presets (US-32.5).

A preset is a named bundle of how a run should be executed — effort, fallback
chain, permission mode, turn and time ceilings, standing instructions, and a
model. It carries nothing about *who* runs it: project grants and module choice
are properties of the agent and its assignment, not of how a run is executed.

The validation lives here rather than in the router because two callers need it:
the preset endpoints, and the re-seed that copies a superadmin's template edit
into an org's own rows.
"""

from __future__ import annotations

from typing import Any

# The settings a preset may carry. `model` is a column of its own (it has to be
# validated against the org's providers), and `mcp` is not a tunable — a module
# either takes an MCP config or it does not (us-31.9 / us-32.4).
#
# US-47.1 removed `permission_mode`. It was the docstring's own warning made
# flesh: offered on three surfaces, resolved through all three layers, stored on
# every run, and overridden by the machine default before it reached the CLI.
# Measured against the real CLI, only `bypassPermissions` lets a headless run
# call an MCP tool, so the runner now states that itself and there is nothing
# left to choose between.
PRESET_SETTINGS = (
    "fallback_model",
    "effort",
    "max_turns",
    # US-33.2: minutes this KIND of run may take. It narrows the per-agent lease
    # us-31.2 owns and can never widen it — a plan run and a code run plainly
    # deserve different allowances, which is the one part of time us-31.2 left
    # to a preset.
    "max_minutes",
    "standing_instructions",
)
# US-53.1 removed `auth` (added by us-52.1). Billing is not a run setting:
# whose money an agent spends is a property of the agent, decided once —
# `runner_config.claude_billing` — not per preset. Migration 197 stripped the
# stored values; a payload still sending `auth` is refused as unknown above.

# US-32.10: the same five `claude --help` lists, and the same five the Claude
# module declares. A test asserts the two lists are equal — they are written
# down in two repositories' worth of files and drifting apart is what capped
# every preset at `high` while the CLI had taken `xhigh` and `max` all along.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

MAX_NAME = 60
MAX_DESCRIPTION = 400
MAX_INSTRUCTIONS = 4000


class PresetInvalid(Exception):
    """A refusal that names the field and what would have been accepted."""


def clean_settings(raw: Any) -> dict[str, Any]:
    """Validate a preset's settings bundle, or refuse it by name.

    Refusing an unknown key rather than dropping it is the us-32.3 lesson: a
    stored setting nothing reads is a control that appears to work.
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PresetInvalid("settings must be an object")

    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in PRESET_SETTINGS:
            raise PresetInvalid(
                f"'{key}' is not a run setting — a preset carries: "
                + ", ".join(PRESET_SETTINGS)
            )
        if value is None or value == "":
            continue  # unset is a legitimate value; it means "inherit"

        if key == "effort":
            if value not in EFFORTS:
                raise PresetInvalid(
                    f"effort must be one of: {', '.join(EFFORTS)} (got '{value}')"
                )
        elif key == "max_turns":
            value = _positive_int(key, value, ceiling=500)
        elif key == "max_minutes":
            value = _positive_int(key, value, ceiling=1440)
        elif key == "fallback_model":
            value = str(value).strip()[:200]
            if not value:
                continue
        elif key == "standing_instructions":
            value = str(value).strip()[:MAX_INSTRUCTIONS]
            if not value:
                continue
        out[key] = value
    return out


def _positive_int(key: str, value: Any, ceiling: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise PresetInvalid(f"{key} must be a whole number")
    if not 1 <= n <= ceiling:
        raise PresetInvalid(f"{key} must be between 1 and {ceiling} (got {n})")
    return n


def _positive_number(key: str, value: Any, ceiling: float) -> float:
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise PresetInvalid(f"{key} must be a number")
    if not 0 < n <= ceiling:
        raise PresetInvalid(
            f"{key} must be greater than 0 and at most {ceiling} (got {n})"
        )
    return round(n, 2)


def clean_tool_grants(raw: Any, catalog_ids: set[str] | None = None) -> list[str]:
    """US-34.3: the catalog entries this preset grants.

    A preset REFERENCES entries; it never carries connection details, so the
    catalog can move a server without every preset being edited. An id that is
    not in the catalog is refused here — flagged at save time, naming the entry,
    rather than becoming a tool that silently is not there.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PresetInvalid("tool grants must be a list of server ids")
    out: list[str] = []
    for value in raw[:40]:
        server_id = str(value).strip()
        if not server_id:
            continue
        if catalog_ids is not None and server_id not in catalog_ids:
            raise PresetInvalid(
                f"'{server_id}' is not an MCP server in this org's catalog — "
                "register it first, or remove it from this preset"
            )
        if server_id not in out:
            out.append(server_id)
    return out


def clean_name(raw: Any) -> str:
    name = str(raw or "").strip()
    if not name:
        raise PresetInvalid("a preset needs a name")
    if len(name) > MAX_NAME:
        raise PresetInvalid(f"a name may be at most {MAX_NAME} characters")
    return name


def validate_model(model: Any, providers: list[dict[str, Any]]) -> str | None:
    """A set model must be one this org's providers actually offer.

    us-27.8 made the model the thing that resolves a call's provider, so a model
    no provider lists does not route anywhere — and a preset naming one is a
    run that fails ninety seconds in, on a remote machine, over a model id that
    looks perfectly real.
    """
    value = str(model or "").strip()
    if not value:
        return None  # inherit the org default, as the route table already does
    offered: set[str] = set()
    names: list[str] = []
    for p in providers:
        names.append(str(p.get("name") or p.get("provider_type") or "a provider"))
        offered.update(p.get("models") or [])
        if p.get("default_model"):
            offered.add(str(p["default_model"]))
    if value in offered:
        return value
    if not providers:
        raise PresetInvalid(
            f"'{value}' cannot be checked — this org has no LLM providers "
            "configured yet. Add one under Settings → LLM providers first."
        )
    raise PresetInvalid(
        f"no provider in this org offers '{value}'. Checked: "
        + ", ".join(sorted(names))
        + ". The model is what decides which provider answers, so one that is "
        "not listed routes nowhere."
    )


def unsupported_settings(
    settings: dict[str, Any], module_support: dict[str, set[str]]
) -> list[str]:
    """US-32.5 + us-32.4: settings no enabled module can express.

    A warning, not a refusal: a preset may legitimately be written for an agent
    that will run Claude while another agent in the org runs Grok. But the
    manager hears about it at configuration time rather than mid-run.

    `module_support` maps a module name to the settings it declared.
    """
    if not module_support:
        return []
    warnings: list[str] = []
    for key in sorted(settings):
        holders = [m for m, s in module_support.items() if key in s]
        if holders:
            continue
        warnings.append(
            f"no enabled module can be told '{key}' — "
            + ", ".join(f"{m} cannot" for m in sorted(module_support))
        )
    return warnings


def reseed_diff(
    preset: dict[str, Any], template: dict[str, Any]
) -> list[dict[str, Any]]:
    """What re-seeding this preset from its template would change.

    A template edit must never silently rewrite how every org's agents run, so
    the org is offered the update as an action that states its effect first.
    """
    changes: list[dict[str, Any]] = []
    current = preset.get("settings") or {}
    incoming = template.get("settings") or {}
    for key in sorted(set(current) | set(incoming)):
        before, after = current.get(key), incoming.get(key)
        if before != after:
            changes.append({"setting": key, "from": before, "to": after})
    if (preset.get("description") or "") != (template.get("description") or ""):
        changes.append(
            {
                "setting": "description",
                "from": preset.get("description"),
                "to": template.get("description"),
            }
        )
    return changes
