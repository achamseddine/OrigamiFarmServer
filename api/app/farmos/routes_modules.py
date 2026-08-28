"""GET /modules (this farm's own licence rows) and POST
/modules/{module_code}/activate. A super user is not necessarily scoped to
one farm on the platform side, so the target farm is an explicit query
param here too — defaulting to the caller's own farm, which is the only
farm a FarmOS-authenticated user can ever act on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.db import get_control_db
from app.common.enums import ActorType, EntitlementSource, EntitlementStatus
from app.entitlements.state_machine import transition_entitlement
from app.farmos.deps import AccessContext, check_farm_id, require_owner_or_manager
from app.farmos.schemas import ModuleLicenseOut, ModuleLicenseUpdate
from app.plans.models import ModuleCatalog, TenantEntitlement

router = APIRouter()


def _to_license_out(e: TenantEntitlement) -> ModuleLicenseOut:
    return ModuleLicenseOut(
        id=str(e.id),
        farm_id=str(e.tenant_id),
        module_code=e.module_code,
        status=e.status.value.lower(),
        plan=e.plan or "",
        starts_at=e.effective_from.isoformat() if e.effective_from else None,
        expires_at=e.effective_until.isoformat() if e.effective_until else None,
        max_users=e.max_users,
        max_products=e.max_products,
    )


@router.get("/modules", response_model=list[ModuleLicenseOut])
def list_modules(
    access: AccessContext = Depends(require_owner_or_manager),
    db: Session = Depends(get_control_db),
) -> list[ModuleLicenseOut]:
    rows = db.execute(
        select(TenantEntitlement).where(TenantEntitlement.tenant_id == access.tenant_id)
    ).scalars().all()
    return [_to_license_out(row) for row in rows]


@router.post("/modules/{module_code}/activate", response_model=ModuleLicenseOut)
def activate_module(
    module_code: str,
    payload: ModuleLicenseUpdate,
    farm_id: str | None = Query(default=None),
    access: AccessContext = Depends(require_owner_or_manager),
    db: Session = Depends(get_control_db),
) -> ModuleLicenseOut:
    if farm_id is not None:
        check_farm_id(farm_id, access)

    module = db.get(ModuleCatalog, module_code)
    if module is None:
        module = ModuleCatalog(module_code=module_code, name_en=module_code.title(), name_ar=module_code)
        db.add(module)
        db.flush()

    entitlement = db.execute(
        select(TenantEntitlement).where(
            TenantEntitlement.tenant_id == access.tenant_id,
            TenantEntitlement.module_code == module_code,
        )
    ).scalar_one_or_none()
    if entitlement is None:
        entitlement = TenantEntitlement(
            tenant_id=access.tenant_id,
            module_code=module_code,
            status=EntitlementStatus.INACTIVE,
            source=EntitlementSource.OVERRIDE,
            effective_from=datetime.now(timezone.utc),
        )
        db.add(entitlement)
        db.flush()

    new_status = EntitlementStatus(payload.status.upper())
    transition_entitlement(
        db,
        entitlement,
        new_status,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        reason=f"Self-service activation by {access.role}",
        effective_from=datetime.now(timezone.utc),
        effective_until=(
            datetime.fromisoformat(payload.expires_at) if payload.expires_at else None
        ),
    )
    entitlement.plan = payload.plan
    entitlement.max_users = payload.max_users
    entitlement.max_products = payload.max_products
    db.flush()
    return _to_license_out(entitlement)
