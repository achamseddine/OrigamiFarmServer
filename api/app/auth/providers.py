"""Pluggable identity-provider verification.

Origami Server speaks standards-based OIDC. The concrete provider (Keycloak
today) is isolated behind IdentityProvider so switching providers later is
a config + adapter change, not a rewrite of every route. AUTH_DEV_MODE
swaps in DevIdentityProvider, which signs/verifies tokens locally with
APP_SECRET_KEY — for local development and automated tests only. It must
never be enabled in staging or production (enforced in app/main.py).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import jwt

from app.config import Settings


@dataclass(frozen=True)
class VerifiedClaims:
    subject: str
    email: str
    display_name: str


class IdentityProvider(ABC):
    @abstractmethod
    def verify(self, token: str) -> VerifiedClaims: ...


class OIDCIdentityProvider(IdentityProvider):
    """Validates access tokens against a standards-based OIDC provider
    (Keycloak by default) via its published JWKS.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._jwks_client = jwt.PyJWKClient(settings.oidc_jwks_url)

    def verify(self, token: str) -> VerifiedClaims:
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self._settings.oidc_audience,
            issuer=self._settings.oidc_issuer,
        )
        return VerifiedClaims(
            subject=claims["sub"],
            email=claims.get("email", ""),
            display_name=claims.get("name") or claims.get("preferred_username", ""),
        )


class DevIdentityProvider(IdentityProvider):
    """Local-only stand-in for a real IdP. Tokens are HS256 JWTs signed
    with APP_SECRET_KEY via app/auth/session.py:issue_dev_session_token.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def verify(self, token: str) -> VerifiedClaims:
        claims = jwt.decode(token, self._settings.app_secret_key, algorithms=["HS256"])
        return VerifiedClaims(
            subject=claims["sub"],
            email=claims.get("email", ""),
            display_name=claims.get("name", ""),
        )


def issue_dev_session_token(settings: Settings, *, subject: str, email: str, name: str) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "email": email,
        "name": name,
        "iat": now,
        "exp": now + 12 * 3600,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def get_identity_provider(settings: Settings) -> IdentityProvider:
    if settings.auth_dev_mode:
        return DevIdentityProvider(settings)
    return OIDCIdentityProvider(settings)
