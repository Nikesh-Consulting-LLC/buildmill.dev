"""Every dispatchable run kind is one the runner will actually claim — US-48.2.

This is the third time the same bug has shipped. `test`, `release` and
`deploy` were refused until us-43.7; `guidelines` and `elaborate` were refused
until migration 178 fixed the *factory* side and us-43.7 fixed the runner's;
and `wireframe` was refused again here, because us-43.7 left
`CLIModule.capabilities` a CLOSED set (`set(HANDBACK_SHAPE)`) even though it
made the prompt builder kind-agnostic. `workloop._module_can_take` then
refuses anything outside it with "the claude module does not do 'X' work".

The failure mode is the worst kind: nothing errors. The run is created, sits
`queued`, and the manager sees an agent that will not pick it up.

So this test does not check a list against another list a human maintains — it
checks the runner against the **database's own `runs_kind_check`**, which is
what actually decides whether a kind can be dispatched.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
RUNNER = REPO / "apps" / "runner"


def _handback_shape_keys() -> set[str]:
    """The kinds the claude module declares, read from the source.

    Parsed rather than imported: apps/runner is a separate program with its
    own dependencies, and this suite must not need them installed to protect
    the contract between the two halves."""
    src = (RUNNER / "supervisor" / "modules" / "cli_base.py").read_text(
        encoding="utf-8"
    )
    block = src.split("HANDBACK_SHAPE: dict[str, str] = {", 1)[1].split("}", 1)[0]
    return set(re.findall(r'"([a-z_]+)":', block))


def _db_run_kinds() -> set[str]:
    """The kinds `runs.kind` actually allows, from the newest migration that
    rewrites the constraint."""
    migrations = sorted((REPO / "infra" / "supabase" / "migrations").glob("*.sql"))
    newest = None
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        if "add constraint runs_kind_check" in text:
            newest = text
    assert newest, "no migration defines runs_kind_check"
    block = newest.split("add constraint runs_kind_check", 1)[1].split(")", 1)[0]
    return set(re.findall(r"'([a-z_]+)'", block))


def test_the_runner_claims_every_kind_the_database_can_dispatch():
    db_kinds = _db_run_kinds()
    runner_kinds = _handback_shape_keys()
    missing = db_kinds - runner_kinds
    assert not missing, (
        f"the runner refuses {sorted(missing)} — `capabilities` is "
        "`set(HANDBACK_SHAPE)` and `workloop._module_can_take` rejects "
        "anything outside it, so a run of this kind is created, sits queued, "
        "and no agent ever claims it. Add the kind to HANDBACK_SHAPE in "
        "apps/runner/supervisor/modules/cli_base.py with the shape its "
        "hand-back actually uses (mcp unless it writes files or a worktree)."
    )


def test_the_runner_declares_no_kind_the_database_rejects():
    """The other direction: a kind the runner would take but nothing can
    dispatch is dead weight, and usually a rename nobody finished."""
    extra = _handback_shape_keys() - _db_run_kinds()
    assert not extra, f"the runner declares kinds the database does not allow: {sorted(extra)}"


def test_wireframe_hands_back_over_mcp():
    """A wireframe run writes no file into the checkout and commits nothing —
    it submits with submit_wireframe. Told to leave a worktree for the harness
    to commit, it would fail later and more confusingly than a refusal."""
    src = (RUNNER / "supervisor" / "modules" / "cli_base.py").read_text(
        encoding="utf-8"
    )
    block = src.split("HANDBACK_SHAPE: dict[str, str] = {", 1)[1].split("}", 1)[0]
    match = re.search(r'"wireframe":\s*(\w+)', block)
    assert match, "wireframe is not in HANDBACK_SHAPE"
    assert match.group(1) == "HANDBACK_MCP"
