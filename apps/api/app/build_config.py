"""US-7.9: write-only build/test config for coding runs.

Names live in public.project_build_config (readable by org members under RLS);
values are secrets in the private `data` bucket, written and read only by the
api service role. Values are delivered exclusively into a claimed CODE run of
the owning project and are masked in any run logs — never returned to the
browser, never to PRD/plan runs, never to another project.
"""

from __future__ import annotations

from . import db, storage
from .config import Settings


async def fetch_build_config_values(
    settings: Settings, org_id: str, project_id: str
) -> dict[str, str]:
    """Resolve the project's build config (names from the table, values from
    the data bucket). Empty when nothing is set."""
    names = db.list_build_config_names(settings, project_id)
    if not names:
        return {}
    prefix = storage.build_config_prefix(org_id, project_id)
    out: dict[str, str] = {}
    for name in names:
        value = await storage.get_object(settings, f"{prefix}/{name}")
        if value is not None:
            out[name] = value.decode("utf-8", errors="replace")
    return out
