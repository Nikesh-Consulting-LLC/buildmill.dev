"""Regenerate `scripts/testing/endpoints.json` from the live FastAPI app.

The Playwright suite is data-driven: every category spec reads this catalog and
asserts the auth boundary of every operation it owns. That only stays honest if
the catalog is derived from the app rather than hand-maintained — a new router
added without a test would otherwise be invisible.

Run it from the repo root, on any machine with the api venv:

    apps/api/.venv/Scripts/python scripts/testing/tools/generate-catalog.py     # Windows
    apps/api/.venv/bin/python scripts/testing/tools/generate-catalog.py         # POSIX

It imports `app.main`, so it needs the two settings with no default
(`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`). Nothing is contacted at import
time, so placeholders are fine and are supplied automatically below when the
environment does not already carry real ones.

What each entry records:

    method, path   the full URL path, `/api/v1/...` prefix included
    tag            the router tag — this is what splits the specs by category
    auth           which credential the operation demands (see AUTH_KINDS)
    op             the endpoint function name, for grepping back to source
    params         path parameters and their JSON-schema type, so a test can
                   synthesize a value that passes validation and reaches the
                   auth check rather than dying at 422
    body           whether a request body is required
    mutating       anything that is not a GET
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from collections import Counter

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
API_DIR = REPO_ROOT / "apps" / "api"
OUT = REPO_ROOT / "scripts" / "testing" / "endpoints.json"

# Import-time settings only; no network call is made by importing the app.
os.environ.setdefault("SUPABASE_URL", "https://catalog.invalid")
os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "catalog-generation-only")

sys.path.insert(0, str(API_DIR))

from fastapi.routing import APIRoute  # noqa: E402

from app.main import app  # noqa: E402


def collect_routes(routes, acc, seen):
    """Walk the route tree.

    FastAPI wraps `include_router` results in `_IncludedRouter`, which is a
    `BaseRoute` with no `.routes` — the actual APIRoutes hang off
    `.original_router`. Missing that returns exactly one route (health) and
    makes the catalog look empty for no obvious reason.
    """
    for route in routes:
        if isinstance(route, APIRoute):
            acc.append(route)
        elif type(route).__name__ == "_IncludedRouter":
            original = getattr(route, "original_router", None)
            if original is not None and id(original) not in seen:
                seen.add(id(original))
                collect_routes(original.routes, acc, seen)
        elif hasattr(route, "routes"):
            collect_routes(route.routes, acc, seen)


def dependency_surface(dependant):
    """Every dependency callable name and header alias reachable from a route."""
    names: set[str] = set()
    headers: set[str] = set()

    def walk(dep):
        if dep.call is not None:
            names.add(getattr(dep.call, "__name__", str(dep.call)))
        for param in dep.header_params:
            headers.add((param.alias or param.name).lower())
        for sub in dep.dependencies:
            walk(sub)

    walk(dependant)
    return names, headers


# Routes that authenticate inside the handler rather than through a dependency,
# so the walk above cannot see it. The git remote reads `Authorization` off the
# raw request to answer with a Basic challenge git clients understand — there is
# no header parameter to detect. Keyed by the router-level path prefix.
HANDLER_AUTH_OVERRIDES = {
    "/git/": "basic_worker_token",
}

# Genuinely public: reachable with no credential at all, by design.
PUBLIC_PATHS = {
    "/api/v1/health",
    "/api/v1/github/install/callback",
}


def classify_auth(names: set[str], headers: set[str]) -> str:
    # Order matters: require_platform_admin depends on verify_token, so the
    # stricter guard has to win or every admin route reads as a plain user route.
    if "require_platform_admin" in names:
        return "platform_admin"
    if "verify_token" in names:
        return "user_jwt"
    if "verify_worker" in names:
        return "worker_token"
    if "x-report-key" in headers:
        return "report_key"
    if "x-worker-token" in headers:
        return "worker_token"
    if {"x-factory-mcp-key", "x-api-key", "authorization"} & headers:
        return "scoped_key"
    return "none"


def main() -> int:
    spec = app.openapi()
    spec_paths = spec["paths"]

    routes: list[APIRoute] = []
    collect_routes(app.routes, routes, set())

    strip_converter = lambda p: re.sub(r"{([^}:]+)(?::[^}]+)?}", r"{\1}", p)  # noqa: E731

    entries = []
    unmatched = []
    for route in routes:
        names, headers = dependency_surface(route.dependant)
        auth = classify_auth(names, headers)
        router_path = strip_converter(route.path)

        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            # The router knows its own path but not the prefix it was mounted
            # under; the OpenAPI document knows the full path. Join on the two
            # candidates rather than re-deriving prefixes by hand.
            full = next(
                (
                    candidate
                    for candidate in (router_path, "/api/v1" + router_path)
                    if candidate in spec_paths
                    and method.lower() in spec_paths[candidate]
                ),
                None,
            )
            if full is None:
                unmatched.append(f"{method} {route.path}")
                continue

            resolved = auth
            if resolved == "none":
                for prefix, kind in HANDLER_AUTH_OVERRIDES.items():
                    if full.startswith(prefix):
                        resolved = kind
                        break

            operation = spec_paths[full][method.lower()]
            params = {
                p["name"]: (p.get("schema") or {}).get("type", "string")
                for p in operation.get("parameters", [])
                if p.get("in") == "path"
            }
            entries.append(
                {
                    "method": method,
                    "path": full,
                    "tag": (route.tags or ["untagged"])[0],
                    "auth": resolved,
                    "public": full in PUBLIC_PATHS,
                    "op": route.name,
                    "params": params,
                    "body": bool(operation.get("requestBody")),
                    "mutating": method != "GET",
                }
            )

    if unmatched:
        print("Could not place these routes in the OpenAPI document:")
        for item in unmatched:
            print("  " + item)
        return 1

    entries.sort(key=lambda e: (e["tag"], e["path"], e["method"]))
    OUT.write_text(json.dumps(entries, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {OUT.relative_to(REPO_ROOT)} — {len(entries)} operations")
    print("  by auth: " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(e["auth"] for e in entries).items())))
    print("  by tag:  " + ", ".join(f"{k}={v}" for k, v in sorted(Counter(e["tag"] for e in entries).items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
