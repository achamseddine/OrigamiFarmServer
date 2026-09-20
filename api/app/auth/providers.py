"""Pluggable identity-provider verification.

Two kinds of credential can reach a platform/tenant route, and this module
is where each is turned into a VerifiedClaims:

* A **session token this server issued itself** (HS256 over APP_SECRET_KEY)
  — minted by the platform password login in app/platform/auth_routes.py,
  and by dev-login locally. This is what the admin console carries.
* An **OIDC access token** from an external provider (RS256, verified
  against the provider's published JWKS). Origami speaks standards-based
  OIDC so an organization can put its own IdP in front of the console;
  the concrete provider stays behind IdentityProvider so switching is a
  config + adapter change rather than a rewrite of every route.

Both are accepted in production. AUTH_DEV_MODE does not change how a
session token is verified — only whether one can be obtained without a
password (see app/auth/routes.py), which is why app/main.py refuses to
boot with it enabled in production.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache

import jwt

from app.config import Settings

# Marks a token as a console/API session rather than a FarmOS tablet token.
# Both are HS256 over the same secret, so without this claim the two
# families would be silently interchangeable — see app/farmos/security.py.
SESSION_TOKEN_TYPE = "platform_session"


@dataclass(frozen=True)
class VerifiedClaims:
    subject: str
    email: str
    display_name: str


class IdentityProvider(ABC):
    @abstractmethod
    def verify(self, token: str) -> VerifiedClaims: ...


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    """Cached per URL: a fresh client would re-fetch the provider's keys on
    every single request.
    """
    return jwt.PyJWKClient(jwks_url)


class OIDCIdentityProvider(IdentityProvider):
    """Validates access tokens against a standards-based OIDC provider
    (Keycloak by default) via its published JWKS.
    """

    def __init__(self, settings: Settings):
        self._settings = settings

    def verify(self, token: str) -> VerifiedClaims:
        signing_key = _jwks_client(self._settings.oidc_jwks_url).get_signing_key_from_jwt(token)
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


class LocalSessionProvider(IdentityProvider):
    """Verifies a session token this server signed with APP_SECRET_KEY."""

    def __init__(self, settings: Settings):
        self._settings = settings

    def verify(self, token: str) -> VerifiedClaims:
        claims = jwt.decode(token, self._settings.app_secret_key, algorithms=["HS256"])
        if claims.get("typ") != SESSION_TOKEN_TYPE:
            raise jwt.InvalidTokenError("Not a platform session token")
        return VerifiedClaims(
            subject=claims["sub"],
            email=claims.get("email", ""),
            display_name=claims.get("name", ""),
        )


class ChainedIdentityProvider(IdentityProvider):
    """Accepts either credential this deployment can present.

    The JWT's own algorithm header decides which verifier sees it: HS256 is
    a session this server issued, RS256 an OIDC access token. Each branch
    pins its own algorithm list and key source, so a token cannot be
    steered into the wrong verifier to have its signature checked against
    the wrong key.
    """

    def __init__(self, settings: Settings):
        self._local = LocalSessionProvider(settings)
        self._oidc = OIDCIdentityProvider(settings)

    def verify(self, token: str) -> VerifiedClaims:
        if jwt.get_unverified_header(token).get("alg") == "HS256":
            return self._local.verify(token)
        return self._oidc.verify(token)


def issue_session_token(
    settings: Settings, *, subject: str, email: str, name: str, ttl_hours: int | None = None
) -> tuple[str, int]:
    """Mints a console/API session. Returns the token and its expiry."""
    now = int(time.time())
    expires_at = now + (ttl_hours or settings.platform_session_ttl_hours) * 3600
    payload = {
        "sub": subject,
        "email": email,
        "name": name,
        "typ": SESSION_TOKEN_TYPE,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256"), expires_at


def get_identity_provider(settings: Settings) -> IdentityProvider:
    # Dev mode never needs the OIDC branch, and skipping it keeps a stray
    # RS256 token from provoking a JWKS fetch against a provider that
    # isn't running locally.
    if settings.auth_dev_mode:
        return LocalSessionProvider(settings)
    return ChainedIdentityProvider(settings)
