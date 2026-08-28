"""Shared test setup helpers.

Platform role grants and tenant memberships are seeded directly against
the control database in tests rather than through an API, mirroring how a
fresh environment is bootstrapped (see scripts/seed.py) before any
platform admin exists to call the API with.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.enums import (
    EntitlementSource,
    EntitlementStatus,
    MembershipStatus,
    PlatformRole,
    TenantRole,
    TenantStatus,
)
from app.farmos.security import hash_password
from app.plans.models import ModuleCatalog, TenantEntitlement
from app.tenants.models import (
    MembershipFarmAccess,
    MembershipModulePermission,
    PlatformRoleAssignment,
    Tenant,
    TenantMembership,
)


def ensure_user(db: Session, email: str, display_name: str | None = None) -> UserIdentity:
    user = db.execute(select(UserIdentity).where(UserIdentity.email == email)).scalar_one_or_none()
    if user is None:
        user = UserIdentity(idp_subject=email, email=email, display_name=display_name or email)
        db.add(user)
        db.flush()
    return user


def grant_platform_role(db: Session, email: str, role: PlatformRole) -> UserIdentity:
    user = ensure_user(db, email)
    db.add(PlatformRoleAssignment(user_id=user.id, platform_role=role.value))
    db.flush()
    return user


def create_tenant(
    db: Session,
    *,
    company_code: str,
    display_name: str = "Test Farm",
    status: TenantStatus = TenantStatus.ACTIVE,
) -> Tenant:
    tenant = Tenant(
        company_code=company_code,
        legal_name=display_name,
        display_name=display_name,
        country="US",
        status=status,
    )
    db.add(tenant)
    db.flush()
    return tenant


def ensure_module(db: Session, module_code: str) -> ModuleCatalog:
    module = db.get(ModuleCatalog, module_code)
    if module is None:
        module = ModuleCatalog(
            module_code=module_code, name_en=module_code.title(), name_ar=module_code
        )
        db.add(module)
        db.flush()
    return module


def grant_module(
    db: Session, tenant: Tenant, module_code: str, status: EntitlementStatus = EntitlementStatus.ACTIVE
) -> TenantEntitlement:
    ensure_module(db, module_code)
    entitlement = TenantEntitlement(
        tenant_id=tenant.id,
        module_code=module_code,
        status=status,
        source=EntitlementSource.OVERRIDE,
        effective_from=datetime.now(timezone.utc),
    )
    db.add(entitlement)
    db.flush()
    return entitlement


def add_membership(
    db: Session,
    tenant: Tenant,
    email: str,
    *,
    role: TenantRole = TenantRole.EMPLOYEE,
    permissions: list[str] | None = None,
    farm_ids: list[uuid.UUID] | None = None,
) -> TenantMembership:
    user = ensure_user(db, email)
    membership = TenantMembership(
        tenant_id=tenant.id, user_id=user.id, status=MembershipStatus.ACTIVE, tenant_role=role
    )
    db.add(membership)
    db.flush()
    for perm in permissions or []:
        module_code, _, action = perm.partition(":")
        db.add(
            MembershipModulePermission(
                membership_id=membership.id, module_code=module_code, permission_code=action
            )
        )
    for farm_id in farm_ids or []:
        db.add(MembershipFarmAccess(membership_id=membership.id, farm_id=farm_id))
    db.flush()
    return membership


FARMOS_DEMO_PASSWORD = "test-password-123"


def add_farmos_user(
    db: Session,
    tenant: Tenant,
    email: str,
    *,
    role: str = "worker",
    display_name: str | None = None,
    password: str = FARMOS_DEMO_PASSWORD,
    grid: dict[str, list[str]] | None = None,
) -> tuple[UserIdentity, TenantMembership]:
    """Creates a password-enabled FarmOS tablet user. `grid` maps module
    code -> list of allowed actions (from app.farmos.permissions.ACTIONS),
    ignored for role in ("owner", "manager") since those get full_access
    without needing any permission rows at all.
    """
    user = ensure_user(db, email, display_name)
    user.password_hash = hash_password(password)
    membership = TenantMembership(
        tenant_id=tenant.id,
        user_id=user.id,
        status=MembershipStatus.ACTIVE,
        tenant_role=TenantRole.TENANT_OWNER if role == "owner" else TenantRole.EMPLOYEE,
        role=role,
    )
    db.add(membership)
    db.flush()
    for module_code, actions in (grid or {}).items():
        for action in actions:
            db.add(
                MembershipModulePermission(
                    membership_id=membership.id, module_code=module_code, permission_code=action
                )
            )
    db.flush()
    return user, membership
