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
from collections.abc import Callable, Generator
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
from app.common.tenant_router import TenantDataRouter
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

    full_access = is_full_access_role(membership.role)
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


def is_full_access_role(role: str) -> bool:
    """owners/managers get every permission on every module, always — see
    module docstring: a farm can never lock itself out.
    """
    return role in ("owner", "manager")


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


def require_visitor_access(access: AccessContext = Depends(get_access_context)) -> AccessContext:
    """RULE-VIS-010: visitor PII is permission-controlled — only
    visit-operations roles (owner/manager/visitor_coordinator) can read or
    create visitor CRM records. Nobody else gets a route to it.
    """
    if not access.full_access and access.role != "visitor_coordinator":
        raise HTTPException(
            status_code=403, detail="Only a visitor coordinator or a farm manager can do that."
        )
    return access


def require_diagnostic_role(access: AccessContext = Depends(get_access_context)) -> AccessContext:
    """Constitution: "Veterinarians diagnose and prescribe." Recording a
    treatment (a diagnosis, a medication, a withdrawal period) is gated to
    the farm owner/manager or a veterinarian — a worker or accountant
    account is rejected before the request body is even looked at.
    """
    if not access.full_access and access.role != "veterinarian":
        raise HTTPException(
            status_code=403, detail="Only a veterinarian or a farm manager can record a treatment."
        )
    return access


def check_farm_id(farm_id: str, access: AccessContext) -> None:
    """Several list/create endpoints take farm_id as a required query or
    body field, per the contract. It is never trusted as authorization —
    farm_id is just access.tenant_id spelled the app's way (see
    docs/FARMOS_API.md), so any value that doesn't match the caller's own
    farm is rejected the same way a guessed cross-tenant object ID is:
    404, not 403, so a client can't use this to enumerate other farms.
    """
    try:
        if uuid.UUID(farm_id) != access.tenant_id:
            raise HTTPException(status_code=404, detail="Farm not found.")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Farm not found.") from exc


def optional_idempotency_key(
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> str | None:
    return idempotency_key


def get_farmos_tenant_db(
    access: AccessContext = Depends(get_access_context),
) -> Generator[Session, None, None]:
    """The farm-data-plane (RLS-protected) session for this request's own
    tenant — the same TenantDataRouter the rest of the codebase uses, just
    keyed off the FarmOS AccessContext instead of the OIDC tenant context.
    """
    with TenantDataRouter.session_for(access.tenant_id) as session:
        yield session
