"""Dashboard endpoints.

Split by cost, because the difference matters to whoever calls them:
/overview and /licensing are control-plane aggregates and cheap, while
/usage opens one RLS-scoped session per tenant and is capped for that
reason.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.dependencies import require_platform_role
from app.auth.models import UserIdentity
from app.auth.schemas import Identity
from app.common.db import get_control_db
from app.common.enums import (
    DeviceStatus,
    EntitlementStatus,
    MembershipStatus,
    PlatformRole,
    TenantStatus,
)
from app.common.errors import AppError, ErrorCode
from app.devices.models import Device, LicenseLease
from app.plans.models import ModuleCatalog, Subscription, TenantEntitlement
from app.platform.metrics import (
    MODULE_TABLES,
    audit_events_per_day,
    tenant_farm_data_usage,
    tenants_created_per_month,
)
from app.tenants.models import PlatformRoleAssignment, Tenant, TenantMembership

router = APIRouter()

_ANY_PLATFORM_ROLE = (
    PlatformRole.PLATFORM_SUPER_ADMIN,
    PlatformRole.PLATFORM_COMMERCIAL_ADMIN,
    PlatformRole.PLATFORM_SUPPORT_ADMIN,
    PlatformRole.PLATFORM_AUDITOR,
)

# Farm-data usage costs one session per tenant, so the cross-tenant view is
# bounded rather than silently slow. The response says how many were left
# out so the console can be honest about it too.
USAGE_TENANT_CAP = 50


class TenantUsageOut(BaseModel):
    tenant_id: uuid.UUID
    company_code: str
    display_name: str
    status: TenantStatus
    total_records: int
    records_by_module: dict[str, int]
    modules_with_data: list[str]
    modules_entitled: list[str]
    last_activity_at: datetime | None
    active_devices: int
    active_users: int


class UsageListOut(BaseModel):
    items: list[TenantUsageOut]
    tenants_total: int
    tenants_measured: int


def _entitled_modules(db: Session, tenant_id: uuid.UUID) -> list[str]:
    return list(
        db.execute(
            select(TenantEntitlement.module_code)
            .where(
                TenantEntitlement.tenant_id == tenant_id,
                TenantEntitlement.status.in_(
                    [EntitlementStatus.ACTIVE, EntitlementStatus.TRIAL]
                ),
            )
            .order_by(TenantEntitlement.module_code)
        ).scalars()
    )


def _tenant_usage(db: Session, tenant: Tenant) -> TenantUsageOut:
    usage = tenant_farm_data_usage(tenant.id)
    active_devices = db.execute(
        select(func.count())
        .select_from(Device)
        .where(Device.tenant_id == tenant.id, Device.status == DeviceStatus.ACTIVE)
    ).scalar_one()
    active_users = db.execute(
        select(func.count())
        .select_from(TenantMembership)
        .where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.status == MembershipStatus.ACTIVE,
        )
    ).scalar_one()

    return TenantUsageOut(
        tenant_id=tenant.id,
        company_code=tenant.company_code,
        display_name=tenant.display_name,
        status=tenant.status,
        modules_entitled=_entitled_modules(db, tenant.id),
        active_devices=active_devices,
        active_users=active_users,
        **usage,
    )


@router.get("/metrics/overview")
def metrics_overview(
    audit_days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> dict:
    """Platform-wide state: who exists, what is licensed, what is running."""
    now = datetime.now(timezone.utc)

    tenants_by_status = {
        status.value: db.execute(
            select(func.count()).select_from(Tenant).where(Tenant.status == status)
        ).scalar_one()
        for status in TenantStatus
    }
    devices_by_status = {
        status.value: db.execute(
            select(func.count()).select_from(Device).where(Device.status == status)
        ).scalar_one()
        for status in DeviceStatus
    }

    leases_active = db.execute(
        select(func.count())
        .select_from(LicenseLease)
        .where(LicenseLease.revoked_at.is_(None), LicenseLease.expires_at > now)
    ).scalar_one()
    leases_expiring = db.execute(
        select(func.count())
        .select_from(LicenseLease)
        .where(
            LicenseLease.revoked_at.is_(None),
            LicenseLease.expires_at > now,
            LicenseLease.expires_at <= now + timedelta(days=7),
        )
    ).scalar_one()

    staff_count = db.execute(
        select(func.count(func.distinct(PlatformRoleAssignment.user_id)))
    ).scalar_one()
    user_count = db.execute(select(func.count()).select_from(UserIdentity)).scalar_one()

    renewals_due = db.execute(
        select(func.count())
        .select_from(Subscription)
        .where(Subscription.renews_at.is_not(None), Subscription.renews_at <= now + timedelta(days=30))
    ).scalar_one()

    return {
        "generated_at": now.isoformat(),
        "tenants_by_status": tenants_by_status,
        "tenants_total": sum(tenants_by_status.values()),
        "devices_by_status": devices_by_status,
        "devices_total": sum(devices_by_status.values()),
        "leases_active": leases_active,
        "leases_expiring_7d": leases_expiring,
        "staff_count": staff_count,
        "user_count": user_count,
        "renewals_due_30d": renewals_due,
        "audit_events_per_day": audit_events_per_day(db, audit_days),
        "tenants_created_per_month": tenants_created_per_month(db, 6),
    }


@router.get("/metrics/licensing")
def metrics_licensing(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> dict:
    """Which modules are licensed to whom, and the state of issued leases."""
    now = datetime.now(timezone.utc)
    tenants_total = db.execute(select(func.count()).select_from(Tenant)).scalar_one()

    catalog = db.execute(select(ModuleCatalog).order_by(ModuleCatalog.module_code)).scalars().all()
    names = {module.module_code: module.name_en for module in catalog}
    license_codes = {module.module_code: module.license_code for module in catalog}

    counts = db.execute(
        select(TenantEntitlement.module_code, TenantEntitlement.status, func.count())
        .group_by(TenantEntitlement.module_code, TenantEntitlement.status)
    ).all()

    by_module: dict[str, dict] = {}
    for module_code, status, count in counts:
        entry = by_module.setdefault(
            module_code,
            {
                "module_code": module_code,
                "name": names.get(module_code, module_code),
                "license_code": license_codes.get(module_code),
                "is_permission_module": module_code in MODULE_TABLES,
                "active": 0,
                "trial": 0,
                "other": 0,
            },
        )
        if status == EntitlementStatus.ACTIVE:
            entry["active"] += count
        elif status == EntitlementStatus.TRIAL:
            entry["trial"] += count
        else:
            entry["other"] += count

    modules = sorted(
        by_module.values(), key=lambda entry: (-(entry["active"] + entry["trial"]), entry["module_code"])
    )

    expiring = db.execute(
        select(LicenseLease, Tenant.display_name, Device.display_name)
        .join(Tenant, Tenant.id == LicenseLease.tenant_id)
        .outerjoin(Device, Device.id == LicenseLease.device_id)
        .where(
            LicenseLease.revoked_at.is_(None),
            LicenseLease.expires_at > now,
            LicenseLease.expires_at <= now + timedelta(days=7),
        )
        .order_by(LicenseLease.expires_at)
        .limit(25)
    ).all()

    return {
        "generated_at": now.isoformat(),
        "tenants_total": tenants_total,
        "modules": modules,
        "leases_expiring_soon": [
            {
                "lease_id": str(lease.id),
                "tenant_id": str(lease.tenant_id),
                "tenant_name": tenant_name,
                "device_name": device_name,
                "expires_at": lease.expires_at.isoformat(),
                "modules": list(lease.modules or []),
            }
            for lease, tenant_name, device_name in expiring
        ],
    }


@router.get("/metrics/usage", response_model=UsageListOut)
def metrics_usage(
    limit: int = Query(default=USAGE_TENANT_CAP, ge=1, le=USAGE_TENANT_CAP),
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> UsageListOut:
    """What each tenant actually holds, newest tenants first.

    One RLS-scoped session per tenant, so this is the expensive endpoint —
    hence the cap, and hence tenants_measured being reported separately
    from tenants_total.
    """
    tenants_total = db.execute(select(func.count()).select_from(Tenant)).scalar_one()
    tenants = db.execute(
        select(Tenant).order_by(Tenant.created_at.desc()).limit(limit)
    ).scalars().all()

    items = [_tenant_usage(db, tenant) for tenant in tenants]
    items.sort(key=lambda item: item.total_records, reverse=True)

    return UsageListOut(items=items, tenants_total=tenants_total, tenants_measured=len(items))


@router.get("/metrics/usage/{tenant_id}", response_model=TenantUsageOut)
def metrics_usage_for_tenant(
    tenant_id: uuid.UUID,
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(require_platform_role(*_ANY_PLATFORM_ROLE)),
) -> TenantUsageOut:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise AppError(ErrorCode.NOT_FOUND)
    return _tenant_usage(db, tenant)
