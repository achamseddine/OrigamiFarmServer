"""Who a farm wants told (MEDICINE-NOTIFICATION-RULES.md §Notification
behavior, CLINICAL-DECISION-SUPPORT §12 "notify manager/vet according to
policy"), and the farm-wide notification bell.

A domain alert is authoritative; routing only decides its recipients. A
farm configures `notification_route` rows per route code — a role, a
person, or both — and the built-in default applies where it has not:
managers and owners for logistics and escalations, veterinarians for the
clinical routes. People live in the control database (tenant
memberships), which the tenant-scoped session cannot see, so this module
opens its own short control session for the lookups; the farm's own
configuration is read from the tenant session when one is given.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.common.db import ControlSessionLocal
from app.common.enums import MembershipStatus
from app.farmos.deps import AccessContext
from app.farmos.farm_models import Notification
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.livestock_service import now_utc
from app.farmos.platform_models import ROUTE_CHANNELS, NotificationRoute
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
ROUTABLE_ROLES = ("owner", "manager", "veterinarian", "worker", "accountant", "staff")


class RoutingError(FeedError):
    pass


@dataclass(frozen=True)
class Recipient:
    user_id: uuid.UUID
    role: str
    name: str


def _members(tenant_id: uuid.UUID, *, roles: tuple[str, ...] = (), user_ids: tuple[uuid.UUID, ...] = ()):
    """Active members of the farm by role and/or by user id, in a stable
    order (role, name)."""
    if not roles and not user_ids:
        return []
    with ControlSessionLocal() as cdb:
        stmt = (
            select(TenantMembership.user_id, TenantMembership.role, UserIdentity.display_name)
            .join(UserIdentity, UserIdentity.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == MembershipStatus.ACTIVE,
            )
            .order_by(TenantMembership.role, UserIdentity.display_name)
        )
        if roles and user_ids:
            stmt = stmt.where(TenantMembership.role.in_(roles) | TenantMembership.user_id.in_(user_ids))
        elif roles:
            stmt = stmt.where(TenantMembership.role.in_(roles))
        else:
            stmt = stmt.where(TenantMembership.user_id.in_(user_ids))
        return cdb.execute(stmt).all()


def configured_routes(db: Session, route_code: str) -> list[NotificationRoute]:
    return list(
        db.scalars(
            select(NotificationRoute)
            .where(
                NotificationRoute.route_code == route_code,
                NotificationRoute.active.is_(True),
                NotificationRoute.deleted_at.is_(None),
            )
            .order_by(NotificationRoute.created_at)
        )
    )


def recipients(tenant_id: uuid.UUID, route_code: str, *, db: Session | None = None) -> list[Recipient]:
    """Active members of the farm the route reaches: the farm's own
    configuration when it has one (explicit people first, then roles),
    else the built-in default. No twins."""
    roles = DEFAULT_ROLES.get(route_code)
    if roles is None:
        raise RoutingError(f"route_code must be one of {list(ROUTE_CODES)}")
    rows = configured_routes(db, route_code) if db is not None else []
    out: list[Recipient] = []
    seen: set[uuid.UUID] = set()

    def add(user_id: uuid.UUID, role: str, name: str) -> None:
        if user_id not in seen:
            seen.add(user_id)
            out.append(Recipient(user_id=user_id, role=role, name=name))

    if rows:
        wanted_users = tuple(r.user_id for r in rows if r.user_id)
        wanted_roles = tuple(sorted({r.role for r in rows if r.role}))
        people = _members(tenant_id, roles=wanted_roles, user_ids=wanted_users)
        for user_id, role, name in people:
            if user_id in wanted_users:
                add(user_id, role, name)
        for user_id, role, name in people:
            if role in wanted_roles:
                add(user_id, role, name)
        return out
    for user_id, role, name in _members(tenant_id, roles=roles):
        add(user_id, role, name)
    return out


def primary_recipient(
    tenant_id: uuid.UUID, route_code: str, *, prefer_role: str | None = None, db: Session | None = None
) -> Recipient | None:
    people = recipients(tenant_id, route_code, db=db)
    if prefer_role:
        for p in people:
            if p.role == prefer_role:
                return p
    return people[0] if people else None


def member(tenant_id: uuid.UUID, user_id: uuid.UUID | None) -> Recipient | None:
    """The active member with this user id, or None."""
    if user_id is None:
        return None
    rows = _members(tenant_id, user_ids=(user_id,))
    return Recipient(user_id=rows[0][0], role=rows[0][1], name=rows[0][2]) if rows else None


def set_routes(
    db: Session, access: AccessContext, route_code: str, entries: list[dict]
) -> list[NotificationRoute]:
    """Replaces the farm's configuration for one route. An empty list
    returns the route to the built-in default. The replaced rows stay,
    inactive — who was told what, when, remains answerable."""
    if route_code not in ROUTE_CODES:
        raise RoutingError(f"route_code must be one of {list(ROUTE_CODES)}")
    at = now_utc()
    for r in configured_routes(db, route_code):
        r.active = False
        r.replaced_at = at
        r.last_modified_by = access.membership_id
    out = []
    for e in entries:
        role, target = e.get("role"), e.get("user_id")
        channel = e.get("channel") or "in_app"
        if not role and not target:
            raise RoutingError("Each route entry names a role or a user.")
        if role and role not in ROUTABLE_ROLES:
            raise RoutingError(f"role must be one of {list(ROUTABLE_ROLES)}")
        if channel not in ROUTE_CHANNELS:
            raise RoutingError(
                f"channel must be one of {list(ROUTE_CHANNELS)} (push, email and sms come later)."
            )
        user_pk = None
        if target:
            user_pk = parse_uuid(target)
            if user_pk is None or member(access.tenant_id, user_pk) is None:
                raise RoutingError("User not found on this farm", 404)
        row = NotificationRoute(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            route_code=route_code,
            role=role,
            user_id=user_pk,
            channel=channel,
            active=True,
            created_by=access.user_id,
        )
        db.add(row)
        out.append(row)
    db.flush()
    return out


def route_dict(db: Session, tenant_id: uuid.UUID, route_code: str) -> dict:
    rows = configured_routes(db, route_code)
    return {
        "route_code": route_code,
        "configured": [
            {
                "id": str(r.id),
                "role": r.role,
                "user_id": str(r.user_id) if r.user_id else None,
                "channel": r.channel,
            }
            for r in rows
        ],
        "default_roles": list(DEFAULT_ROLES[route_code]),
        "using_default": not rows,
        "effective_recipients": [
            {"id": str(p.user_id), "name": p.name, "role": p.role}
            for p in recipients(tenant_id, route_code, db=db)
        ],
    }


def routes_overview(db: Session, tenant_id: uuid.UUID) -> list[dict]:
    return [route_dict(db, tenant_id, code) for code in ROUTE_CODES]


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


def clear_bell(db: Session, *, source_type: str, source_id: str, at: datetime) -> None:
    for n in db.scalars(
        select(Notification).where(
            Notification.source_type == source_type,
            Notification.source_id == source_id,
            Notification.read_at.is_(None),
        )
    ):
        n.read_at = at
