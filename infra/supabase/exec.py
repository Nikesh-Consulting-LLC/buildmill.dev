"""Run an ad-hoc SQL statement against the live DB (reads apps/api/.env).

Usage: python infra/supabase/exec.py "select count(*) from public.tasks"
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apply import database_url  # noqa: E402

import psycopg  # noqa: E402
from psycopg.rows import dict_row  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        cur = conn.execute(sys.argv[1])
        if cur.description:
            print(json.dumps(cur.fetchall(), default=str, indent=1))
        else:
            print(f"rows affected: {cur.rowcount}")
        conn.commit()


if __name__ == "__main__":
    main()
