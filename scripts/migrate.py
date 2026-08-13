#!/usr/bin/env python3
"""Migration pipeline — one command for the four-step migration ritual.

  python scripts/migrate.py apply 195_foo.sql   # apply to BOTH DBs, regen types, embed test
  python scripts/migrate.py drift               # compare public function bodies across DBs
  python scripts/migrate.py status              # last applied migration on each DB

Connections come from two env vars (or a git-ignored scripts/.migrate.env with
the same KEY=value lines) — named explicitly so nothing is ever guessed:

  SUPABASE_DB_URL_PROD   postgres://... for Software-Factory (wdudmfhhqxrqzoyhuzwx)
  SUPABASE_DB_URL_DEV    postgres://... for build-mill-dev  (nncquokoblcfcqyajzmk)

Types regen shells out to `npx supabase gen types` when SUPABASE_ACCESS_TOKEN is
set; otherwise it tells you to regenerate via MCP. Run with the api venv python
(apps/api/.venv) so psycopg is available.

Never put credentials in this file or on the command line.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "infra" / "supabase" / "migrations"
TYPES_FILE = REPO / "apps" / "web" / "src" / "lib" / "supabase" / "database.types.ts"
PROD_REF = "wdudmfhhqxrqzoyhuzwx"
ENV_FILE = Path(__file__).resolve().parent / ".migrate.env"

try:
    import psycopg
except ImportError:
    sys.exit("error: psycopg not importable — run with apps/api/.venv/Scripts/python.exe")


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def targets(env: dict[str, str]) -> dict[str, str]:
    out = {}
    for label, key in (("prod", "SUPABASE_DB_URL_PROD"), ("dev", "SUPABASE_DB_URL_DEV")):
        url = env.get(key)
        if not url:
            sys.exit(f"error: {key} not set (env or scripts/.migrate.env) — see --help")
        out[label] = url
    return out


def normalize_sql(body: str) -> str:
    """Strip comments and collapse whitespace so prod/dev bodies compare fairly
    (Supabase's migration path strips SQL comments on apply)."""
    body = re.sub(r"--[^\n]*", "", body)
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.DOTALL)
    return re.sub(r"\s+", " ", body).strip()


def cmd_apply(args: argparse.Namespace) -> int:
    path = MIGRATIONS / args.file
    if not path.exists():
        sys.exit(f"error: {path} not found")
    name = re.sub(r"^\d+_", "", path.stem)
    sql = path.read_text(encoding="utf-8")
    version = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")

    for label, url in targets(load_env()).items():
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "select version from supabase_migrations.schema_migrations where name = %s",
                    (name,))
                row = cur.fetchone()
                if row and not args.force:
                    print(f"{label}: already applied as {row[0]} — skipping (use --force to reapply)")
                    continue
                cur.execute(sql)
                cur.execute(
                    "insert into supabase_migrations.schema_migrations (version, name, statements) "
                    "values (%s, %s, %s)", (version, name, [sql]))
            conn.commit()
            print(f"{label}: applied {path.name} as {version}")

    regen_types()
    return run_embed_test()


def regen_types() -> None:
    token = load_env().get("SUPABASE_ACCESS_TOKEN")
    if not token:
        print("types: SUPABASE_ACCESS_TOKEN not set — regenerate via MCP "
              "generate_typescript_types into apps/web/src/lib/supabase/database.types.ts")
        return
    print("types: regenerating from prod schema ...")
    result = subprocess.run(
        ["npx", "supabase", "gen", "types", "typescript", "--project-id", PROD_REF],
        capture_output=True, text=True, shell=(os.name == "nt"),
        env={**os.environ, "SUPABASE_ACCESS_TOKEN": token})
    if result.returncode != 0:
        print(f"types: FAILED — {result.stderr.strip()[:500]}", file=sys.stderr)
        return
    TYPES_FILE.write_text(result.stdout, encoding="utf-8", newline="\n")
    print(f"types: wrote {TYPES_FILE.relative_to(REPO)}")


def run_embed_test() -> int:
    venv_py = REPO / "apps" / "api" / ".venv" / "Scripts" / "python.exe"
    python = str(venv_py) if venv_py.exists() else sys.executable
    print("embed sweep: pytest tests/test_embed_ambiguity.py ...")
    result = subprocess.run(
        [python, "-m", "pytest", "tests/test_embed_ambiguity.py", "-q"],
        cwd=REPO / "apps" / "api")
    return result.returncode


def fetch_functions(url: str) -> dict[str, str]:
    q = ("select p.oid::regprocedure::text, pg_get_functiondef(p.oid) "
         "from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
         "where n.nspname = 'public' and p.prokind = 'f'")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(q)
        return {sig: normalize_sql(body) for sig, body in cur.fetchall()}


def cmd_drift(_: argparse.Namespace) -> int:
    t = targets(load_env())
    prod, dev = fetch_functions(t["prod"]), fetch_functions(t["dev"])
    problems = []
    for sig in sorted(prod.keys() | dev.keys()):
        if sig not in dev:
            problems.append(f"only on prod: {sig}")
        elif sig not in prod:
            problems.append(f"only on dev:  {sig}")
        elif prod[sig] != dev[sig]:
            problems.append(f"BODY DIFFERS: {sig}")
    if problems:
        print(f"function drift between prod and dev ({len(problems)}):")
        for p in problems:
            print(f"  - {p}")
        print("fix by patching the live body in place (see memory: never rebuild "
              "from an old migration file)")
        return 1
    print(f"no drift: {len(prod)} public functions match")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    for label, url in targets(load_env()).items():
        with psycopg.connect(url) as conn, conn.cursor() as cur:
            cur.execute("select version, name from supabase_migrations.schema_migrations "
                        "order by version desc limit 1")
            row = cur.fetchone()
            print(f"{label}: {row[0]} {row[1]}" if row else f"{label}: (none)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_apply = sub.add_parser("apply", help="apply a migration file to both DBs + regen types + embed test")
    p_apply.add_argument("file", help="file name under infra/supabase/migrations/")
    p_apply.add_argument("--force", action="store_true", help="reapply even if the name is recorded")
    p_apply.set_defaults(func=cmd_apply)

    p_drift = sub.add_parser("drift", help="compare public function bodies across the two DBs")
    p_drift.set_defaults(func=cmd_drift)

    p_status = sub.add_parser("status", help="last applied migration on each DB")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
