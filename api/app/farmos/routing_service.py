"""Who a farm wants told (MEDICINE-NOTIFICATION-RULES.md §Notification
behavior, CLINICAL-DECISION-SUPPORT §12 "notify manager/vet according to
policy"), and the farm-wide notification bell.

A domain alert is authoritative; routing only decides its recipients.
People live in the control database (tenant memberships), which the
tenant-scoped session cannot see, so this module opens its own short
control session for the lookups. The built-in defaults apply: managers
and owners for logistics and escalations, veterinarians for the clinical
routes. A farm's own `notification_routes` configuration is the platform
stage.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import ControlSessionLocal
from app.common.enums import MembershipStatus
from app.farmos.farm_models import Notification
from app.tenants.models import TenantMembership

DEFAULT_ROLES: dict[str, tuple[str, ...]] = {
    "pharmacy_stock": ("manager", "owner"),
    "pharmacy_critical": ("manager", "owner"),
    "pharmacy_expiry": ("manager", "owner"),
    "emergency_escalation": ("manager", "owner", "veterinarian"),
    "emergency_manager": ("manager", "owner"),
    "emergency_veterinarian": ("veterinarian",),
    "emergency_reassessment": ("manager", "owner"),
    "feed_performance": ("manager", "owner"),
    "procurement_requisition": ("manager", "owner"),
}
ROUTE_CODES = tuple(DEFAULT_ROLES)


@dataclass(frozen=True)
class Recipient:
    user_id: uuid.UUID
    role: str
    name: str


def recipients(tenant_id: uuid.UUID, route_code: str) -> list[Recipient]:
    """Active members of the farm the route reaches, by role, no twins."""
    roles = DEFAULT_ROLES.get(route_code)
    if roles is None:
        raise ValueError(f"route_code must be one of {list(ROUTE_CODES)}")
    with ControlSessionLocal() as cdb:
        rows = cdb.execute(
            select(TenantMembership.user_id, TenantMembership.role, UserIdentity.display_name)
            .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
                TenantMembership.role.in_(roles),
            )
            .order_by(TenantMembership.role, UserIdentity.display_name)
        ).all()
    out: list[Recipient] = []
    seen: set[uuid.UUID] = set()
    for user_id, role, name in rows:
        if user_id not in seen:
            seen.add(user_id)
            out.append(Recipient(user_id=user_id, role=role, name=name))
    return out


def primary_recipient(
    tenant_id: uuid.UUID, route_code: str, *, prefer_role: str | None = None
) -> Recipient | None:
    people = recipients(tenant_id, route_code)
    if prefer_role:
        for p in people:
            if p.role == prefer_role:
                return p
    return people[0] if people else None


def member(tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> Recipient | None:
    """The active member with this user id, or None."""
    if user_id is None:
        return None
    with ControlSessionLocal() as cdb:
        row = cdb.execute(
            select(TenantMembership.user_id, TenantMembership.role, UserIdentity.display_name)
            .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
        ).first()
    return Recipient(user_id=row[0], role=row[1], name=row[2]) if row else None


class NameCache:
    """Display names for user ids, resolved once per request from the
    control database (the views name who approved, administered, diagnosed)."""

    def __init__(self) -> None:
        self._names: dict[uuid.UUID, str | None] = {}

    def get(self, user_id: uuid.UUID | str | None) -> str | None:
        if user_id is None:
            return None
        try:
            pk = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))
        except ValueError:
            return None
        if pk not in self._names:
            with ControlSessionLocal() as cdb:
                self._names[pk] = cdb.scalar(select(UserIdentity.display_name).where(UserIdentity.id == pk))
        return self._names[pk]


# ------------------------------------------------------------------ bell
def notify_bell(
    db: Session,
    tenant_id: uuid.UUID,
    *,
    module_code: str,
    notification_type: str,
    title: str,
    description: str | None,
    priority: str,
    entity_type: str | None,
    entity_id: str | None,
    source_type: str,
    source_id: str,
    actor: uuid.UUID | None = None,
) -> Notification | None:
    """One unread bell entry per (source, type); a repeat refreshes it."""
    existing = db.scalar(
        select(Notification).where(
            Notification.source_type == source_type,
            Notification.source_id == source_id,
            Notification.notification_type == notification_type,
            Notification.read_at.is_(None),
            Notification.deleted_at.is_(None),
        )
    )
    if existing is not None:
        existing.title = title[:300]
        existing.description = (description or "")[:1000] or None
        existing.priority = priority
        return existing
    row = Notification(
        tenant_id=tenant_id,
        last_modified_by=actor,
        module_code=module_code,
        notification_type=notification_type,
        title=title[:300],
        description=(description or "")[:1000] or None,
        priority=priority,
        entity_type=entity_type,
        entity_id=entity_id,
        source_type=source_type,
        source_id=source_id,
    )
    db.add(row)
    return row


def clear_bell(db: Session, *, source_type: str, source_id: str, at) -> None:
    for n in db.scalars(
        select(Notification).where(
            Notification.source_type == source_type,
            Notification.source_id == source_id,
            Notification.read_at.is_(None),
        )
    ):
        n.read_at = at
