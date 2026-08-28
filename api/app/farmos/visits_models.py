"""Farm-data-plane (RLS-protected, tenant-scoped) tables for the Farm
Visits / agritourism module (a licensed add-on — see
app/farmos/routes_modules.py). Field names match docs/FARMOS_API.md
exactly.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class VisitActivity(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "visit_activity"

    name: Mapped[str] = mapped_column()
    activity_type: Mapped[str] = mapped_column(String(32), default="other")
    price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    capacity_per_slot: Mapped[int] = mapped_column(default=1)
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    requires_staff_role: Mapped[str | None] = mapped_column(nullable=True)
    requires_animal_id: Mapped[str | None] = mapped_column(nullable=True)
    welfare_limit_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class VisitPackage(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "visit_package"

    name: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(nullable=True)
    base_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    duration_minutes: Mapped[int | None] = mapped_column(nullable=True)
    included_items_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class VisitorProfile(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "visitor_profile"

    full_name: Mapped[str] = mapped_column()
    phone: Mapped[str | None] = mapped_column(nullable=True)
    email: Mapped[str | None] = mapped_column(nullable=True)
    preferred_language: Mapped[str] = mapped_column(String(8), default="en")
    notes: Mapped[str | None] = mapped_column(nullable=True)
    consent_marketing: Mapped[bool] = mapped_column(Boolean, default=False)


class VisitSession(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "visit_session"

    date: Mapped[date] = mapped_column()
    start_time: Mapped[str] = mapped_column(String(8))
    end_time: Mapped[str] = mapped_column(String(8))
    capacity: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(32), default="open")
    weather_note: Mapped[str | None] = mapped_column(nullable=True)
    expected_staff_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)


class VisitBooking(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "visit_booking"

    visitor_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    package_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    adults: Mapped[int] = mapped_column(default=1)
    children: Mapped[int] = mapped_column(default=0)
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    deposit_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    balance_due: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    payment_method: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(nullable=True, index=True)


class BookingActivity(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visit_booking_activity"

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    activity_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    quantity: Mapped[int] = mapped_column(default=1)
    unit_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total_price: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


class VisitCost(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visit_cost"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    category: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(10, 2))
    allocation_method: Mapped[str] = mapped_column(String(32), default="per_session")


class VisitIncident(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visit_incident"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    incident_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16), default="low")
    description: Mapped[str] = mapped_column()
    action_taken: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column()


class VisitStaffRosterEntry(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visit_staff_roster"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    worker_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    role: Mapped[str] = mapped_column(String(64))
    start_time: Mapped[str] = mapped_column(String(8))
    end_time: Mapped[str] = mapped_column(String(8))
    hourly_rate: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    total_cost: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)


class VisitorFeedback(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visitor_feedback"

    booking_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    rating: Mapped[int] = mapped_column()
    comments: Mapped[str | None] = mapped_column(nullable=True)
    would_return: Mapped[bool | None] = mapped_column(nullable=True)
    submitted_at: Mapped[datetime] = mapped_column()


class VisitRetailSale(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """RULE-VIS-006: also creates a core app.farmos.finance_models.Sale row
    (sale_id) so the purchase flows into Sales & Finance like any other
    sale — this row just links that sale back to the booking/visitor.
    """

    __tablename__ = "visit_retail_sale"

    booking_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    visitor_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    sale_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    channel: Mapped[str] = mapped_column(String(32), default="farm_shop")
    total_amount: Mapped[float] = mapped_column(Numeric(10, 2), default=0)


class OpeningCalendarDay(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "visit_opening_calendar_day"

    weekday: Mapped[int] = mapped_column()
    is_open: Mapped[bool] = mapped_column(Boolean, default=False)
    open_time: Mapped[str | None] = mapped_column(nullable=True)
    close_time: Mapped[str | None] = mapped_column(nullable=True)
    default_capacity: Mapped[int] = mapped_column(default=0)
    notes: Mapped[str | None] = mapped_column(nullable=True)
