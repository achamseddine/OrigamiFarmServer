"""The single place that answers "is tenant X allowed to use module Y right
now?". GET /api/v1/me/entitlements and every require_module() check in
app/auth/dependencies.py both read through here — the client's cached copy
of this response is for UI rendering only, never a security boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import ENTITLEMENT_EFFECTIVE_STATUSES, EntitlementStatus, TenantStatus
from app.plans.models import TenantEntitlement
from app.tenants.models import Tenant


class EntitlementService:
    def __init__(self, db: Session):
        self._db = db

    def _tenant_allows_modules(self, tenant: Tenant | None) -> bool:
        if tenant is None:
            return False
        # Suspended/terminated tenants lose module access even if an
        # entitlement row is still marked ACTIVE — safe-mode read access
        # to the tenant's own data is handled separately at the API layer,
        # not by pretending the module is entitled.
        return tenant.status in {
            TenantStatus.ONBOARDING,
            TenantStatus.TRIAL,
            TenantStatus.ACTIVE,
            TenantStatus.GRACE,
        }

    def _current_entitlement(self, tenant_id: uuid.UUID, module_code: str) -> TenantEntitlement | None:
        return self._db.execute(
            select(TenantEntitlement).where(
                TenantEntitlement.tenant_id == tenant_id,
                TenantEntitlement.module_code == module_code,
            )
        ).scalar_one_or_none()

    def is_module_active(self, tenant_id: uuid.UUID, module_code: str) -> bool:
        tenant = self._db.get(Tenant, tenant_id)
        if not self._tenant_allows_modules(tenant):
            return False

        entitlement = self._current_entitlement(tenant_id, module_code)
        if entitlement is None or entitlement.status not in ENTITLEMENT_EFFECTIVE_STATUSES:
            return False

        now = datetime.now(timezone.utc)
        if entitlement.effective_from and entitlement.effective_from > now:
            return False
        if entitlement.effective_until and entitlement.effective_until <= now:
            return False
        return True

    def effective_entitlements(self, tenant_id: uuid.UUID) -> dict[str, dict]:
        rows = self._db.execute(
            select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
        ).scalars().all()
        return {
            row.module_code: {
                "enabled": self.is_module_active(tenant_id, row.module_code),
                "status": row.status.value if isinstance(row.status, EntitlementStatus) else row.status,
                "effective_from": row.effective_from.isoformat() if row.effective_from else None,
                "effective_until": row.effective_until.isoformat() if row.effective_until else None,
            }
            for row in rows
        }
