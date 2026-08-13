"""Apply a migration file to the live Supabase project.

Usage: python infra/supabase/apply.py infra/supabase/migrations/006_reviews.sql
Reads the DB connection string from the DATABASE_URL environment variable
(falls back to apps/api/.env).
"""

import os
import sys
from pathlib import Path

import psycopg


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    env_file = Path(__file__).resolve().parents[2] / "apps" / "api" / ".env"
    for line in env_file.read_text().splitlines():
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("DATABASE_URL not set (env or apps/api/.env)")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    sql = Path(sys.argv[1]).read_text(encoding="utf-8")
    with psycopg.connect(database_url()) as conn:
        conn.execute(sql)
        conn.commit()
    print(f"applied: {sys.argv[1]}")


if __name__ == "__main__":
    main()
