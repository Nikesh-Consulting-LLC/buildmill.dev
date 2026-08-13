"""GitHub credential resolution (US-3.15): the one place that answers
"give me a token for this org (and repo)" across connection methods —
a PAT explicitly listing the requested repo first, App installation
next (short-lived, mintable), any other PAT after that (Vault),
GITHUB_TOKEN env as the existing last-resort fallback.

Two entry points mirror the codebase's two data paths: token_for_user
(PostgREST + caller's JWT, RLS-scoped — routers) and token_for_org
(direct Postgres, service path — git proxy, worker endpoints, deploys,
cron). The picked PAT never narrows: the user narrowed it at creation
on GitHub; a PAT that explicitly lists the repo beats the App because
its grant is known, while the App's reach can't be checked cheaply.
"""

import asyncio
import logging
from typing import Any

from . import db, github
from .config import Settings
from .llm import read_vault_secret
from .supabase import postgrest_get

logger = logging.getLogger(__name__)

CONNECTION_SELECT = "id,method,installation_id,vault_secret_id,repos"


def pick_connection(
    connections: list[dict[str, Any]], repo_full_name: str | None = None
) -> dict[str, Any] | None:
    if repo_full_name:
        wanted = repo_full_name.lower()
        for c in connections:
            if c["method"] == "pat" and any(
                (r.get("full_name") or "").lower() == wanted
                for r in (c.get("repos") or [])
            ):
                return c
    for c in connections:
        if c["method"] == "app":
            return c
    for c in connections:
        if c["method"] == "pat":
            return c
    return None


async def materialize(
    settings: Settings, connection: dict[str, Any] | None
) -> str:
    if connection is None:
        if settings.github_token:
            return settings.github_token
        raise github.GitHubNotConfigured(
            "the org has no GitHub connection — the manager must connect "
            "one in Settings → GitHub"
        )
    if connection["method"] == "app":
        try:
            return await github.mint_installation_token(
                settings, connection["installation_id"]
            )
        except github.GitHubCredentialError as e:
            logger.warning(
                "GitHub credential failure: method=app installation_id=%s "
                "upstream_status=%s",
                connection["installation_id"],
                e.upstream_status,
            )
            raise
    secret = await asyncio.to_thread(
        read_vault_secret, settings, connection["vault_secret_id"]
    )
    if not secret:
        logger.warning(
            "GitHub credential failure: method=pat connection_id=%s "
            "vault secret missing",
            connection.get("id"),
        )
        raise github.GitHubCredentialError(
            "stored GitHub token missing from the vault — the manager must "
            "reconnect GitHub in Settings → GitHub"
        )
    return secret


def describe_connection(connection: dict[str, Any] | None, token: str) -> str:
    """The credential in words (US-79.2) — method and identity, never the
    secret. When GitHub answers 401 or 404, "which credential was even used"
    is the whole diagnosis, and a bare token string cannot say. The PAT's
    last-4 matches the `key_last4` convention the UI already shows."""
    if connection is None:
        return "the GITHUB_TOKEN environment fallback"
    if connection["method"] == "app":
        return (
            "the org's GitHub App installation "
            f"(id {connection['installation_id']})"
        )
    return f"the stored PAT (…{token[-4:]})"


async def resolve_for_user(
    settings: Settings,
    user_token: str,
    org_id: str,
    repo_full_name: str | None = None,
) -> tuple[str, str]:
    """US-76.4: the credential for work owned by `org_id`, and its description.

    RLS scopes this read to every org the caller belongs to — the right
    boundary for *the user*, the wrong one for *the operation*. Without the
    filter, `pick_connection` returned the first App row it happened to see, so
    merging a PR in one workspace could mint another workspace's installation
    token. `org_id` is required rather than defaulted: a default would let the
    next call site re-open the hole silently.
    """
    rows = await postgrest_get(
        settings,
        user_token,
        "github_connections",
        {"select": CONNECTION_SELECT, "org_id": f"eq.{org_id}"},
    )
    connection = pick_connection(rows, repo_full_name)
    token = await materialize(settings, connection)
    return token, describe_connection(connection, token)


async def token_for_user(
    settings: Settings,
    user_token: str,
    org_id: str,
    repo_full_name: str | None = None,
) -> str:
    token, _ = await resolve_for_user(settings, user_token, org_id, repo_full_name)
    return token


async def token_for_org(
    settings: Settings, org_id: str, repo_full_name: str | None = None
) -> str:
    rows = await asyncio.to_thread(db.get_github_connections, settings, org_id)
    return await materialize(settings, pick_connection(rows, repo_full_name))
