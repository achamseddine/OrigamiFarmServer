"""Password hashing and JWT issuance for the FarmOS tablet app's own login.

This is intentionally independent of app/auth/providers.py (OIDC / platform
dev-login): field workers using the tablet have no OIDC account, and the
tablet contract specifies a plain email+password POST /auth/login. The
token is still just a bearer of identity — every request re-loads the
membership from the database (app/farmos/deps.py), so a forged or stale
claim in the token itself grants nothing on its own.
"""

from __future__ import annotations

import time
import uuid

import bcrypt
import jwt

from app.config import Settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def issue_access_token(settings: Settings, *, user_id: uuid.UUID, tenant_id: uuid.UUID, email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": email,
        "uid": str(user_id),
        "farm_id": str(tenant_id),
        "iat": now,
        "exp": now + settings.farmos_token_ttl_days * 86400,
    }
    return jwt.encode(payload, settings.app_secret_key, algorithm="HS256")


def decode_access_token(settings: Settings, token: str) -> dict:
    return jwt.decode(token, settings.app_secret_key, algorithms=["HS256"])
