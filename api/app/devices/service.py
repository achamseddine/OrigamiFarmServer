from __future__ import annotations

import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import EntitlementStatus
from app.devices.models import Device
from app.entitlements.service import EntitlementService
from app.plans.models import TenantEntitlement
from app.tenants.models import Farm


def hash_activation_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def generate_activation_code() -> str:
    # Human-typeable if needed, but designed primarily for QR encoding.
    return secrets.token_urlsafe(9)


def active_modules_for_tenant(db: Session, tenant_id: uuid.UUID) -> list[str]:
    entitlements = EntitlementService(db)
    codes = db.execute(
        select(TenantEntitlement.module_code).where(
            TenantEntitlement.tenant_id == tenant_id,
            TenantEntitlement.status.in_(
                [EntitlementStatus.ACTIVE, EntitlementStatus.TRIAL]
            ),
        )
    ).scalars().all()
    return [code for code in codes if entitlements.is_module_active(tenant_id, code)]


def farm_ids_for_device(db: Session, device: Device) -> list[uuid.UUID]:
    if device.farm_id:
        return [device.farm_id]
    return list(
        db.execute(select(Farm.id).where(Farm.tenant_id == device.tenant_id, Farm.active.is_(True)))
        .scalars()
        .all()
    )


def permission_profile_hash_for(modules: list[str], farm_ids: list[uuid.UUID]) -> str:
    fingerprint = "|".join(sorted(modules)) + "::" + "|".join(sorted(str(f) for f in farm_ids))
    return hashlib.sha256(fingerprint.encode()).hexdigest()
