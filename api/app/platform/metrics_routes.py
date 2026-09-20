"""Dashboard endpoints.

Split by cost, because the difference matters to whoever calls them:
/overview and /revenue are control-plane aggregates and cheap, while
/usage opens one RLS-scoped session per tenant and is capped for that
reason.

/metrics/licensing is gone. It answered "which customers have bought
which modules, and which tablets can still work offline" — both questions
the product stopped having when it became one subscription covering
everything, sold to tablets that simply sign in.
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
    MembershipStatus,
    PlatformRole,
    TenantStatus,
)
from app.common.errors import AppError, ErrorCode
from app.devices.models import Device
from app.plans.models import Subscription
from app.platform.metrics import (
    audit_events_per_day,
    tenant_farm_data_usage,
    tenants_created_per_month,
)
from app.platform.revenue import invoice_totals, revenue_snapshot
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
    last_activity_at: datetime | None
    active_devices: int
    active_users: int


class UsageListOut(BaseModel):
    items: list[TenantUsageOut]
    tenants_total: int
    tenants_measured: int


# What a tenant was entitled to used to be reported here, beside what they
# had actually recorded, so an operator could see a farm paying for a module
# it never opened. Every farm now has every module, so the comparison is
# between a list and itself.


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
    """Platform-wide state: who exists, and what is running."""
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
        "staff_count": staff_count,
        "user_count": user_count,
        "renewals_due_30d": renewals_due,
        "audit_events_per_day": audit_events_per_day(db, audit_days),
        "tenants_created_per_month": tenants_created_per_month(db, 6),
    }


@router.get("/metrics/revenue")
def metrics_revenue(
    db: Session = Depends(get_control_db),
    _identity: Identity = Depends(
        require_platform_role(
            PlatformRole.PLATFORM_SUPER_ADMIN,
            PlatformRole.PLATFORM_COMMERCIAL_ADMIN,
            PlatformRole.PLATFORM_AUDITOR,
        )
    ),
) -> dict:
    """Recurring revenue and the commercial pipeline behind it.

    Support admins are excluded: troubleshooting a tenant's data does not
    require knowing what the business charges.
    """
    snapshot = revenue_snapshot(db)
    now = datetime.now(timezone.utc)

    tenants_by_month = tenants_created_per_month(db, 12)

    return {
        "generated_at": now.isoformat(),
        "currency": snapshot.currency,
        "currencies_present": snapshot.currencies_present,
        "mrr_cents": snapshot.mrr_cents,
        "arr_cents": snapshot.arr_cents,
        "arpa_cents": round(snapshot.mrr_cents / snapshot.paying_tenants)
        if snapshot.paying_tenants
        else 0,
        "paying_tenants": snapshot.paying_tenants,
        "trial_tenants": snapshot.trial_tenants,
        "at_risk_tenants": snapshot.at_risk_tenants,
        "at_risk_mrr_cents": snapshot.at_risk_mrr_cents,
        "lost_tenants": snapshot.lost_tenants,
        "renewals_due_30d": snapshot.renewals_due_30d,
        "renewals_due_30d_mrr_cents": snapshot.renewals_due_30d_mrr_cents,
        # The two numbers that say whether this report can be trusted as a
        # complete picture of the book.
        "unpriced_subscriptions": snapshot.unpriced_subscriptions,
        "tenants_without_subscription": snapshot.tenants_without_subscription,
        "by_plan": [
            {
                "plan_code": entry.plan_code,
                "plan_name": entry.plan_name,
                "currency": entry.currency,
                "monthly_price_cents": entry.monthly_price_cents,
                "annual_price_cents": entry.annual_price_cents,
                "subscriptions": entry.subscriptions,
                "mrr_cents": entry.mrr_cents,
                "unpriced_subscriptions": entry.unpriced_subscriptions,
            }
            for entry in snapshot.by_plan
        ],
        "tenants_created_per_month": tenants_by_month,
        "invoicing": invoice_totals(db),
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
