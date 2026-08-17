"""us-110.1 — an agent's projects are the ones you checked.

The Add agent wizard asked which projects an agent may use twice: a
single-select MCP scope (`workers.project_id`) and a multi-select access list
(`worker_capabilities`). They were written by two calls that never read each
other, and the helper text under each contradicted the other. The scope won,
so an agent created with two projects checked silently never claimed the
second's runs.

The scope is gone. These are the structural guards that keep it gone — no
database, no network, so they run in the Essential suite. The behaviour at the
data layer (two granted projects both offered, a revoked grant offering
nothing, the sole-grant default) is pinned in test_worker_pool_sql.py.
"""

import inspect
from pathlib import Path

import pytest

from app import db as app_db
from app import factory_mcp

SRC = Path(__file__).resolve().parents[1] / "app"


def _text(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# --------------------------------------- the scope is gone, and stays gone


def test_no_second_project_filter_survives_in_the_listings():
    """Each of these took a project_id that narrowed the capability filter's
    answer. A second filter is how a granted project's runs went missing."""
    for fn, expected in (
        (app_db.list_worker_pool, ["settings", "worker"]),
        (app_db.list_worker_runs, ["settings", "worker"]),
        (app_db.list_factory_queue, ["settings", "org_id"]),
    ):
        assert list(inspect.signature(fn).parameters) == expected, fn.__name__


def test_the_scope_contextvar_and_its_claim_guard_are_gone():
    assert not hasattr(factory_mcp, "_scoped_project")
    # claim_work's out-of-scope refusal was the only caller.
    assert not hasattr(app_db, "run_in_project")


def test_the_pool_sql_has_no_project_narrowing_left():
    """Belt and braces on the signature check: the clause itself is gone from
    the query, so a caller cannot reintroduce it by passing a parameter."""
    src = inspect.getsource(app_db.list_worker_pool)
    assert "%(project)s" not in src
    # ...while the fail-closed capability predicate — the one real filter —
    # is still there doing the job.
    assert "worker_has_grant" in src


def test_no_refusal_recommends_the_retired_mcp_url():
    """`/mcp/<org-shortname>/<project-slug>` 404s — migration 216 superseded
    it — yet ten refusals still told agents to connect that way. A refusal
    naming an impossible cure is worse than one naming none."""
    text = _text("factory_mcp.py")
    assert "<project-slug>" not in text
    assert "project-scoped MCP url" not in text


# ------------------------------------------- what replaces the scope's job


def test_the_no_project_hint_points_at_something_that_exists():
    hint = factory_mcp._NO_PROJECT_HINT
    assert "list_available_work" in hint
    assert "project_id" in hint


def test_default_project_is_the_sole_grant(monkeypatch):
    monkeypatch.setattr(
        factory_mcp.db, "sole_granted_project", lambda s, w: "proj-1"
    )
    assert factory_mcp._default_project({"id": "w-1"}) == "proj-1"


def test_default_project_refuses_to_guess_between_several(monkeypatch):
    """None is the point: guessing would answer about the wrong project
    silently, which is the failure the retired scope was built out of."""
    monkeypatch.setattr(
        factory_mcp.db, "sole_granted_project", lambda s, w: None
    )
    assert factory_mcp._default_project({"id": "w-1"}) is None


@pytest.mark.parametrize(
    "tool",
    [
        "get_project_guidelines",
        "get_project_learnings",
        "list_project_documents",
        "submit_learning",
        "get_project_tree",
        "read_project_file",
        "get_project_workspace",
    ],
)
def test_every_no_claim_read_still_takes_an_explicit_project_id(tool):
    """With no scope, the explicit argument is the only way a multi-project
    worker addresses a project — so none of these may lose it."""
    fn = getattr(factory_mcp, tool)
    assert "project_id" in inspect.signature(fn).parameters, tool


# ------------------------------------------------ the migration itself

MIGRATION = (
    Path(__file__).resolve().parents[3]
    / "infra"
    / "supabase"
    / "migrations"
    / "279_an_agents_projects_are_its_grants.sql"
)


def test_the_migration_drops_the_column_and_its_setter():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    assert "drop column if exists project_id" in sql
    assert "drop function if exists public.set_worker_project" in sql
    # create_worker comes back four-argument; nothing may recreate the fifth.
    assert "p_project" not in sql.split("as $$")[0].split("create or replace")[-1]
