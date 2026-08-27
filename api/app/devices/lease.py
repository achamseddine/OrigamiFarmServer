"""Signed offline license lease (JWS via RS256).

The private key lives only on the server; tablets ship only the public
key and verify leases locally while offline. Never place the private key
or a secret capable of minting leases on a client device.
"""

from __future__ import annotations

import functools
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.config import Settings


@functools.lru_cache(maxsize=2)
def _read_key(path: str) -> str:
    with open(path) as f:
        return f.read()


def _private_key(settings: Settings) -> str:
    return _read_key(settings.license_lease_private_key_path)


def _public_key(settings: Settings) -> str:
    return _read_key(settings.license_lease_public_key_path)


def issue_license_lease(
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    farm_ids: list[uuid.UUID],
    modules: list[str],
    permission_profile_hash: str,
    ttl_hours: int | None = None,
) -> tuple[uuid.UUID, str, datetime]:
    lease_id = uuid.uuid4()
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=ttl_hours or settings.license_lease_default_ttl_hours)

    payload = {
        "lease_id": str(lease_id),
        "tenant_id": str(tenant_id),
        "device_id": str(device_id),
        "farm_ids": [str(f) for f in farm_ids],
        "modules": modules,
        "permission_profile_hash": permission_profile_hash,
        "issued_at": issued_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "policy_version": settings.license_lease_policy_version,
        "iat": int(issued_at.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, _private_key(settings), algorithm="RS256")
    return lease_id, token, expires_at


def verify_license_lease(settings: Settings, token: str) -> dict:
    """Server-side verification, used by tests and by any debug tooling.
    Tablets perform the equivalent check locally with only the public key.
    """
    return jwt.decode(token, _public_key(settings), algorithms=["RS256"])
