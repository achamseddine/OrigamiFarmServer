"""Authentication and authorization for the FarmOS tablet contract.

Separate from app/auth/dependencies.py on purpose — that chain is for
OIDC/dev-login platform+tenant sessions; this one is for the tablet's own
password login. Both ultimately guard the same tenant-scoped data, so both
still resolve tenant_id (called farm_id on the wire here) from
server-verified state only — never from a client-supplied value. See
TENANCY.md and docs/FARMOS_API.md.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

import jwt
from fastapi import Depends, Header
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import get_control_db
from app.common.enums import MembershipStatus, TenantStatus
from app.config import get_settings
from app.farmos.permissions import permissions_grid
from app.farmos.security import decode_access_token
from app.tenants.models import Tenant, TenantMembership

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class AccessContext:
    membership_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID  # serialized as "farm_id" everywhere on the wire
    email: str
    display_name: str
    role: str
    full_access: bool
    permissions: dict[str, dict[str, bool]] = field(default_factory=dict)

    def has(self, module_code: str, action: str) -> bool:
        if self.full_access:
            return True
        return self.permissions.get(module_code, {}).get(action, False)


def get_access_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_control_db),
) -> AccessContext:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Please sign in to continue.")

    settings = get_settings()
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=401, detail="Your session has expired. Please sign in again."
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="Please sign in again.") from exc

    try:
        user_id = uuid.UUID(claims["uid"])
        tenant_id = uuid.UUID(claims["farm_id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Please sign in again.") from exc

    user = db.get(UserIdentity, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in again.")

    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id, TenantMembership.user_id == user_id
        )
    ).scalar_one_or_none()
    if membership is None or membership.status != MembershipStatus.ACTIVE:
        raise HTTPException(
            status_code=403,
            detail="Your access to this farm has been turned off. Ask your farm owner for help.",
        )

    tenant = db.get(Tenant, tenant_id)
    if tenant is None or tenant.status == TenantStatus.TERMINATED:
        raise HTTPException(status_code=403, detail="This farm account is no longer available.")
    if tenant.status == TenantStatus.SUSPENDED:
        raise HTTPException(
            status_code=403,
            detail="This farm's subscription is paused. Ask your farm owner to reactivate it.",
        )

    full_access = membership.role in ("owner", "manager")
    return AccessContext(
        membership_id=membership.id,
        user_id=user.id,
        tenant_id=tenant_id,
        email=user.email,
        display_name=user.display_name,
        role=membership.role,
        full_access=full_access,
        permissions={} if full_access else permissions_grid(db, membership.id),
    )


def require_permission(module_code: str, action: str) -> Callable:
    def _dependency(access: AccessContext = Depends(get_access_context)) -> AccessContext:
        if not access.has(module_code, action):
            raise HTTPException(
                status_code=403,
                detail="You don't have permission to do that. Ask your farm owner or manager for access.",
            )
        return access

    return _dependency


def require_owner_or_manager(access: AccessContext = Depends(get_access_context)) -> AccessContext:
    if not access.full_access:
        raise HTTPException(status_code=403, detail="Only the farm owner or a manager can do that.")
    return access


def optional_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    return idempotency_key
