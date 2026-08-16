"""US-100.5: the refresh run proposes whole files, and the manager decides
the pass whole.

The two tools are exercised as plain coroutines with the worker context set
and the db layer stubbed — `test_factory_mcp.py` is `needs_db` and skips in
Essential, so without these the reshape would have no coverage in the suite
anyone runs after a change.

What these pin, in the story's words:
  AC1  the proposal covers the whole published set, keyed by file; a file
       identical to what the project holds is refused
  AC2  recommend_guideline_change proposes the document, no section_key
  AC3  accepting writes the factory's text and publishes nothing (SQL pin)
  AC4  the run kind and its file keep their names; the default text speaks
       files
  AC5  the disabling refusals are gone (see also
       test_instruction_publish_status.py)
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest

from app import factory_mcp
from app.factory_mcp import _current_worker

REPO = Path(__file__).resolve().parents[3]
MIGRATIONS = REPO / "infra" / "supabase" / "migrations"

WORKER = {"id": "w-1", "org_id": "org-1", "name": "Ada"}
RUN = {
    "id": "run-1",
    "kind": "guidelines",
    "worker_id": "w-1",
    "org_id": "org-1",
    "project_id": "11111111-1111-1111-1111-111111111111",
    "input_context": {"scope": "all"},
}
CURRENT = {
    "project_name": "Demo",
    "agent_instructions": "# Conventions\n\nUse tabs.",
    "instructions": {"code": "Build it.", "plan": "Think first.", "test": ""},
}


@pytest.fixture(autouse=True)
def as_worker(monkeypatch):
    # us-110.1: no MCP scope contextvar any more — a no-claim read defaults to
    # the worker's project when it has exactly one granted.
    monkeypatch.setattr(
        "app.factory_mcp.db.sole_granted_project",
        lambda s, w: RUN["project_id"],
    )
    tok = _current_worker.set(dict(WORKER))
    yield
    _current_worker.reset(tok)


@pytest.fixture
def stub_db(monkeypatch, settings_override):
    recorded: dict = {}

    monkeypatch.setattr("app.factory_mcp.get_settings", lambda: settings_override)
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, rid, org: dict(RUN)
    )
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_instruction_files",
        lambda s, pid: dict(CURRENT),
    )

    def fake_record(settings, worker, run, summary, files):
        recorded["summary"] = summary
        recorded["files"] = files
        return {"ok": True, "refresh_id": "r-1", "files": len(files)}

    monkeypatch.setattr("app.factory_mcp.db.record_guidelines_refresh", fake_record)
    monkeypatch.setattr(
        "app.factory_mcp.db.complete_run", lambda *a, **k: True
    )
    monkeypatch.setattr(
        "app.routers.worker._store_handback_notes", lambda *a, **k: None
    )
    return recorded


def _submit(files, summary="Read the repo; two files need work.", run_id="run-1"):
    return asyncio.run(
        factory_mcp.submit_guidelines_refresh(run_id, summary, files)
    )


# --- AC1: whole files, keyed by file --------------------------------------


def test_a_full_pass_records_one_row_per_file(stub_db):
    out = _submit(
        [
            {
                "key": "agents",
                "proposed_text": "# Conventions\n\nUse spaces.",
                "rationale": "The repo uses spaces (see .editorconfig).",
                "severity": "major",
            },
            {
                "key": "code",
                "proposed_text": "Build with `npm run build`.",
                "rationale": "The current text names no command.",
            },
        ]
    )
    assert "error" not in out, out
    assert out["ok"] is True
    assert out["files_proposed"] == ["AGENTS.md", ".buildmill/Code.md"]
    keys = [f["key"] for f in stub_db["files"]]
    assert keys == ["agents", "code"]
    assert stub_db["files"][0]["path"] == "AGENTS.md"
    assert stub_db["files"][1]["path"] == ".buildmill/Code.md"
    assert stub_db["files"][1]["severity"] == "minor"  # the default


def test_a_file_may_be_named_by_path_or_kind(stub_db):
    out = _submit(
        [
            {"key": "AGENTS.md", "proposed_text": "new doc", "rationale": "why"},
            {"key": ".buildmill/Plan.md", "proposed_text": "new plan", "rationale": "why"},
        ]
    )
    assert "error" not in out, out
    assert [f["key"] for f in stub_db["files"]] == ["agents", "plan"]


def test_a_section_key_names_no_file_and_is_refused(stub_db):
    out = _submit(
        [{"key": "deployment", "proposed_text": "x", "rationale": "y"}]
    )
    assert "error" in out
    assert "names no file" in out["error"]
    assert "agents (AGENTS.md)" in out["hint"]
    assert "files" not in stub_db


def test_an_identical_file_is_refused_not_stored(stub_db):
    """AC1: 'a proposal identical to what is already there is refused rather
    than creating an empty one'."""
    out = _submit(
        [
            {
                "key": "code",
                "proposed_text": "Build it.",
                "rationale": "no change really",
            }
        ]
    )
    assert "error" in out
    assert "identical" in out["error"]
    assert "files" not in stub_db


def test_the_same_file_twice_is_refused(stub_db):
    out = _submit(
        [
            {"key": "code", "proposed_text": "a", "rationale": "r"},
            {"key": ".buildmill/Code.md", "proposed_text": "b", "rationale": "r"},
        ]
    )
    assert "error" in out
    assert "second time" in out["error"]


def test_nothing_to_propose_is_still_an_answer(stub_db):
    out = _submit([])
    assert "error" not in out, out
    assert out["files_proposed"] == []
    assert stub_db["files"] == []


def test_a_document_only_scope_refuses_per_task_files(stub_db, monkeypatch):
    run = dict(RUN, input_context={"scope": "document"})
    monkeypatch.setattr(
        "app.factory_mcp.db.get_worker_run", lambda s, rid, org: run
    )
    out = _submit([{"key": "code", "proposed_text": "x", "rationale": "y"}])
    assert "error" in out
    assert "document only" in out["error"]
    ok = _submit(
        [{"key": "agents", "proposed_text": "x", "rationale": "y"}]
    )
    assert "error" not in ok, ok


def test_a_file_needs_its_rationale(stub_db):
    out = _submit([{"key": "test", "proposed_text": "Run pytest."}])
    assert "error" in out
    assert "rationale" in out["error"]


def test_the_old_sections_parameter_is_gone():
    params = inspect.signature(factory_mcp.submit_guidelines_refresh).parameters
    assert "sections" not in params
    assert "files" in params


# --- AC2: the recommendation proposes the document ------------------------


def test_recommend_guideline_change_has_no_section_key():
    params = inspect.signature(factory_mcp.recommend_guideline_change).parameters
    assert "section_key" not in params


def test_a_recommendation_records_the_document(monkeypatch, settings_override):
    recorded = {}
    monkeypatch.setattr("app.factory_mcp.get_settings", lambda: settings_override)
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md",
        lambda s, pid, org: {"name": "Demo", "guidelines": "old doc"},
    )

    def fake_record(settings, worker, org_id, project_id, severity, text, rationale):
        recorded.update(severity=severity, text=text, rationale=rationale)
        return {"id": "rec-1", "duplicate": False}

    monkeypatch.setattr(
        "app.factory_mcp.db.record_guideline_recommendation", fake_record
    )
    out = asyncio.run(
        factory_mcp.recommend_guideline_change("new doc", "old one is stale", "minor")
    )
    assert "error" not in out, out
    assert recorded == {"severity": "minor", "text": "new doc", "rationale": "old one is stale"}
    assert "AGENTS.md" in out["markdown"]


def test_a_recommendation_identical_to_the_document_is_refused(
    monkeypatch, settings_override
):
    monkeypatch.setattr("app.factory_mcp.get_settings", lambda: settings_override)
    monkeypatch.setattr(
        "app.factory_mcp.db.get_project_guidelines_md",
        lambda s, pid, org: {"name": "Demo", "guidelines": "same"},
    )
    out = asyncio.run(
        factory_mcp.recommend_guideline_change("same", "why", "minor")
    )
    assert "error" in out and "identical" in out["error"]


# --- AC3: accept writes the factory's text and publishes nothing ------------


M268 = (MIGRATIONS / "268_the_refresh_proposes_files.sql").read_text(encoding="utf-8")


def test_accepting_writes_the_document_or_the_kind_row():
    assert "if rec.section_key = 'agents' then" in M268
    assert "set agent_instructions = rec.proposed_text" in M268
    assert "update public.worker_instructions" in M268
    assert "insert into public.worker_instructions" in M268


def test_accepting_publishes_nothing():
    """A refresh must not put a commit in the repository on its own — the
    accept path touches the factory's tables only; publish is the manager's
    button (us-99.4)."""
    body = M268.split("-- 2 ", 1)[1]  # past the header prose
    for forbidden in ("instructions_synced", "sync_instruction", "github", "commit"):
        assert forbidden not in body.lower(), forbidden


def test_the_pass_is_decided_whole_by_one_rpc():
    assert "create or replace function public.decide_guidelines_refresh(" in M268
    # every pending row, one verdict, through the single per-row write path
    assert "perform public.decide_guideline_recommendation(v_rec.id, p_accept, p_note)" in M268
    assert "grant execute on function public.decide_guidelines_refresh" in M268


def test_legacy_section_rows_stay_decidable():
    assert "rec.section_id is not null" in M268
    assert "update public.project_guidelines" in M268


# --- AC4: the kind keeps its name and file; the default speaks files -------


def test_the_kind_and_its_file_are_unchanged():
    from app.instruction_files import KIND_FILES

    assert KIND_FILES["guidelines"] == "Guidelines_Refresh.md"


M269 = (
    MIGRATIONS / "269_the_refresh_and_release_defaults_speak_files.sql"
).read_text(encoding="utf-8")


def test_the_default_text_describes_proposing_files():
    assert "submit_guidelines_refresh" in M269
    assert "propose whole files" in M269
    assert "section_key" not in M269.split("new_guidelines text :=")[1].split("new_release")[0]


def test_the_release_default_tells_the_agent_to_propose_the_version():
    """us-100.6's uncorrected gap: the default still said the version is
    never chosen by the agent."""
    release = M269.split("new_release text :=")[1].split("guarded text[]")[0]
    assert "proposed_version" in release
    assert "version_rationale" in release
    assert "Agent Instructions" in release
    assert "never chosen by you" not in release


def test_the_splice_guards_every_other_kind():
    assert "before_hash is distinct from after_hash" in M269
    assert "raise exception" in M269
    for kind in ("code", "plan", "merge", "deploy", "test"):
        assert f"'{kind}'" in M269.split("guarded text[] :=")[1].split("];")[0]


def test_the_backfill_touches_only_untouched_rows():
    assert "content = old_guidelines" in M269
    assert "content = old_release" in M269
    for verb in ("drop column", "drop table", "delete from"):
        assert verb not in M269.lower()


# --- the context is live -----------------------------------------------------


def test_the_brief_reads_the_files_live_not_from_the_snapshot():
    src = inspect.getsource(factory_mcp)
    branch = src.split('if run["kind"] == "guidelines":\n        # us-100.5')[1].split("return _next")[0]
    assert "db.get_project_instruction_files" in branch
    assert "current_guidelines" not in branch


def test_dispatch_no_longer_snapshots_the_files():
    from app import db

    src = inspect.getsource(db.dispatch_guidelines_refresh)
    assert "current_guidelines" not in src
    assert '"document"' in src  # the new scope


# --- found on live: the pass is decided by a manager, past the SELECT-only RLS


M271 = (
    MIGRATIONS / "271_the_pass_is_decided_by_a_manager.sql"
).read_text(encoding="utf-8")


def test_deciding_the_pass_runs_as_definer_with_an_explicit_capability_check():
    """guideline_refreshes has only a SELECT policy (the bundle was always
    closed by a definer trigger), so an INVOKER decide saw no row under
    FOR UPDATE and answered 'refresh not found' on live. Definer, with the
    authorization stated in the body, is the fix — and the check must be
    there, or definer would let any member decide any org's pass."""
    body = M271.split("create or replace function public.decide_guidelines_refresh(", 1)[1]
    head = body.split("$$", 1)[0]
    assert "security definer" in head
    assert "set search_path = public" in head
    assert "has_org_capability(v_ref.org_id, 'manage_project')" in M271
    assert "revoke all on function public.decide_guidelines_refresh" in M271
