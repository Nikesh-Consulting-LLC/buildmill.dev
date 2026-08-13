"""US-5.33: the content audit trail's SQL mechanisms.

No live database rides the test suite, so these tests pin the migration's
load-bearing clauses — the trigger coverage, the append-only guard, the
no-client-write policy posture, and the actor-resolution order — plus the
API-side actor attribution hook in db.upsert_project_learnings. The
behavior itself was verified against the live project when the migration
was applied (worker-attributed rows on update; update/delete rejected).
"""

from pathlib import Path

MIGRATION = (
    Path(__file__).resolve().parents[2].parent
    / "infra"
    / "supabase"
    / "migrations"
    / "067_content_audit.sql"
).read_text(encoding="utf-8")


def test_all_four_surfaces_have_capture_triggers():
    for table, events in (
        ("public.projects", "after insert or update"),
        ("public.project_guidelines", "after insert or update or delete"),
        ("public.project_learnings", "after insert or update or delete"),
        ("public.worker_instructions", "after insert or update or delete"),
    ):
        assert f"{events} on {table}" in MIGRATION, table


def test_append_only_guard_covers_update_and_delete():
    assert "before update or delete on public.content_audit" in MIGRATION
    assert "content_audit is append-only" in MIGRATION


def test_no_client_write_policy():
    # Exactly one policy — SELECT for org members. Any write policy would
    # break the "rows come only from the triggers" invariant.
    assert MIGRATION.count("create policy") == 1
    assert "for select" in MIGRATION
    assert "is_org_member" in MIGRATION


def test_actor_resolution_order_is_user_then_api_then_system():
    # auth.uid() (UI CRUD) wins, the API-declared actor is next, and
    # 'system' is the fallback — in that order (matching on the function
    # body's branch heads, not the header comments).
    uid = MIGRATION.index("if auth.uid() is not null then")
    api = MIGRATION.index(
        "elsif nullif(current_setting('app.audit_actor_name', true), '')"
    )
    system = MIGRATION.index("actor_type := 'system'")
    assert uid < api < system


def test_project_branch_tracks_the_overview_fields():
    for field in (
        "name",
        "description",
        "repo_full_name",
        "default_branch",
        "env_runtime",
        "env_setup_commands",
        "env_notes",
    ):
        assert f"('{field}'" in MIGRATION, field


def test_guideline_reorders_are_captured():
    assert "old.sort_order is distinct from new.sort_order" in MIGRATION
    assert "'position ' || old.sort_order" in MIGRATION


def test_capture_functions_are_security_definer():
    # UI writers run under RLS; the trigger insert must clear
    # content_audit's default-deny via definer rights.
    assert MIGRATION.count("security definer") >= 2


def test_upsert_learnings_declares_the_audit_actor(monkeypatch):
    """US-5.33 API attribution: the service-role learnings write sets the
    app.audit_actor_* config the trigger reads."""
    from app import db

    executed: list[tuple[str, tuple]] = []

    class FakeConn:
        def execute(self, sql, params=None):
            executed.append((" ".join(sql.split()), params))

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "_connect", lambda settings: FakeConn())
    db.upsert_project_learnings(
        None,
        "org-1",
        "project-1",
        "merged text",
        actor={"type": "worker", "id": "w-1", "name": "codey"},
    )
    joined = " | ".join(sql for sql, _ in executed)
    assert "app.audit_actor_type" in joined
    assert "app.audit_actor_name" in joined
    params = [p for _, p in executed if p and "codey" in p]
    assert params, "actor name must ride the set_config call"


def test_upsert_learnings_without_actor_sets_no_config(monkeypatch):
    from app import db

    executed: list[str] = []

    class FakeConn:
        def execute(self, sql, params=None):
            executed.append(" ".join(sql.split()))

        def commit(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(db, "_connect", lambda settings: FakeConn())
    db.upsert_project_learnings(None, "org-1", "project-1", "merged text")
    assert not any("app.audit_actor" in sql for sql in executed)
