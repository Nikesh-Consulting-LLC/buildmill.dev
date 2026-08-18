"""us-119.1: a database call never holds the event loop.

`db.py` is synchronous psycopg. Called directly from an `async def`, one of
its round trips freezes uvicorn's single event loop for its whole duration —
and the runner-facing handlers make ~80,000 such calls a day on prod, so
every other request in the process (a page's capability check, a CORS
preflight, a response being written) waited behind them: `/spend` at 1.9 s
for a sub-millisecond query, `/worker/pool` at 5.8 s.

The fix is mechanical — `await asyncio.to_thread(db.fn, ...)` — and the
danger is that it creeps back one call at a time, because a direct call is
the natural thing to type and nothing fails when you do. So the rule is
asserted here, over the source, for the files that were converted. Extend
`COVERED` as more files are; never shrink it.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"

# The files whose async handlers must not touch the database on the loop.
# us-119.1 converted the runner-facing surface; the manager-facing routers
# (mcp_catalog, presets, admin, agent_sessions, ...) are the named follow-up.
COVERED = (
    "routers/worker.py",
    "routers/runner_socket.py",
    "reconcile.py",
)

# Sync dependencies FastAPI itself runs on its threadpool: a plain `def`
# dependency never executes on the loop, so it may call db directly.
FASTAPI_THREADPOOLED = {"verify_worker"}


def _touches_db(node: ast.AST) -> bool:
    for c in ast.walk(node):
        if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute):
            if isinstance(c.func.value, ast.Name) and c.func.value.id == "db":
                return True
            if c.func.attr == "_connect":
                return True
    return False


def violations(source: str, label: str) -> list[str]:
    """Every database call made on the event loop in `source`, as
    `label:line name(...)`. A call counts when it is a direct `db.<fn>(...)`
    or a call to a same-module sync helper that opens a connection, its
    nearest enclosing function is an `async def`, and it is not the first
    argument of an `asyncio.to_thread(...)`."""
    tree = ast.parse(source)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    # A same-module sync helper that opens a connection is a database call
    # by another name — calling it from a coroutine blocks the loop just the
    # same, so it must be wrapped too.
    sync_db_helpers = {
        n.name
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and _touches_db(n)
        and n.name not in FASTAPI_THREADPOOLED
    }

    def enclosing_function(node: ast.AST):
        p = parents.get(node)
        while p is not None:
            if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                return p
            p = parents.get(p)
        return None

    def wrapped_in_to_thread(node: ast.AST) -> bool:
        p = parents.get(node)
        while p is not None:
            if (
                isinstance(p, ast.Call)
                and isinstance(p.func, ast.Attribute)
                and p.func.attr == "to_thread"
            ):
                return True
            p = parents.get(p)
        return False

    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id == "db":
            name = f"db.{f.attr}"
        elif isinstance(f, ast.Name) and f.id in sync_db_helpers:
            name = f.id
        else:
            continue
        if not isinstance(enclosing_function(node), ast.AsyncFunctionDef):
            continue
        if wrapped_in_to_thread(node):
            continue
        out.append(f"{label}:{node.lineno} {name}(...) on the event loop")
    return out


def test_the_covered_files_never_call_the_database_on_the_loop():
    found: list[str] = []
    for rel in COVERED:
        p = APP / rel
        assert p.exists(), f"{rel} is in COVERED but does not exist — did it move?"
        found += violations(p.read_text(encoding="utf-8"), rel)
    assert not found, (
        "direct database call(s) inside async def — wrap each in "
        "`await asyncio.to_thread(...)` (us-119.1):\n  " + "\n  ".join(found)
    )


def test_the_guard_still_sees_a_violation():
    """A guard that matches nothing guards nothing: feed it the pattern it
    exists to catch and make sure it fires — for a direct db call, for a
    same-module helper that opens a connection — and stays quiet for the
    wrapped form, for a sync helper's own body, and for a threadpooled
    dependency."""
    sample = (
        "import asyncio\n"
        "from . import db\n"
        "def helper(settings):\n"
        "    with db._connect(settings) as conn:\n"
        "        return conn.execute('select 1').fetchone()\n"
        "def verify_worker(settings):\n"
        "    return db.get_worker_by_token(settings, 't')\n"
        "async def bad(settings):\n"
        "    a = db.get_worker(settings, 'x')\n"
        "    b = helper(settings)\n"
        "    return a, b\n"
        "async def good(settings):\n"
        "    a = await asyncio.to_thread(db.get_worker, settings, 'x')\n"
        "    b = await asyncio.to_thread(helper, settings)\n"
        "    c = verify_worker(settings)\n"
        "    return a, b, c\n"
    )
    found = violations(sample, "sample.py")
    assert found == [
        "sample.py:9 db.get_worker(...) on the event loop",
        "sample.py:10 helper(...) on the event loop",
    ], found
