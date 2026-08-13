"""app/github.py — state token, App JWT, and pure helpers (US-1.19)."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from app import github
from app.config import Settings


def _settings(**overrides) -> Settings:
    base = dict(
        supabase_url="https://test.supabase.co",
        supabase_publishable_key="sb_publishable_test",
        github_app_id="123456",
        github_app_slug="software-factory-dev",
        github_app_state_secret="test-state-secret",
    )
    base.update(overrides)
    return Settings(**base)


def _rsa_pem() -> tuple[str, str]:
    """Returns (private_pem, public_pem) as strings."""
    key = generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


def test_state_roundtrip():
    settings = _settings()
    state = github.make_state(settings, org_id="org-1", user_id="user-1")
    org_id, user_id = github.verify_state(settings, state)
    assert org_id == "org-1"
    assert user_id == "user-1"


def test_state_tampered_is_rejected():
    settings = _settings()
    state = github.make_state(settings, org_id="org-1", user_id="user-1")
    with pytest.raises(github.GitHubError):
        github.verify_state(_settings(github_app_state_secret="different"), state)


def test_state_expired_is_rejected():
    settings = _settings()
    now = int(time.time())
    expired = jwt.encode(
        {"org_id": "org-1", "user_id": "user-1", "iat": now - 1000, "exp": now - 1},
        settings.github_app_state_secret,
        algorithm="HS256",
    )
    with pytest.raises(github.GitHubError):
        github.verify_state(settings, expired)


def test_mint_app_jwt_is_valid_rs256():
    private_pem, public_pem = _rsa_pem()
    settings = _settings(github_app_private_key=private_pem)
    token = github.mint_app_jwt(settings)
    claims = jwt.decode(token, public_pem, algorithms=["RS256"])
    assert claims["iss"] == "123456"


def test_private_key_pem_unescapes_literal_newlines():
    settings = _settings(github_app_private_key="line1\\nline2")
    assert settings.github_app_private_key_pem == "line1\nline2"


def test_parse_pr_url():
    owner, repo, number = github.parse_pr_url(
        "https://github.com/acme/webshop/pull/42"
    )
    assert (owner, repo, number) == ("acme", "webshop", 42)


def test_parse_pr_url_invalid_raises():
    with pytest.raises(github.GitHubError):
        github.parse_pr_url("not-a-url")
