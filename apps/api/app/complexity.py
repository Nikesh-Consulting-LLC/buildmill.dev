"""US-7.1: advisory work-item complexity scoring.

A best-effort scorer that never raises to its caller — any failure (no
provider, provider error, unparseable reply, invalid enum) is logged and the
existing values are left untouched. Scored at creation (basis 'story') and
refined after a plan run (basis 'plan'); a 'plan' estimate is never downgraded
by a later story-level call.
"""

from __future__ import annotations

import logging
from typing import Any

from . import db, llm
from .config import Settings

logger = logging.getLogger("uvicorn.error")


def _details(ctx: dict[str, Any]) -> str:
    body = (ctx.get("body") or "").strip()
    criteria = ctx.get("acceptance_criteria") or []
    parts = [body] if body else []
    if criteria:
        parts.append(
            "Acceptance criteria:\n"
            + "\n".join(f"- {c}" for c in criteria)
        )
    return "\n\n".join(parts) or "(no spec)"


async def score_and_store_issue(
    settings: Settings, issue_id: str, *, basis: str | None = None
) -> bool:
    """Score one work item and persist the estimate. `basis` forces 'plan' or
    'story'; None auto-selects 'plan' when a plan artifact exists. Returns True
    if an estimate was written, False otherwise — never raises."""
    try:
        ctx = db.get_issue_scoring_context(settings, issue_id)
        if not ctx:
            return False
        chosen = basis or ("plan" if ctx.get("plan") else "story")
        # Don't spend a call to downgrade a plan-basis estimate.
        if chosen == "story" and ctx.get("complexity_basis") == "plan":
            return False
        result = await llm.score_complexity(
            settings,
            str(ctx["org_id"]),
            basis=chosen,
            item_type=ctx["type"],
            title=ctx["title"],
            details=_details(ctx),
            implementation_plan=ctx.get("plan") or "",
            test_plan=ctx.get("test_plan") or "",
        )
        db.set_issue_complexity(
            settings,
            issue_id,
            complexity=result["complexity"],
            touches_critical=result["touches_critical"],
            data_model_impact=result["data_model_impact"],
            rationale=result["rationale"],
            basis=chosen,
            model=result.get("model"),
        )
        return True
    except llm.LlmNotConfigured:
        logger.info("complexity scoring skipped for %s: no LLM provider", issue_id)
        return False
    except Exception as e:  # noqa: BLE001 — best-effort, never raises
        logger.warning("complexity scoring failed for %s: %s", issue_id, e)
        return False
