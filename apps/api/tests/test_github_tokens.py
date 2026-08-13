"""app/github_tokens.py — connection preference + token materialization (US-3.15)."""

import asyncio

import pytest

from app import github_tokens
from app.config import Settings
from app.github import (
    GitHubCredentialError,
    GitHubNotConfigured,
    mint_error,
)


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
    )
    base.update(overrides)
    return Settings(**base)


APP_CONN = {"id": "c-app", "method": "app", "installation_id": 42,
            "vault_secret_id": None, "repos": []}
PAT_CONN = {"id": "c-pat", "method": "pat", "installation_id": None,
            "vault_secret_id": "sec-1",
            "repos": [{"full_name": "acme/webshop", "default_branch": "main"}]}


def test_pick_prefers_pat_that_lists_the_repo():
    picked = github_tokens.pick_connection([APP_CONN, PAT_CONN], "acme/webshop")
    assert picked["id"] == "c-pat"


def test_pick_prefers_app_when_pat_does_not_list_repo():
    picked = github_tokens.pick_connection([PAT_CONN, APP_CONN], "other/repo")
    assert picked["id"] == "c-app"


def test_pick_app_first_without_repo_context():
    assert github_tokens.pick_connection([PAT_CONN, APP_CONN])["id"] == "c-app"


def test_pick_pat_when_no_app():
    assert github_tokens.pick_connection([PAT_CONN], "other/repo")["id"] == "c-pat"


def test_pick_none_when_empty():
    assert github_tokens.pick_connection([]) is None


def test_pick_repo_match_is_case_insensitive():
    picked = github_tokens.pick_connection([APP_CONN, PAT_CONN], "ACME/WebShop")
    assert picked["id"] == "c-pat"


def test_materialize_app_mints_installation_token(monkeypatch):
    async def fake_mint(settings, installation_id):
        assert installation_id == 42
        return "ghs_minted"

    monkeypatch.setattr(
        "app.github_tokens.github.mint_installation_token", fake_mint
    )
    result = asyncio.run(github_tokens.materialize(_settings(), APP_CONN))
    assert result == "ghs_minted"


def test_materialize_pat_reads_vault(monkeypatch):
    monkeypatch.setattr(
        "app.github_tokens.read_vault_secret",
        lambda settings, secret_id: "github_pat_secret" if secret_id == "sec-1" else None,
    )
    result = asyncio.run(github_tokens.materialize(_settings(), PAT_CONN))
    assert result == "github_pat_secret"


def test_materialize_pat_missing_secret_is_credential_error(monkeypatch):
    """US-5.24 (b): a vault-less PAT row is a credential failure — the
    manager must reconnect — not a "no connection" answer."""
    monkeypatch.setattr(
        "app.github_tokens.read_vault_secret", lambda settings, secret_id: None
    )
    with pytest.raises(GitHubCredentialError) as ei:
        asyncio.run(github_tokens.materialize(_settings(), PAT_CONN))
    assert "reconnect GitHub" in ei.value.message


def test_materialize_none_falls_back_to_env():
    result = asyncio.run(github_tokens.materialize(_settings(github_token="envtok"), None))
    assert result == "envtok"


def test_materialize_none_without_env_is_not_configured():
    """US-5.24 (a): no connection and no env fallback names the manager
    as the fix owner."""
    with pytest.raises(GitHubNotConfigured) as ei:
        asyncio.run(github_tokens.materialize(_settings(github_token=""), None))
    assert "manager must connect" in ei.value.message


def test_materialize_app_mint_failure_logs_and_reraises(monkeypatch, caplog):
    """US-5.24 (b): mint failures keep their upstream status for the
    caller and leave exactly one warning line with the full detail."""

    async def boom(settings, installation_id):
        raise mint_error(404)

    monkeypatch.setattr("app.github_tokens.github.mint_installation_token", boom)
    with pytest.raises(GitHubCredentialError) as ei:
        asyncio.run(github_tokens.materialize(_settings(), APP_CONN))
    assert ei.value.upstream_status == 404
    assert "reconnect GitHub" in ei.value.message
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "method=app" in caplog.text
    assert "42" in caplog.text


def test_mint_error_distinguishes_mismatch_from_bad_key():
    assert "does not belong" in mint_error(404).message
    assert mint_error(404).upstream_status == 404
    assert "bad App id or private key" in mint_error(401).message
    assert mint_error(401).upstream_status == 401
    assert "HTTP 500" in mint_error(500).message


def test_gitproxy_repo_token_resolves_pat(client, settings_override, monkeypatch):
    """A PAT-only org's git proxy upstream credential is the vault PAT."""
    import asyncio

    from app.routers import gitproxy

    monkeypatch.setattr(
        "app.github_tokens.db.get_github_connections",
        lambda settings, org_id: [dict(PAT_CONN)],
    )
    monkeypatch.setattr(
        "app.github_tokens.read_vault_secret",
        lambda settings, secret_id: "github_pat_secret",
    )
    gitproxy._token_cache.clear()
    token = asyncio.run(
        gitproxy._repo_token(settings_override, "org-1", "acme/webshop")
    )
    assert token == "github_pat_secret"


def test_gitproxy_credential_failure_is_403_with_cause(
    client, settings_override, monkeypatch
):
    """US-5.24: a clone/push against broken org credentials answers the
    credential message (403), not a bare "repository not found" 404."""
    from fastapi import HTTPException

    from app.routers import gitproxy

    monkeypatch.setattr(
        "app.github_tokens.db.get_github_connections",
        lambda settings, org_id: [dict(APP_CONN)],
    )

    async def boom(settings, installation_id):
        raise mint_error(404)

    monkeypatch.setattr(
        "app.github_tokens.github.mint_installation_token", boom
    )
    gitproxy._token_cache.clear()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            gitproxy._repo_token(settings_override, "org-1", "acme/webshop")
        )
    assert ei.value.status_code == 403
    assert "reconnect GitHub" in ei.value.detail
