"""The tool surface a run gets, and what is safe to record about it.

Three stories meet here, and they meet on purpose:

* us-34.1's catalog says where a server is and what credential it needs.
* us-34.3 composes the EFFECTIVE surface — default deny, the preset asks, the
  project may refuse — and records it on the run.
* us-34.4 decides what a proxied call may be remembered as.

Kept out of the router so the security-relevant decisions are testable without a
request, and out of `db.py` so they are readable as one argument.
"""

from __future__ import annotations

import re
from typing import Any

TRANSPORTS = ("http", "stdio")

# US-34.1: a slug is what the `mcp.json` key becomes, so it has to be safe to put
# in a JSON key and in an argv. Anything else is refused at registration rather
# than sanitised silently — a server whose name changed under the manager is a
# server they cannot find again.
# One character is a legitimate slug: a server named "X" is a real thing to
# register, and requiring two would refuse it for no reason.
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,38}[a-z0-9])?$")

MAX_NAME = 60
MAX_TOOLS = 60


class CatalogInvalid(Exception):
    """A refusal that names the field and what would have been accepted."""


def clean_entry(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate a catalog registration. Shape only — reachability is a live check.

    us-27.13's rule: a check belongs where the value is entered, not where it
    eventually fails. This is the half that needs no network.
    """
    name = str(raw.get("name") or "").strip()
    if not name:
        raise CatalogInvalid("a server needs a name")
    if len(name) > MAX_NAME:
        raise CatalogInvalid(f"a name may be at most {MAX_NAME} characters")

    slug = str(raw.get("slug") or "").strip().lower()
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not SLUG_RE.match(slug):
        raise CatalogInvalid(
            f"'{slug}' is not a usable id — lowercase letters, digits, dashes and "
            "underscores, up to 40 characters, starting and ending with a letter "
            "or digit. It becomes the server's key in the "
            "agent's MCP config, so it cannot contain anything a JSON key or a "
            "command line would mangle."
        )

    transport = str(raw.get("transport") or "").strip().lower()
    if transport not in TRANSPORTS:
        raise CatalogInvalid(
            f"transport must be one of: {', '.join(TRANSPORTS)} (got '{transport}')"
        )

    endpoint = str(raw.get("endpoint") or "").strip()
    command = str(raw.get("command") or "").strip()
    if transport == "http":
        if not endpoint:
            raise CatalogInvalid("an http server needs an endpoint")
        if not endpoint.startswith(("http://", "https://")):
            raise CatalogInvalid(
                "an endpoint must be an http(s) URL — the proxy has to be able to "
                "reach it, and the scheme is how it knows how"
            )
        command = ""
    else:
        if not command:
            raise CatalogInvalid("a stdio server needs a command to launch")
        endpoint = ""

    needs_credential = bool(raw.get("needs_credential"))
    header = str(raw.get("credential_header") or "").strip()
    if needs_credential and transport == "http" and not header:
        # Without knowing HOW to present the credential, the proxy would resolve
        # a secret it cannot use — which fails at the first tool call rather than
        # at registration.
        raise CatalogInvalid(
            "a credentialed http server needs the header its credential is "
            "presented as (for example `Authorization` or `x-api-key`)"
        )

    tools = [str(t).strip()[:80] for t in (raw.get("declared_tools") or []) if str(t).strip()]
    if len(tools) > MAX_TOOLS:
        raise CatalogInvalid(f"at most {MAX_TOOLS} declared tools")

    return {
        "name": name,
        "slug": slug,
        "description": str(raw.get("description") or "").strip()[:400],
        "transport": transport,
        "endpoint": endpoint or None,
        "command": command or None,
        "declared_tools": tools,
        "needs_credential": needs_credential,
        "credential_header": header[:80] or None,
    }


# ---------------------------------------------------------------------------
# US-34.3: the effective surface
# ---------------------------------------------------------------------------


def compose_surface(
    *,
    grants: list[str] | None,
    catalog: dict[str, dict[str, Any]],
    withheld: list[str] | None,
) -> dict[str, Any]:
    """What this run may reach, and why anything it cannot is missing.

    Default deny: no grants means the factory server and nothing else — the state
    us-31.9 ships. Registering a server in the catalog therefore changes no
    existing run's surface until a preset names it, so an admin adding a server
    cannot accidentally re-tool every run in the org. The same fail-closed
    principle us-31.3 applies to project grants.

    The result is a record, not just a list: a withheld or unavailable server is
    NAMED, because a run that quietly received a smaller toolset and produced
    worse work is the failure this whole design is trying to avoid.
    """
    refused = set(str(w) for w in (withheld or []))
    granted: list[dict[str, Any]] = []
    withheld_out: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []

    for raw_id in grants or []:
        server_id = str(raw_id)
        entry = catalog.get(server_id)
        if not entry:
            # A preset naming an entry that was removed. Reported rather than
            # dropped: the manager configured a tool that is not there.
            unavailable.append(
                {"id": server_id, "name": None, "why": "no longer in the catalog"}
            )
            continue
        if server_id in refused:
            withheld_out.append(
                {
                    "id": server_id,
                    "name": entry.get("name"),
                    "why": "this project withholds it",
                }
            )
            continue
        if not entry.get("enabled", True):
            unavailable.append(
                {
                    "id": server_id,
                    "name": entry.get("name"),
                    "why": "disabled in the catalog",
                }
            )
            continue
        if entry.get("last_check_ok") is False:
            unavailable.append(
                {
                    "id": server_id,
                    "name": entry.get("name"),
                    "why": entry.get("last_check_error")
                    or "its last validation failed",
                }
            )
            continue
        granted.append(
            {
                "id": server_id,
                "slug": entry.get("slug"),
                "name": entry.get("name"),
                "transport": entry.get("transport"),
                "tools": list(entry.get("declared_tools") or []),
                # US-34.4: a credential-free stdio server runs on the machine and
                # never passes through the proxy, so it CANNOT be audited there.
                # Said here rather than implying a completeness the design does
                # not have.
                "proxied": bool(entry.get("needs_credential"))
                or entry.get("transport") == "http",
            }
        )

    return {
        "granted": granted,
        "withheld": withheld_out,
        "unavailable": unavailable,
        # The factory's own server is always present and is not a grant — it is
        # how a run hands work back at all (us-31.9).
        "factory": True,
        "audited": [g["id"] for g in granted if g["proxied"]],
        "unaudited": [g["id"] for g in granted if not g["proxied"]],
    }


def surface_notes(surface: dict[str, Any]) -> list[str]:
    """One line per thing the agent should be told is missing, and why.

    A run starts with a tool absent; the agent is told which server is
    unavailable and why, rather than silently receiving a smaller toolset.
    """
    notes = []
    for entry in surface.get("withheld") or []:
        notes.append(
            f"the '{entry.get('name') or entry.get('id')}' tool server is not "
            f"available on this project: {entry.get('why')}"
        )
    for entry in surface.get("unavailable") or []:
        notes.append(
            f"the '{entry.get('name') or entry.get('id')}' tool server is "
            f"unavailable: {entry.get('why')}"
        )
    return notes


# ---------------------------------------------------------------------------
# US-34.4: what a call may be remembered as
# ---------------------------------------------------------------------------

# Argument names that carry secrets. Matched loosely on purpose: a false positive
# costs a redacted field, a false negative costs a credential in a table any org
# member can read.
SECRET_HINTS = (
    "key",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "auth",
    "bearer",
    "cookie",
    "session",
    "private",
    "signature",
)

# Values that look like a credential whatever they are called.
SECRET_VALUE_RE = re.compile(
    r"("
    r"sk-[A-Za-z0-9_\-]{8,}"          # OpenAI-style
    r"|sk_live_[A-Za-z0-9]{8,}"
    r"|sfw_[A-Za-z0-9]{8,}"           # this factory's own worker tokens
    r"|sfg_[A-Za-z0-9]{8,}"           # ...and its gateway keys
    r"|sfm_[A-Za-z0-9]{8,}"           # ...and its scoped MCP keys
    r"|gh[pousr]_[A-Za-z0-9]{8,}"     # GitHub
    r"|xox[baprs]-[A-Za-z0-9\-]{8,}"  # Slack
    r"|ey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."  # a JWT
    r")"
)

REDACTED = "[redacted]"
MAX_VALUE = 120
MAX_ARG_KEYS = 30


def redact_arguments(args: Any, _depth: int = 0) -> Any:
    """A conservative record of what was asked.

    Enough to know what was asked without becoming a second copy of the data.
    Anything that looks like a credential is REMOVED rather than truncated,
    because a truncated secret is still a secret — and the shell audit made the
    same trade for the same reason.
    """
    if _depth > 4:
        return REDACTED
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for i, (key, value) in enumerate(args.items()):
            if i >= MAX_ARG_KEYS:
                out["…"] = f"{len(args) - MAX_ARG_KEYS} more argument(s)"
                break
            name = str(key)[:60]
            if any(hint in name.lower() for hint in SECRET_HINTS):
                out[name] = REDACTED
                continue
            out[name] = redact_arguments(value, _depth + 1)
        return out
    if isinstance(args, list):
        return [redact_arguments(v, _depth + 1) for v in args[:20]]
    if isinstance(args, str):
        if SECRET_VALUE_RE.search(args):
            return REDACTED
        if len(args) > MAX_VALUE:
            # Long free text is summarised rather than stored: a prompt or a file
            # body is project data, and this table is not where it belongs.
            return f"[{len(args)} chars]"
        return args
    if isinstance(args, (int, float, bool)) or args is None:
        return args
    return REDACTED
