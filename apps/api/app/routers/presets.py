"""Run-setting presets: the org's own rows, and the platform's templates
(US-32.5).

Reading presets goes straight to Supabase under RLS from the browser, as
everything else does. Every WRITE is here, because a preset is the one place a
model id is chosen away from the run that uses it: since us-27.8 the model is
what resolves a call's provider, so a preset naming a model the org does not
offer routes nowhere — and that must be refused at Save, not discovered ninety
seconds into a run on a remote machine.

Org preset writes are gated on `manage_work`, the same capability that
configures an agent. Template writes are platform-admin only.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import db, presets as presets_lib
from ..auth import AuthUser, verify_token
from ..config import Settings, get_settings
from ..supabase import RpcError, rpc
from .admin import require_platform_admin

router = APIRouter(tags=["presets"])


async def _require_manage_work(
    org_id: str, user: AuthUser, settings: Settings
) -> None:
    try:
        ok = await rpc(
            settings,
            user.token,
            "has_org_capability",
            {"p_org": org_id, "p_capability": "manage_work"},
        )
    except RpcError:
        ok = False
    if not ok:
        raise HTTPException(
            status_code=403, detail="Not authorized to manage presets"
        )


async def _require_platform_admin_for_preset_write(
    user: AuthUser, settings: Settings
) -> None:
    """US-57.6: presets are platform-authored now — an org's own presets
    stay readable and usable (a run_route can still point at one), but
    creating, editing, deleting or re-seeding one is the superadmin's."""
    await require_platform_admin(user, settings)


def _validated(
    settings: Settings,
    org_id: str,
    *,
    name: Any = None,
    description: Any = None,
    model: Any = None,
    raw_settings: Any = None,
    raw_grants: Any = None,
) -> dict[str, Any]:
    """Shared validation for create and update. Raises 422 naming the field."""
    try:
        out: dict[str, Any] = {}
        if name is not None:
            out["name"] = presets_lib.clean_name(name)
        if description is not None:
            out["description"] = str(description).strip()[
                : presets_lib.MAX_DESCRIPTION
            ]
        if raw_settings is not None:
            out["settings"] = presets_lib.clean_settings(raw_settings)
        if model is not None:
            providers, _routes = db.get_org_llm_config(settings, org_id)
            out["model"] = presets_lib.validate_model(model, providers)
        if raw_grants is not None:
            # US-34.3: a preset naming a removed entry is flagged at SAVE time,
            # naming the entry, rather than becoming a tool that is not there.
            catalog = {
                str(row["id"]) for row in db.list_mcp_servers(settings, org_id)
            }
            out["tool_grants"] = presets_lib.clean_tool_grants(raw_grants, catalog)
    except presets_lib.PresetInvalid as e:
        raise HTTPException(status_code=422, detail=str(e))
    return out


def _warnings(
    settings: Settings, org_id: str, preset_settings: dict[str, Any]
) -> list[str]:
    """US-32.5: a preset naming a setting no enabled module supports is flagged
    on save — naming the setting and the modules — using us-32.4's
    declarations. A warning rather than a refusal: an org may legitimately run
    Claude on one agent and Grok on another."""
    try:
        support = db.org_module_support(settings, org_id)
    except Exception:  # noqa: BLE001 — a missing warning never blocks a save
        return []
    return presets_lib.unsupported_settings(preset_settings, support)


def _shape(row: dict[str, Any], warnings: list[str] | None = None) -> dict[str, Any]:
    out = dict(row)
    out["id"] = str(out["id"])
    out["org_id"] = str(out["org_id"])
    if warnings:
        out["warnings"] = warnings
    return out


# ---------------------------------------------------------------- org presets


class PresetCreate(BaseModel):
    name: str
    description: str = ""
    model: str | None = None
    settings: dict[str, Any] = {}


class PresetUpdate(BaseModel):
    # US-34.3: catalog entry ids this preset grants. Empty means the factory
    # server only — default deny.
    tool_grants: list[str] | None = None
    name: str | None = None
    description: str | None = None
    # An explicit null means "inherit the org default"; omitting the field
    # leaves the model alone. Pydantic cannot tell those apart on its own, so
    # the caller says which it meant.
    model: str | None = None
    clear_model: bool = False
    settings: dict[str, Any] | None = None


@router.post("/orgs/{org_id}/presets")
async def create_preset(
    org_id: str,
    body: PresetCreate,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_platform_admin_for_preset_write(user, settings)
    clean = _validated(
        settings,
        org_id,
        name=body.name,
        description=body.description,
        model=body.model if body.model is not None else "",
        raw_settings=body.settings,
    )
    existing = {p["name"] for p in db.list_presets(settings, org_id, True)}
    if clean["name"] in existing:
        raise HTTPException(
            status_code=409,
            detail=f"this org already has a preset called '{clean['name']}'",
        )
    row = db.create_preset(
        settings,
        org_id,
        name=clean["name"],
        description=clean["description"],
        model=clean["model"],
        preset_settings=clean["settings"],
    )
    return _shape(row, _warnings(settings, org_id, clean["settings"]))


@router.patch("/presets/{preset_id}")
async def patch_preset(
    preset_id: str,
    body: PresetUpdate,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    preset = db.get_preset(settings, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="preset not found")
    org_id = str(preset["org_id"])
    await _require_platform_admin_for_preset_write(user, settings)

    clean = _validated(
        settings,
        org_id,
        name=body.name,
        description=body.description,
        model=body.model,
        raw_settings=body.settings,
        raw_grants=body.tool_grants,
    )
    if body.name and clean.get("name") != preset["name"]:
        taken = {
            p["name"]
            for p in db.list_presets(settings, org_id, True)
            if str(p["id"]) != preset_id
        }
        if clean["name"] in taken:
            raise HTTPException(
                status_code=409,
                detail=f"this org already has a preset called '{clean['name']}'",
            )
    row = db.update_preset(
        settings,
        preset_id,
        name=clean.get("name"),
        description=clean.get("description"),
        model=clean.get("model"),
        clear_model=body.clear_model,
        preset_settings=clean.get("settings"),
        tool_grants=clean.get("tool_grants"),
    )
    if not row:
        raise HTTPException(status_code=404, detail="preset not found")
    return _shape(row, _warnings(settings, org_id, row["settings"] or {}))


@router.delete("/presets/{preset_id}")
async def delete_preset(
    preset_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Archived, not deleted — a finished run names the preset it ran under."""
    preset = db.get_preset(settings, preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="preset not found")
    await _require_platform_admin_for_preset_write(user, settings)
    archived = db.archive_preset(settings, preset_id)
    return {"archived": archived, "name": preset["name"]}


# ------------------------------------------------------------------ re-seeding


@router.get("/orgs/{org_id}/presets/reseed")
async def preview_reseed(
    org_id: str,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """What accepting the platform's current templates would change here.

    Offered rather than applied: silently changing how every org's agents run
    because a template was edited is the failure this avoids.
    """
    await _require_manage_work(org_id, user, settings)
    templates = {t["key"]: t for t in db.list_preset_templates(settings)}
    out = []
    for preset in db.list_presets(settings, org_id):
        key = preset.get("template_key")
        template = templates.get(key) if key else None
        if not template:
            continue
        if (preset.get("seeded_version") or 0) >= template["version"] and not (
            presets_lib.reseed_diff(preset, template)
        ):
            continue
        out.append(
            {
                "preset_id": str(preset["id"]),
                "name": preset["name"],
                "template_key": key,
                "seeded_version": preset.get("seeded_version"),
                "template_version": template["version"],
                "changes": presets_lib.reseed_diff(preset, template),
            }
        )
    # A template this org has no copy of at all is also an update to offer.
    have = {p.get("template_key") for p in db.list_presets(settings, org_id, True)}
    missing = [
        {"template_key": k, "name": t["name"], "changes": [], "new": True}
        for k, t in templates.items()
        if k not in have
    ]
    return {"presets": out, "missing": missing}


class ReseedBody(BaseModel):
    template_keys: list[str] | None = None


@router.post("/orgs/{org_id}/presets/reseed")
async def apply_reseed(
    org_id: str,
    body: ReseedBody,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    await _require_platform_admin_for_preset_write(user, settings)
    keys = body.template_keys or [t["key"] for t in db.list_preset_templates(settings)]
    updated = db.apply_reseed(settings, org_id, keys)
    return {
        "updated": [
            {"id": str(r["id"]), "name": r["name"], "version": r["version"]}
            for r in updated
        ]
    }


# ---------------------------------------------------------- platform templates


class TemplateUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    model_hint: str | None = None
    settings: dict[str, Any] | None = None


@router.get("/admin/preset-templates")
async def list_templates(
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    return {"templates": [dict(t) | {"id": str(t["id"])} for t in db.list_preset_templates(settings)]}


@router.patch("/admin/preset-templates/{key}")
async def patch_template(
    key: str,
    body: TemplateUpdate,
    admin: AuthUser = Depends(require_platform_admin),
    settings: Settings = Depends(get_settings),
):
    """Edit a template. Every org keeps running exactly as it was until it
    accepts the change — see the re-seed endpoints above."""
    if not db.get_preset_template(settings, key):
        raise HTTPException(status_code=404, detail="template not found")
    cleaned = None
    if body.settings is not None:
        try:
            cleaned = presets_lib.clean_settings(body.settings)
        except presets_lib.PresetInvalid as e:
            raise HTTPException(status_code=422, detail=str(e))
    row = db.update_preset_template(
        settings,
        key,
        name=presets_lib.clean_name(body.name) if body.name else None,
        description=(
            str(body.description).strip()[: presets_lib.MAX_DESCRIPTION]
            if body.description is not None
            else None
        ),
        model_hint=(
            str(body.model_hint).strip()[: presets_lib.MAX_DESCRIPTION]
            if body.model_hint is not None
            else None
        ),
        template_settings=cleaned,
        updated_by=admin.id,
    )
    return dict(row) | {"id": str(row["id"])}


# ---------------------------------------------------------------------------
# US-33.6: how each preset actually performed
# ---------------------------------------------------------------------------


@router.get("/orgs/{org_id}/presets/outcomes")
async def preset_outcomes(
    org_id: str,
    days: int = 90,
    user: AuthUser = Depends(verify_token),
    settings: Settings = Depends(get_settings),
):
    """Grouped by preset name AND version — which is why us-32.5 versions them.

    "Deep got worse last week" is only answerable if the two versions are
    separate rows rather than one average that hides the change.
    """
    try:
        member = await rpc(settings, user.token, "is_org_member", {"org": org_id})
    except RpcError:
        member = False
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this org")
    return {"outcomes": db.preset_outcomes(settings, org_id, days)}
