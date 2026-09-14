"""What a plan contains, and what putting a tenant on one actually does.

`plan_module` has existed since the first migration and nothing ever read
or wrote it, which left a plan as a name with a price and no contents:
choosing one for a customer granted them nothing, and the console's
"Plans & modules" screen showed two lists with no stated relationship.
This module closes that gap — a plan is a named bundle of modules at a
price, and subscribing a tenant to it grants those modules.

Two deliberate asymmetries in `apply_plan_to_tenant`:

Granting is automatic, revoking is not. Moving a customer to a smaller
plan does not switch off what they are already using — a farm midway
through a season would lose the module it records milk with, because
somebody edited a price list. The extras are reported instead, so the
decision to withdraw one stays a decision somebody makes.

An entitlement granted by hand is left alone. `EntitlementSource`
already separates PLAN from OVERRIDE; an override is somebody's explicit
"yes, this tenant, regardless of their plan", and re-running the plan
must not quietly downgrade that record of intent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common.enums import ActorType, EntitlementSource, EntitlementStatus
from app.entitlements.state_machine import transition_entitlement
from app.plans.models import ModuleCatalog, Plan, PlanModule, TenantEntitlement


@dataclass
class PlanApplication:
    """What subscribing a tenant to a plan changed, and what it left alone."""

    granted: list[str] = field(default_factory=list)
    already_active: list[str] = field(default_factory=list)
    # Modules the tenant holds that this plan does not include. Never
    # revoked here — see the module docstring.
    not_in_plan: list[str] = field(default_factory=list)


def plan_module_codes(db: Session, plan_id: uuid.UUID) -> list[str]:
    return list(
        db.execute(
            select(PlanModule.module_code)
            .where(PlanModule.plan_id == plan_id, PlanModule.included.is_(True))
            .order_by(PlanModule.module_code)
        ).scalars()
    )


def set_plan_modules(db: Session, plan: Plan, module_codes: list[str]) -> list[str]:
    """Replaces a plan's contents wholesale and returns the stored set.

    Replace rather than merge: the console sends the ticked boxes, and a
    merge would make unticking a box do nothing at all.
    """
    wanted = sorted(set(module_codes))

    unknown = [
        code for code in wanted if db.get(ModuleCatalog, code) is None
    ]
    if unknown:
        raise ValueError(f"Unknown module(s): {', '.join(unknown)}")

    existing = {
        row.module_code: row
        for row in db.execute(
            select(PlanModule).where(PlanModule.plan_id == plan.id)
        ).scalars()
    }

    for code in wanted:
        row = existing.get(code)
        if row is None:
            db.add(PlanModule(plan_id=plan.id, module_code=code, included=True))
        else:
            row.included = True
    for code, row in existing.items():
        if code not in wanted:
            db.delete(row)

    db.flush()
    return wanted


def apply_plan_to_tenant(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    plan: Plan,
    actor_id: uuid.UUID | None,
    reason: str,
) -> PlanApplication:
    """Grants every module this plan includes, and reports the rest."""
    included = plan_module_codes(db, plan.id)
    result = PlanApplication()

    entitlements = {
        row.module_code: row
        for row in db.execute(
            select(TenantEntitlement).where(TenantEntitlement.tenant_id == tenant_id)
        ).scalars()
    }

    for module_code in included:
        entitlement = entitlements.get(module_code)
        if entitlement is not None and entitlement.status in (
            EntitlementStatus.ACTIVE,
            EntitlementStatus.TRIAL,
        ):
            result.already_active.append(module_code)
            continue

        if entitlement is None:
            entitlement = TenantEntitlement(
                tenant_id=tenant_id,
                module_code=module_code,
                status=EntitlementStatus.INACTIVE,
                source=EntitlementSource.PLAN,
                effective_from=datetime.now(timezone.utc),
            )
            db.add(entitlement)
            db.flush()

        transition_entitlement(
            db,
            entitlement,
            EntitlementStatus.ACTIVE,
            actor_id=actor_id,
            actor_type=ActorType.PLATFORM_USER,
            reason=reason,
            effective_from=datetime.now(timezone.utc),
        )
        # Only a module this plan actually put there is marked as the
        # plan's doing; an override that happened to be inactive keeps its
        # own provenance.
        if entitlement.source != EntitlementSource.OVERRIDE:
            entitlement.source = EntitlementSource.PLAN
        result.granted.append(module_code)

    for module_code, entitlement in sorted(entitlements.items()):
        if module_code not in included and entitlement.status in (
            EntitlementStatus.ACTIVE,
            EntitlementStatus.TRIAL,
        ):
            result.not_in_plan.append(module_code)

    db.flush()
    return result
