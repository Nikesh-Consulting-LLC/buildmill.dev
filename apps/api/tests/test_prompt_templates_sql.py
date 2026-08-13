"""US-5.17: live SQL coverage — override chains for worker-instruction and
guideline-section defaults, the client RPC's scoping, and the buildmill
seed honoring the superadmin's text.

Runs against DATABASE_URL (apps/api/.env); skips if unreachable.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row


def _database_url() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path(__file__).resolve().parents[1] / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("DATABASE_URL="):
                return line.split("=", 1)[1].strip().strip('"')
    return None


@pytest.fixture(scope="module")
def db():
    url = _database_url()
    if not url:
        pytest.skip("DATABASE_URL not set")
    try:
        conn = psycopg.connect(url, row_factory=dict_row)
        conn.execute("select 1")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"database unreachable: {e}")
    yield conn
    conn.close()


@pytest.fixture
def override(db):
    """Insert an override row for the duration of one test."""
    inserted: list[str] = []

    def _set(key: str, content: str):
        db.execute(
            "insert into public.llm_prompt_templates (prompt_key, content) "
            "values (%s, %s) on conflict (prompt_key) do update "
            "set content = excluded.content",
            (key, content),
        )
        db.commit()
        inserted.append(key)

    yield _set
    db.rollback()
    db.execute(
        "delete from public.llm_prompt_templates where prompt_key = any(%s)",
        (inserted,),
    )
    db.commit()


def _scalar(db, sql, *params):
    return db.execute(sql, params).fetchone()["v"]


def test_default_worker_instruction_honors_override(db, override):
    baked = _scalar(db, "select public.baked_worker_instruction('plan') as v")
    assert _scalar(db, "select public.default_worker_instruction('plan') as v") == baked

    override("worker_instruction/plan", "SUPERADMIN PLAN TEXT")
    assert (
        _scalar(db, "select public.default_worker_instruction('plan') as v")
        == "SUPERADMIN PLAN TEXT"
    )
    # blank override = factory default
    override("worker_instruction/plan", "   ")
    assert _scalar(db, "select public.default_worker_instruction('plan') as v") == baked


def test_effective_guideline_section_honors_override(db, override):
    baked = _scalar(db, "select public.baked_guideline_section('commands') as v")
    assert (
        _scalar(db, "select public.effective_guideline_section('commands') as v")
        == baked
    )
    override("guideline_section/commands", "- Custom: `make ship`")
    assert (
        _scalar(db, "select public.effective_guideline_section('commands') as v")
        == "- Custom: `make ship`"
    )


def test_guideline_defaults_rpc_returns_only_guideline_rows(db, override):
    override("learnings_merge", "THINKING OVERRIDE — must not leak")
    override("worker_instruction/code", "WORKER OVERRIDE — must not leak")
    rows = db.execute(
        "select section_key, content from public.guideline_section_defaults()"
    ).fetchall()
    assert len(rows) == 18
    keys = {r["section_key"] for r in rows}
    assert "buildmill-workflow" in keys and "commands" in keys
    joined = " ".join(r["content"] for r in rows)
    assert "must not leak" not in joined


def test_help_overrides_rpc_returns_only_help_rows(db, override):
    """US-2.30: the /help read path exposes help/* overrides and nothing
    else from the default-deny table."""
    override("help/pipeline/build", "HELP OVERRIDE TEXT")
    override("learnings_merge", "THINKING OVERRIDE — must not leak")
    override("worker_instruction/code", "WORKER OVERRIDE — must not leak")
    override("help/status/draft", "   ")  # blank = factory default: omitted

    rows = db.execute(
        "select prompt_key, content from public.help_content_overrides()"
    ).fetchall()
    keys = {r["prompt_key"] for r in rows}
    assert "help/pipeline/build" in keys
    assert "help/status/draft" not in keys
    assert all(k.startswith("help/") for k in keys)
    joined = " ".join(r["content"] for r in rows)
    assert "must not leak" not in joined


def test_help_overrides_rpc_role_grants(db):
    """US-2.30: everyone signed in reads (authenticated), anon does not."""
    with db.transaction():
        db.execute("set local role authenticated")
        db.execute("select * from public.help_content_overrides()").fetchall()

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with db.transaction():
            db.execute("set local role anon")
            db.execute("select * from public.help_content_overrides()")


def test_buildmill_seed_honors_override(db, override):
    override("guideline_section/buildmill-workflow", "OVERRIDDEN BUILD MILL DOC")
    org = db.execute(
        "select id from public.organizations order by created_at limit 1"
    ).fetchone()
    if not org:
        pytest.skip("no organization")
    row = db.execute(
        "insert into public.projects (org_id, name, repo_full_name) "
        "values (%s, %s, 'acme/tmpl-seed-test') returning id",
        (org["id"], f"tmpl-test {uuid.uuid4().hex[:6]}"),
    ).fetchone()
    db.commit()
    try:
        seeded = db.execute(
            "select content from public.project_guidelines "
            "where project_id = %s and section_key = 'buildmill-workflow'",
            (row["id"],),
        ).fetchone()
        # US-75.1: precedence changed under this test. Since org project
        # templates landed (migration 227), seed_buildmill_guidelines_section
        # prefers the org template's own buildmill-workflow section and only
        # falls back to effective_guideline_section (which is what reads the
        # override) when the template has none. Org customization beating the
        # platform default is the intended order, so assert the order rather
        # than one branch of it — and say which branch ran.
        template_section = db.execute(
            """
            select s.content
            from public.projects p
            join public.org_project_template_sections s
              on s.org_template_id = p.org_template_id
             and s.section_type = 'guideline'
             and s.section_key = 'buildmill-workflow'
            where p.id = %s
            """,
            (row["id"],),
        ).fetchone()
        if template_section:
            assert seeded["content"] == template_section["content"], (
                "the org template's section must win over the platform default"
            )
        else:
            assert seeded["content"] == "OVERRIDDEN BUILD MILL DOC"
        # Either way the override must be reachable — that is the read path
        # this test is named for.
        effective = db.execute(
            "select public.effective_guideline_section('buildmill-workflow') as c"
        ).fetchone()["c"]
        assert effective == "OVERRIDDEN BUILD MILL DOC"
        # us-5.14 seeding also flows through the (un-overridden) defaults
        wi = db.execute(
            "select content from public.worker_instructions "
            "where project_id = %s and run_kind = 'prd'",
            (row["id"],),
        ).fetchone()
        assert wi["content"].strip()
    finally:
        db.rollback()
        db.execute("delete from public.projects where id = %s", (row["id"],))
        db.commit()
