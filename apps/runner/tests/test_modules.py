"""US-10.4: module contract, registry, and local primitives."""

import asyncio
import sys

from supervisor import modules
from supervisor.modules.base import RunContext
from supervisor.primitives import LocalPrimitives


def _ctx(kind, **kw):
    return RunContext(run_id="r1", kind=kind, context={"title": "Widget"}, **kw)


def test_registry_autoloads_sim():
    assert "sim" in modules.available()
    assert modules.get("sim") is not None
    assert "code" in modules.get("sim").capabilities


def test_sim_module_each_kind():
    sim = modules.get("sim")

    async def run(kind, **kw):
        return await sim.execute(_ctx(kind, **kw), prim=None)

    code = asyncio.run(run("code", branch_name="factory/issue-1"))
    assert code.outcome == "succeeded"
    assert code.branch_ref == "factory/issue-1"
    assert code.test_cases and code.test_cases[0]["title"].startswith("Verify")

    plan = asyncio.run(run("plan"))
    assert plan.plan and plan.test_plan

    prd = asyncio.run(run("prd"))
    assert "## Acceptance criteria" in prd.prd

    brk = asyncio.run(run("breakdown"))
    assert brk.stories and brk.stories[0]["title"] == "Widget"


def test_local_primitives_run_shell_ok():
    prim = LocalPrimitives()
    res = asyncio.run(
        prim.run_shell([sys.executable, "-c", "print('hello-prim')"])
    )
    assert res.ok
    assert res.exit_code == 0
    assert "hello-prim" in res.stdout


def test_local_primitives_audit_denial_blocks():
    async def deny(argv, cwd):
        return False

    prim = LocalPrimitives(audit=deny)
    res = asyncio.run(prim.run_shell([sys.executable, "-c", "print('should-not-run')"]))
    assert res.allowed is False
    assert res.exit_code == 126
    assert "should-not-run" not in res.stdout


def test_local_primitives_reports_result_with_audit_id():
    reported = {}

    async def audit(argv, cwd):
        return True, "aid-1"  # (allow, audit_id) tuple form

    async def report(audit_id, exit_code, output):
        reported.update(audit_id=audit_id, exit_code=exit_code, output=output)

    prim = LocalPrimitives(audit=audit, report=report)
    res = asyncio.run(prim.run_shell([sys.executable, "-c", "print('reported')"]))
    assert res.ok
    assert reported["audit_id"] == "aid-1"
    assert reported["exit_code"] == 0
    assert "reported" in reported["output"]
