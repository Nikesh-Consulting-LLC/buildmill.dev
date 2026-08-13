"""US-43: the guidelines refresh — its migrations, its submit contract, and
the catalog the two halves of the app have to agree on.

Same posture as the rest of the suite: no live DB, so migration mechanisms are
pinned textually (behaviour was verified against both Supabase projects when
they were applied) and the MCP tool is exercised through the real server with
the db layer stubbed.
"""

from pathlib import Path

from app.factory_mcp import _CATALOG_SECTION_KEYS

ROOT = Path(__file__).resolve().parents[2].parent
MIGRATIONS = ROOT / "infra" / "supabase" / "migrations"
M171 = (MIGRATIONS / "171_guidelines_refresh.sql").read_text(encoding="utf-8")
M172 = (MIGRATIONS / "172_guidelines_run_never_held.sql").read_text(encoding="utf-8")
M173 = (MIGRATIONS / "173_guideline_refresh_settles.sql").read_text(encoding="utf-8")


# --------------------------------------------------------------- 171: the bundle


def test_one_open_refresh_per_project_is_an_index_not_a_check():
    # A check-then-insert races; the partial unique index is what makes
    # "one open refresh" a promise rather than a hope.
    assert "create unique index" in M171
    assert "guideline_refreshes_one_open_idx" in M171
    assert "where status = 'pending'" in M171


def test_refresh_id_is_nullable_so_us_5_32_rows_are_untouched():
    assert "add column if not exists refresh_id uuid" in M171
    assert "not null" not in M171.split("add column if not exists refresh_id")[1][:120]


def test_guidelines_kind_is_added_to_both_constraints():
    assert "'deploy', 'guidelines'" in M171
    assert "runs_kind_check" in M171
    assert "worker_instructions_run_kind_check" in M171


def test_the_issue_scoping_check_is_left_alone():
    # A guidelines run always carries its chore, so it is NOT one of the
    # project-scoped kinds. Touching runs_issue_or_project_scoped here would
    # quietly let a guidelines run exist with no work item.
    assert "runs_issue_or_project_scoped" not in M171


def test_the_instruction_is_surgery_not_a_rewrite():
    # 131 replaced the release case and 136 rewrote the docs-tree sentences,
    # both in place. Retyping the function from 114 reverts them — the
    # 095/105/106 lesson, which this migration nearly repeated.
    assert "pg_get_functiondef" in M171 or "prosrc" in M171
    assert "raise exception" in M171
    # No wholesale `create or replace ... when 'prd' then` retype.
    assert "when 'prd' then" not in M171


def test_the_instruction_requires_the_deployment_section():
    assert "ALWAYS propose the Deployment and Release section" in M171
    assert "submit_guidelines_refresh" in M171
    # Apostrophes are doubled ONCE inside the dollar-quoted branch. Doubling
    # them twice renders "project''s" in the instruction the agent reads.
    assert "''''" not in M171


# ---------------------------------------------------- 172: never held by the queue


def test_the_exemption_is_the_first_rule():
    body = M172.split("begin", 1)[1]
    exemption = body.index("v_run.kind = 'guidelines'")
    # Ahead of the us-15.3 sibling rule and both build-mode blocks.
    assert exemption < body.index("sib.status = 'draft'")
    assert exemption < body.index("v_mode = 'feature'")
    assert exemption < body.index("v_mode = 'epic'")


def test_the_rebuild_carries_every_existing_rule_forward():
    # Rebuilt from 129's body verbatim: all four hold reasons still present.
    for phrase in (
        "still being curated",
        "waiting on an earlier feature to finish",
        "still need plan approval",
        "ahead of this one is still running",
    ):
        assert phrase in M172


def test_the_exemption_names_exactly_one_kind():
    # Not a general priority concept — one kind, spelled out.
    assert M172.count("= 'guidelines'") == 1


# --------------------------------------------------------- 173: the refresh settles


def test_settling_is_a_trigger_not_a_second_write_path():
    assert "after update of status on public.guideline_recommendations" in M173
    # decide_guideline_recommendation is NOT redefined here.
    assert "function public.decide_guideline_recommendation" not in M173
    assert "insert into public.project_guidelines" not in M173


def test_it_only_closes_when_nothing_is_pending():
    assert "where refresh_id = new.refresh_id and status = 'pending'" in M173
    assert "status = 'decided'" in M173
    assert "set status = 'done'" in M173


def test_a_still_pending_row_closes_nothing():
    assert "or new.status = 'pending'" in M173
    assert "or old.status <> 'pending'" in M173


# ------------------------------------------------------------------- the catalog


def test_api_catalog_matches_the_web_catalog():
    """The API validates section_key against a copy of the web app's catalog
    (it does not read the web source). This is the test that keeps the two
    honest — a key added on one side and not the other means an agent's
    proposal is refused for naming a section the manager can see."""
    ts = (
        ROOT / "apps" / "web" / "src" / "lib" / "project-guidelines-catalog.ts"
    ).read_text(encoding="utf-8")
    web_keys = set()
    for line in ts.splitlines():
        stripped = line.strip()
        if stripped.startswith("key:") and stripped.endswith(","):
            web_keys.add(stripped.split('"')[1])
    assert web_keys, "no catalog keys parsed from the web catalog"
    assert web_keys == _CATALOG_SECTION_KEYS


def test_the_deployment_section_is_in_the_catalog():
    # US-43.4: a catalog entry, not a custom section — so it carries guidance,
    # can be re-proposed against by key, and can be required of the pass.
    assert "deployment" in _CATALOG_SECTION_KEYS


# --------------------------------------------- the two defects found on a live DB
#
# Both shipped to production. Neither could be caught by this suite as written:
# the SQL never runs here, so a column that does not exist and an RPC that
# writes the wrong key both look fine. These pin the fixes textually; the
# behaviour was verified by running the real code against a live database.

M177 = (
    MIGRATIONS / "177_accept_keeps_the_catalog_key.sql"
).read_text(encoding="utf-8")

DB_PY = (
    ROOT / "apps" / "api" / "app" / "db.py"
).read_text(encoding="utf-8")


def _guidelines_refresh_source() -> str:
    """Just the US-43 block of db.py — the SQL this feature owns."""
    start = DB_PY.index("# US-43: guidelines refresh")
    end = DB_PY.index("def decide_learning_submission")
    return DB_PY[start:end]


def test_no_sql_here_names_a_column_issues_does_not_have():
    """`issues` has no `description` column — the story body is `body`. The
    first live click returned a 500: `column "description" of relation
    "issues" does not exist`, and because FastAPI's unhandled-exception path
    attaches no CORS headers the browser reported it as a CORS failure.

    US-43.6 deleted the INSERT that carried the bug, but the digest still
    reads the same table, so the guard stays."""
    src = _guidelines_refresh_source()
    assert "description" not in src


def test_the_work_item_digest_reads_body():
    src = _guidelines_refresh_source()
    assert "i.body" in src
    assert 'r["body"]' in src


def test_accepting_keeps_the_proposed_catalog_key():
    """US-43.4 wants Deployment and Release to be catalog key `deployment`,
    not a custom section — otherwise the next refresh finds no section with
    that key and proposes a second one. 069 hardcoded 'custom' because it
    predates catalog-keyed proposals."""
    assert "coalesce(nullif(rec.section_key, ''), 'custom')" in M177
    assert "'custom'," not in M177.split("values")[1].split("coalesce")[0]


def test_the_unkeyed_path_still_becomes_custom():
    # us-5.32's ad-hoc new-section proposals carry an empty section_key and
    # must keep behaving exactly as they did.
    assert "nullif(rec.section_key, '')" in M177
    assert "'custom'" in M177


# ------------------------------------------- the gate that made it unclaimable

# (Migration 178's mapping was retired by 199 — see the tests below.)


def test_the_mapping_is_retired_with_the_matrix():
    """US-55.1 rewrote the gate: a worker_capabilities row means project
    ACCESS, kinds are gated by the agent's own enabled_kinds checkboxes
    (which carry guidelines/elaborate/wireframe first-class), and the
    kind→capability squeeze that made migration 178 necessary is gone —
    from the API and the database both."""
    import app.db as app_db_module

    assert not hasattr(app_db_module, "run_kind_capability")

    m199 = (
        MIGRATIONS / "199_project_access_not_a_matrix.sql"
    ).read_text(encoding="utf-8")
    assert "drop function if exists public.run_kind_capability" in m199
    assert "create or replace function public.worker_has_grant" in m199
    assert "enabled_kinds" in m199
    # A null capability still means "any access on this project" — the clone
    # gate is unchanged.
    assert "p_capability is null" in m199


def test_the_web_eligibility_surface_dropped_the_mapping_too():
    """us-35.5: two surfaces disagreeing about who can work is the defect it
    was written to remove. The dashboard now mirrors worker_has_grant's two
    halves (access + enabled_kinds) and carries no kind mapping of its own —
    the client-side literal is exactly what drifted (it missed wireframe)."""
    ts = (
        ROOT
        / "apps"
        / "web"
        / "src"
        / "app"
        / "(app)"
        / "dashboard"
        / "data.ts"
    ).read_text(encoding="utf-8")
    assert "RUN_KIND_CAPABILITY" not in ts
    assert "accessByWorker" in ts
    assert "enabledKindsByWorker" in ts


# ------------------------------------------- US-43.6: one shot, not a pipeline

M179 = (
    MIGRATIONS / "179_guidelines_run_is_project_scoped.sql"
).read_text(encoding="utf-8")


def test_the_kind_may_exist_without_a_work_item():
    """The whole fix. Modelled as a chore, the run moved a work item to
    `in-review` at hand-back — a WAITING status — so one decision produced two
    Things to Do entries, the second leading to a code-review gate over a
    branch and a pull request that never existed. With no issue there is no
    status to set and that gate is unreachable."""
    assert (
        "check (issue_id is not null or kind in ('release', 'deploy', "
        "'guidelines'))" in M179
    )


def test_the_other_project_scoped_kinds_survive_the_widening():
    block = M179.split("add constraint runs_issue_or_project_scoped")[1]
    assert "'release'" in block and "'deploy'" in block


def test_dispatch_creates_no_work_item():
    src = _guidelines_refresh_source()
    # No issue insert anywhere in the US-43 block.
    assert "insert into public.issues" not in src
    # And the run is explicitly project-scoped.
    assert "'claude', 'queued', 'guidelines'" in src
    assert "values (%s, %s, null," in src


def test_dispatch_returns_no_issue_id():
    src = _guidelines_refresh_source()
    dispatch = src.split("def dispatch_guidelines_refresh")[1].split("\ndef ")[0]
    tail = dispatch.split("return {")[-1]
    assert "refresh_id" in tail and "run_id" in tail
    assert "issue_id" not in tail


def test_the_scope_and_focus_still_reach_the_agent():
    # They used to ride the chore's body. With no chore they must be in the
    # run's own context or the agent is dispatched with no instruction.
    src = _guidelines_refresh_source()
    assert '"scope_instruction": scope_instruction' in src
    assert '"focus": focus' in src


def test_completing_the_run_touches_no_issue_status():
    complete = DB_PY.split("def complete_run")[1].split("\ndef ")[0]
    branch = complete.split('elif kind == "guidelines":')[1].split("elif")[0]
    code = "\n".join(
        line for line in branch.splitlines() if not line.strip().startswith("#")
    )
    assert "issue_status = None" in code
    assert "in-review" not in code


def test_the_no_active_epic_refusal_is_gone():
    # It existed only because assign_issue_number (074) raises for a project
    # with no active epic. No chore, no refusal — one fewer thing that can
    # stop a manager fixing their guidelines.
    src = _guidelines_refresh_source()
    assert "has no active epic" not in src


# ----------------------------------- a dead run must not strand its refresh

M180 = (
    MIGRATIONS / "180_a_dead_run_settles_its_refresh.sql"
).read_text(encoding="utf-8")


def test_a_dead_run_settles_a_refresh_that_received_nothing():
    """A refresh only left `pending` when its last recommendation was decided
    (173). A run that failed left it pending forever with zero proposals —
    which permanently blocked the project, because only one refresh may be
    open at a time, and rendered as a spinner that never resolved."""
    assert "status in ('failed', 'cancelled', 'stopped')" in M180
    assert "set status = 'decided'" in M180


def test_it_leaves_a_refresh_that_did_receive_proposals_alone():
    # The run did its job; the manager still owns the decision whatever
    # happened to the run afterwards.
    assert "not exists (" in M180
    assert "from public.guideline_recommendations x" in M180


def test_succeeded_is_deliberately_not_handled():
    # A successful run's refresh SHOULD stay pending — that is what "waiting
    # on the manager" means, and 173 closes it when the review is done.
    body = M180.split("create or replace function")[1]
    assert "'succeeded'" not in body


def test_it_is_a_trigger_on_runs_not_a_branch_in_complete_run():
    # A run reaches a terminal state through the MCP submit, the HTTP submit
    # with an error, cancel_run, the lease sweep and the orphan reaper. Only
    # the database sees all of them.
    assert "after update of status on public.runs" in M180


def test_it_backfills_the_already_stranded():
    tail = M180.rsplit("create trigger", 1)[1]
    assert "update public.guideline_refreshes" in tail


# ------------------------------- US-43.8: the brief a guidelines run receives

MCP_PY = (
    ROOT / "apps" / "api" / "app" / "factory_mcp.py"
).read_text(encoding="utf-8")


def _work_context_source() -> str:
    start = MCP_PY.index("async def get_work_context")
    return MCP_PY[start : MCP_PY.index("@mcp.tool", start)]


def test_guidelines_has_its_own_branch():
    """Without it the run fell through to the generic work-item brief and got
    no instruction, no guidelines and no digest — so it compared the repo
    against AGENTS.md, a file generated FROM the guidelines, and could only
    ever find agreement."""
    src = _work_context_source()
    assert 'if run["kind"] == "guidelines":' in src
    # It returns its own brief rather than falling through.
    branch = src.split('if run["kind"] == "guidelines":')[1]
    assert "return _next" in branch.split('if run["kind"] ==')[0]


def test_the_brief_carries_what_the_run_needs():
    src = _work_context_source()
    branch = src.split('if run["kind"] == "guidelines":')[1].split("return _next")[0]
    for needed in ("template", "current_guidelines", "work_items",
                   "scope_instruction", "focus"):
        assert needed in branch, needed


def test_the_brief_names_the_subject_and_warns_off_the_generated_file():
    # The circular check that produced "nothing to propose": AGENTS.md is
    # generated FROM these sections, so reading it back can only agree.
    src = _work_context_source()
    assert "AGENTS.md" in src
    assert "submit_guidelines_refresh" in src


def test_an_empty_section_is_shown_as_needing_writing():
    # A placeholder that is present and blank is a different instruction from
    # a section that does not exist — omitting it loses that.
    assert "_empty — needs writing_" in _work_context_source()


def test_list_run_documents_guards_a_run_with_no_work_item():
    """The function that actually raised. The branch above makes the brief
    right; this makes the crash impossible for the next project-scoped kind."""
    src = DB_PY.split("def list_run_documents")[1].split("\ndef ")[0]
    assert "_valid_uuid" in src
    assert "return []" in src


def test_the_crash_was_in_the_prologue_so_the_guard_is_the_real_fix():
    """`list_run_documents` is called BEFORE any kind branch, so no amount of
    branch ordering avoids it — `release` and `deploy` are project-scoped too
    and would have raised there identically, having simply never been
    exercised. That is why the guard lives in the function that raised."""
    src = _work_context_source()
    prologue = src.split('if run["kind"] ==')[0]
    assert "db.list_run_documents" in prologue
