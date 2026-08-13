"""2026-08-13: a spawn that fails must close its audit row, not raise past it.

Two runs' terminal failures were invisible for days because
create_subprocess_exec raised before the report hook fired — the audit rows
sat with exit_code null and empty output, and the only account of the failure
was the agent's own (wrong) diagnosis.
"""

import asyncio

from supervisor.primitives import LocalPrimitives


def test_spawn_failure_reports_127_and_closes_the_audit():
    reported = []

    async def audit(argv, cwd):
        return True, "aud-1"

    async def report(audit_id, exit_code, output):
        reported.append((audit_id, exit_code, output))

    prim = LocalPrimitives(audit=audit, report=report)
    result = asyncio.run(
        prim.run_shell(["/nonexistent/binary-that-cannot-exist-xyz"])
    )

    assert result.exit_code == 127
    assert "[spawn failed:" in result.stdout
    assert len(reported) == 1
    audit_id, exit_code, output = reported[0]
    assert audit_id == "aud-1"
    assert exit_code == 127
    assert "[spawn failed:" in output
