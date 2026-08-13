"""US-76.3/76.4: GitHub connects to — and runs on — one org at a time.

Reported 2026-08-10: Settings → GitHub showed "GitHub connected." and "No
GitHub account connected yet." together. Both lines were true about different
orgs, because the connect flow bound the installation to an arbitrary
membership (`limit 1`, no `order`, no reference to the active org) while the
page read the active one.

The same unscoped-read habit ran one org's work on another org's credential.
"""

import asyncio

import pytest

from app import github_tokens
from app.routers import github as github_router

ORG_A = "aaaaaaaa-0000-4000-8000-000000000001"  # active, membership created 2nd
ORG_B = "bbbbbbbb-0000-4000-8000-000000000002"  # membership created 1st


class _User:
    token = "user-jwt"
    id = "11111111-2222-4333-8444-555555555555"


def _postgrest(monkeypatch, module, handler):
    async def _get(settings, token, path, params):
        return handler(path, params)

    monkeypatch.setattr(module, "postgrest_get", _get)


# --- US-76.3: connect binds to the workspace you are in -----------------------


def test_the_active_org_wins_over_the_first_membership(settings_override, monkeypatch):
    """The exact shape of the bug: the active org is NOT the first membership."""

    def handler(path, params):
        if path == "organization_members":
            # ordered by created_at — B first, exactly as the old `limit 1`
            # query would have returned it
            assert params.get("order") == "created_at.asc"
            assert params.get("status") == "eq.active"
            return [{"org_id": ORG_B}, {"org_id": ORG_A}]
        if path == "principals":
            return [{"active_org_id": ORG_A}]
        raise AssertionError(path)

    _postgrest(monkeypatch, github_router, handler)
    org = asyncio.run(github_router.active_org_for(settings_override, _User()))
    assert org == ORG_A


def test_an_org_you_left_falls_back_to_a_membership(settings_override, monkeypatch):
    """A stale active_org_id must not connect an org you are no longer in."""

    def handler(path, params):
        if path == "organization_members":
            return [{"org_id": ORG_B}]
        if path == "principals":
            return [{"active_org_id": ORG_A}]  # no longer a member
        raise AssertionError(path)

    _postgrest(monkeypatch, github_router, handler)
    org = asyncio.run(github_router.active_org_for(settings_override, _User()))
    assert org == ORG_B


def test_an_unset_active_org_is_deterministic(settings_override, monkeypatch):
    """Unset falls back to the FIRST membership by created_at — arbitrary is
    what caused the bug; ordered is at least reproducible."""

    def handler(path, params):
        if path == "organization_members":
            return [{"org_id": ORG_B}, {"org_id": ORG_A}]
        if path == "principals":
            return [{"active_org_id": None}]
        raise AssertionError(path)

    _postgrest(monkeypatch, github_router, handler)
    assert asyncio.run(github_router.active_org_for(settings_override, _User())) == ORG_B


def test_no_membership_is_refused(settings_override, monkeypatch):
    from fastapi import HTTPException

    def handler(path, params):
        if path == "organization_members":
            return []
        raise AssertionError(path)

    _postgrest(monkeypatch, github_router, handler)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(github_router.active_org_for(settings_override, _User()))
    assert caught.value.status_code == 404


# --- US-76.4: a credential never crosses an org boundary ----------------------


def test_the_connection_read_is_filtered_to_the_owning_org(
    settings_override, monkeypatch
):
    seen: dict = {}

    async def _get(settings, token, path, params):
        seen.update(params)
        assert path == "github_connections"
        # The store holds both orgs' rows; RLS would return both.
        rows = [
            {"id": "b", "method": "app", "installation_id": 222, "repos": None},
            {"id": "a", "method": "app", "installation_id": 111, "repos": None},
        ]
        if params.get("org_id") == f"eq.{ORG_A}":
            return [rows[1]]
        return rows

    async def _mint(settings, installation_id):
        return f"token-for-{installation_id}"

    monkeypatch.setattr(github_tokens, "postgrest_get", _get)
    monkeypatch.setattr(github_tokens.github, "mint_installation_token", _mint)

    token = asyncio.run(
        github_tokens.token_for_user(settings_override, "jwt", ORG_A, "o/r")
    )
    # org A's installation, not whichever row came back first
    assert token == "token-for-111"
    assert seen["org_id"] == f"eq.{ORG_A}"


def test_the_org_argument_is_required():
    """A default would let the next call site re-open the hole silently."""
    import inspect

    params = inspect.signature(github_tokens.token_for_user).parameters
    assert params["org_id"].default is inspect.Parameter.empty
    # ...and it comes before the optional repo hint, so it cannot be skipped.
    assert list(params) == ["settings", "user_token", "org_id", "repo_full_name"]
