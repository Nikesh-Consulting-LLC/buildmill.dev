"""Simulated agent module (US-10.4): drives the full pipeline with no CLI and no
model. It is the default module for tests and the integration harness — the
supervisor→submit path can be proven end-to-end without a real agent.
"""

from __future__ import annotations

from . import register
from .base import Knob, ModuleResult, Primitives, RunContext


class SimModule:
    name = "sim"
    capabilities = {"code", "plan", "prd", "breakdown"}
    # US-31.9: it fabricates results and never opens a checkout, so neither
    # git nor MCP applies to it. Declaring that keeps the MCP requirement from
    # disqualifying the one module whose whole job is proving the pipeline
    # without a real agent.
    needs_repo = False

    # US-32.4: one knob, and it is the proof that adding a module makes it
    # configurable with no frontend change — `sim` grew a field on the settings
    # page by adding these five lines and nothing else. It has no model and no
    # CLI, so `standing_instructions` is the only thing that means anything: it
    # echoes them into its output, which is how a test can see they arrived.
    settings = (
        Knob(
            "standing_instructions",
            kind="text",
            delivery="prompt",
            help="Echoed into the simulated output, so the delivery path can "
            "be proven without a model.",
        ),
    )

    async def execute(self, ctx: RunContext, prim: Primitives) -> ModuleResult:
        title = (ctx.context or {}).get("title", "work item")
        if ctx.kind == "prd":
            return ModuleResult(
                outcome="succeeded",
                stdout=f"[sim] prd {title}",
                prd=(
                    f"## Problem\n\n{title}\n\n## Goals\n\n- Deliver {title}\n\n"
                    "## Out of scope\n\n- n/a\n\n## Acceptance criteria\n\n"
                    f"- {title} works\n"
                ),
            )
        if ctx.kind == "breakdown":
            return ModuleResult(
                outcome="succeeded",
                stdout=f"[sim] breakdown {title}",
                stories=[
                    {
                        "title": title,
                        "body": f"Implement {title}",
                        "acceptance_criteria": [f"{title} works"],
                    }
                ],
            )
        if ctx.kind == "plan":
            return ModuleResult(
                outcome="succeeded",
                stdout=f"[sim] plan {title}",
                plan=f"## Implementation plan\n\nImplement {title}.",
                test_plan="## Test plan\n\nManual verification.",
            )
        # code
        return ModuleResult(
            outcome="succeeded",
            stdout=f"[sim] code {title}",
            branch_ref=ctx.branch_name or "factory/sim",
            test_cases=[
                {
                    "title": f"Verify: {title}",
                    "steps": "1. run it",
                    "expected_result": f"{title} works",
                    "test_types": ["regression"],
                    "environments": ["dev"],
                }
            ],
        )


register(SimModule())
