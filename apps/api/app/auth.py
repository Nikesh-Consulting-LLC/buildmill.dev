"""Supabase JWT verification via JWKS (US-1.8).

The web app sends the user's Supabase access token as a Bearer token;
we verify it server-side against the project's JWKS — no shared secret.
"""

from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient
from pydantic import BaseModel

from .config import Settings, get_settings


class AuthUser(BaseModel):
    id: str
    email: str
    token: str  # forwarded to PostgREST so RLS applies as this user


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    return PyJWKClient(jwks_url, cache_keys=True)


def get_signing_key(token: str, settings: Settings):
    return _jwks_client(settings.jwks_url).get_signing_key_from_jwt(token).key


def verify_token(
    request: Request, settings: Settings = Depends(get_settings)
) -> AuthUser:
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = auth_header.split(" ", 1)[1].strip()

    try:
        key = get_signing_key(token, settings)
        claims = jwt.decode(
            token,
            key,
            algorithms=["ES256", "RS256"],
            audience="authenticated",
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    return AuthUser(
        id=claims["sub"],
        email=claims.get("email", ""),
        token=token,
    )
