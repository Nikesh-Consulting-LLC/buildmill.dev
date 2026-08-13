"""Quick connectivity/state check against the live DB (reads apps/api/.env)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply import database_url  # noqa: E402

import psycopg  # noqa: E402

with psycopg.connect(database_url(), connect_timeout=10) as conn:
    user = conn.execute("select current_user").fetchone()[0]
    pubs = conn.execute(
        "select tablename from pg_publication_tables where pubname = 'supabase_realtime'"
    ).fetchall()
    print("connected as:", user)
    print("realtime tables:", [p[0] for p in pubs])
