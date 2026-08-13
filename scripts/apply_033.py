"""Apply migration 033 to the live database (DATABASE_URL from apps/api/.env)."""

from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
env_lines = (ROOT / "apps" / "api" / ".env").read_text().splitlines()
url = next(
    line.split("=", 1)[1].strip().strip('"')
    for line in env_lines
    if line.startswith("DATABASE_URL=")
)
sql = (ROOT / "infra" / "supabase" / "migrations" / "033_phase2_workflow.sql").read_text(
    encoding="utf-8"
)

with psycopg.connect(url) as conn:
    conn.execute(sql)
    conn.commit()
    cols = conn.execute(
        """
        select column_name from information_schema.columns
        where table_schema='public' and table_name='deployments'
          and column_name='environment'
        """
    ).fetchall()
    tables = conn.execute(
        """
        select table_name from information_schema.tables
        where table_schema='public'
          and table_name in ('release_records','release_record_events')
        order by 1
        """
    ).fetchall()
    fn = conn.execute(
        """
        select pg_get_functiondef(p.oid)
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname='public' and p.proname='dispatch_issue'
        """
    ).fetchone()
    print("environment col:", cols)
    print("tables:", tables)
    print("dispatch has plan kind:", fn is not None and "v_kind" in fn[0])
    print("OK")
