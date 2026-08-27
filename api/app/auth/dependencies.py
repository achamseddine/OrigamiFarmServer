"""Central, testable authorization dependencies.

Every protected route composes these instead of re-implementing checks.
The chain enforced across get_identity -> get_tenant_context ->
require_module/require_permission/require_farm_scope mirrors the
"Tenant Status x Subscription/Entitlement x User Permission x Farm Scope x
Device State" model from ARCHITECTURE.md — a request is only permitted if
every stage passes.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.auth.providers import get_identity_provider
from app.auth.schemas import Identity, TenantContext
from app.common.db import get_control_db
from app.common.enums import (
    DeviceStatus,
    MembershipStatus,
    PlatformRole,
    TenantStatus,
)
from app.common.errors import AppError, ErrorCode
from app.config import get_settings
from app.devices.models import Device
from app.entitlements.service import EntitlementService
from app.tenants.models import (
    MembershipFarmAccess,
    MembershipModulePermission,
    PlatformRoleAssignment,
    Tenant,
    TenantMembership,
)

_bearer = HTTPBearer(auto_error=False)


def get_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_control_db),
) -> Identity:
    if credentials is None or not credentials.credentials:
        raise AppError(ErrorCode.UNAUTHENTICATED, "Missing bearer token")

    settings = get_settings()
    provider = get_identity_provider(settings)
    try:
        claims = provider.verify(credentials.credentials)
    except Exception as exc:  # noqa: BLE001 - any verification failure is unauthenticated
        raise AppError(ErrorCode.INVALID_TOKEN, "Token verification failed") from exc

    user = db.execute(
        select(UserIdentity).where(UserIdentity.idp_subject == claims.subject)
    ).scalar_one_or_none()
    if user is None:
        user = UserIdentity(
            idp_subject=claims.subject,
            email=claims.email or f"{claims.subject}@unknown.local",
            display_name=claims.display_name or claims.subject,
        )
        db.add(user)
        db.flush()

    return Identity(
        user_id=user.id,
        idp_subject=user.idp_subject,
        email=user.email,
        display_name=user.display_name,
    )


def get_tenant_context(
    identity: Identity = Depends(get_identity),
    db: Session = Depends(get_control_db),
    x_device_id: str | None = Header(default=None, alias="X-Device-Id"),
    x_membership_id: str | None = Header(default=None, alias="X-Membership-Id"),
) -> TenantContext:
    """Resolves tenant scope purely from server-side state.

    A device header, if present, is authoritative for tenant_id (a device
    is bound to exactly one tenant at activation time by an authorized
    admin — see app/devices). A membership header is only ever a *hint*
    about which of the caller's own verified memberships to use; it is
    always re-checked against tenant_membership.user_id before being
    trusted, so a client can never claim someone else's tenant this way.
    """

    device_id: uuid.UUID | None = None
    tenant_id: uuid.UUID

    if x_device_id:
        try:
            device = db.get(Device, uuid.UUID(x_device_id))
        except ValueError:
            device = None
        if device is None:
            raise AppError(ErrorCode.DEVICE_NOT_FOUND)
        if device.status != DeviceStatus.ACTIVE:
            raise AppError(ErrorCode.DEVICE_REVOKED)
        tenant_id = device.tenant_id
        device_id = device.id
    else:
        memberships = list(
            db.execute(
                select(TenantMembership).where(
                    TenantMembership.user_id == identity.user_id,
                    TenantMembership.status == MembershipStatus.ACTIVE,
                )
            )
            .scalars()
            .all()
        )
        if not memberships:
            raise AppError(ErrorCode.MEMBERSHIP_NOT_FOUND)
        chosen: TenantMembership | None = None
        if len(memberships) == 1:
            chosen = memberships[0]
        elif x_membership_id:
            chosen = next((m for m in memberships if str(m.id) == x_membership_id), None)
            if chosen is None:
                # The caller asked for a membership that either doesn't
                # exist or doesn't belong to them — never trust the header
                # value itself as authorization.
                raise AppError(ErrorCode.PERMISSION_DENIED, "Not a member of the requested tenant")
        else:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "Multiple tenant memberships found; specify X-Membership-Id",
            )
        assert chosen is not None  # every branch above either raises or sets it
        tenant_id = chosen.tenant_id

    membership = db.execute(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == identity.user_id,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one_or_none()
    if membership is None:
        raise AppError(ErrorCode.MEMBERSHIP_NOT_FOUND)

    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError(ErrorCode.MEMBERSHIP_NOT_FOUND)
    if tenant.status == TenantStatus.TERMINATED:
        raise AppError(ErrorCode.TENANT_TERMINATED)
    if tenant.status == TenantStatus.SUSPENDED:
        raise AppError(ErrorCode.TENANT_SUSPENDED)

    farm_ids = [
        row.farm_id
        for row in db.execute(
            select(MembershipFarmAccess).where(
                MembershipFarmAccess.membership_id == membership.id
            )
        ).scalars()
    ]
    permissions = {
        f"{row.module_code}:{row.permission_code}"
        for row in db.execute(
            select(MembershipModulePermission).where(
                MembershipModulePermission.membership_id == membership.id
            )
        ).scalars()
    }

    return TenantContext(
        tenant_id=tenant.id,
        tenant_status=tenant.status,
        membership_id=membership.id,
        tenant_role=membership.tenant_role,
        farm_ids=farm_ids,
        module_permissions=permissions,
        device_id=device_id,
    )


def require_platform_role(*allowed_roles: PlatformRole) -> Callable:
    def _dependency(
        identity: Identity = Depends(get_identity),
        db: Session = Depends(get_control_db),
    ) -> Identity:
        assignments = db.execute(
            select(PlatformRoleAssignment).where(
                PlatformRoleAssignment.user_id == identity.user_id
            )
        ).scalars().all()
        held_roles = {PlatformRole(a.platform_role) for a in assignments}
        if PlatformRole.PLATFORM_SUPER_ADMIN in held_roles:
            return identity
        if not held_roles.intersection(allowed_roles):
            raise AppError(ErrorCode.PLATFORM_ROLE_REQUIRED)
        return identity

    return _dependency


def require_module(module_code: str) -> Callable:
    def _dependency(
        tenant_context: TenantContext = Depends(get_tenant_context),
        db: Session = Depends(get_control_db),
    ) -> TenantContext:
        if not EntitlementService(db).is_module_active(tenant_context.tenant_id, module_code):
            raise AppError(ErrorCode.MODULE_NOT_ENTITLED, f"Module {module_code} is not entitled")
        return tenant_context

    return _dependency


def require_permission(module_code: str, action: str) -> Callable:
    def _dependency(
        tenant_context: TenantContext = Depends(require_module(module_code)),
    ) -> TenantContext:
        if not tenant_context.has_permission(module_code, action):
            raise AppError(ErrorCode.PERMISSION_DENIED)
        return tenant_context

    return _dependency


def require_farm_scope(farm_id: uuid.UUID, tenant_context: TenantContext) -> None:
    if not tenant_context.has_farm_access(farm_id):
        raise AppError(ErrorCode.FARM_SCOPE_DENIED)
