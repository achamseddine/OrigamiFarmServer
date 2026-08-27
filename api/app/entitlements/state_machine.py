"""Explicit state machines for tenant/subscription and module entitlement
status. Every transition is validated against the allowed-transitions map
and audited with actor + reason — see LICENSE_ENTITLEMENTS.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import TENANT_STATUS_TRANSITIONS, ActorType, EntitlementStatus, TenantStatus
from app.common.errors import AppError, ErrorCode
from app.plans.models import TenantEntitlement
from app.tenants.models import Tenant

# Module entitlement transitions. Deactivation never implies data deletion
# — see app/tenant_api for how deleted_at tombstones are handled
# independently of entitlement state.
ENTITLEMENT_TRANSITIONS: dict[EntitlementStatus, set[EntitlementStatus]] = {
    EntitlementStatus.INACTIVE: {EntitlementStatus.TRIAL, EntitlementStatus.ACTIVE},
    EntitlementStatus.TRIAL: {
        EntitlementStatus.ACTIVE,
        EntitlementStatus.EXPIRED,
        EntitlementStatus.INACTIVE,
    },
    EntitlementStatus.ACTIVE: {
        EntitlementStatus.SCHEDULED_DISABLE,
        EntitlementStatus.SUSPENDED,
        EntitlementStatus.INACTIVE,
    },
    EntitlementStatus.SCHEDULED_DISABLE: {EntitlementStatus.INACTIVE, EntitlementStatus.ACTIVE},
    EntitlementStatus.SUSPENDED: {EntitlementStatus.ACTIVE, EntitlementStatus.INACTIVE},
    EntitlementStatus.EXPIRED: {
        EntitlementStatus.ACTIVE,
        EntitlementStatus.TRIAL,
        EntitlementStatus.INACTIVE,
    },
}


def transition_tenant_status(
    db: Session,
    tenant: Tenant,
    new_status: TenantStatus,
    *,
    actor_id: uuid.UUID | None,
    actor_type: ActorType,
    reason: str,
) -> Tenant:
    if new_status == tenant.status:
        return tenant
    allowed = TENANT_STATUS_TRANSITIONS.get(tenant.status, set())
    if new_status not in allowed:
        raise AppError(
            ErrorCode.CONFLICT,
            f"Cannot transition tenant from {tenant.status.value} to {new_status.value}",
        )

    before = {"status": tenant.status.value}
    tenant.status = new_status
    db.flush()
    record_audit_event(
        db,
        actor_id=actor_id,
        actor_type=actor_type,
        tenant_id=tenant.id,
        action="tenant.status_changed",
        entity_type="tenant",
        entity_id=str(tenant.id),
        before=before,
        after={"status": new_status.value},
        reason=reason,
    )
    return tenant


def transition_entitlement(
    db: Session,
    entitlement: TenantEntitlement,
    new_status: EntitlementStatus,
    *,
    actor_id: uuid.UUID | None,
    actor_type: ActorType,
    reason: str,
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
) -> TenantEntitlement:
    if new_status != entitlement.status:
        allowed = ENTITLEMENT_TRANSITIONS.get(entitlement.status, set())
        if new_status not in allowed:
            raise AppError(
                ErrorCode.CONFLICT,
                f"Cannot transition module {entitlement.module_code} from "
                f"{entitlement.status.value} to {new_status.value}",
            )

    before = {
        "status": entitlement.status.value,
        "effective_until": entitlement.effective_until.isoformat()
        if entitlement.effective_until
        else None,
    }
    entitlement.status = new_status
    entitlement.changed_by = actor_id
    entitlement.reason = reason
    if effective_from is not None:
        entitlement.effective_from = effective_from
    if effective_until is not None:
        entitlement.effective_until = effective_until
    db.flush()
    record_audit_event(
        db,
        actor_id=actor_id,
        actor_type=actor_type,
        tenant_id=entitlement.tenant_id,
        action="entitlement.status_changed",
        entity_type="tenant_entitlement",
        entity_id=str(entitlement.id),
        before=before,
        after={
            "status": new_status.value,
            "effective_until": entitlement.effective_until.isoformat()
            if entitlement.effective_until
            else None,
        },
        reason=reason,
    )
    return entitlement
