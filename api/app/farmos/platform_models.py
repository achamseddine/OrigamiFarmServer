"""Platform masters the domain modules share (OrigamiFarmOS
FOUNDATION-SCHEMA.md, ASSET-MAINTENANCE-SCHEMA.md §2,
MEDICINE-NOTIFICATION-RULES.md §Notification behavior,
FEED-PERFORMANCE-INTELLIGENCE.md §12).

* `unit_of_measure` — a farm's own units on top of the built-in catalogue
  in app/farmos/feed_catalog.py. A unit is a row with a dimension and a
  factor to a base unit; app/farmos/uom_service.py resolves against both,
  so a medicine's strength, a lot's unit and a dose unit are references,
  never free text. Built-ins stay in code (the same for every farm, like
  the livestock catalog); only a farm's additions are rows.
* `farm_asset` — durable resources: a mixer wagon, a cooling tank, a
  milking machine. A numbered mix names the mixer it was made on.
* `notification_route` — who a farm wants told about what: a route code
  (pharmacy stock, emergency escalation, …) to a role or a person. The
  domain alert stays authoritative; routing only decides the recipients.
* `scheduled_job_run` — the run log of the scheduled cycles (feed
  performance, pharmacy scan, emergency reassessment check), so a manager
  can see when the farm was last evaluated and why a run failed.

Every row is a tenant row (RLS), including the unit catalogue additions:
a unit one farm invents is never visible to another.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

UOM_DIMENSIONS = (
    "mass",
    "volume",
    "count",
    "concentration",
    "dose_rate",
    "energy",
    "time",
    "length",
    "area",
    "other",
)
ASSET_TYPES = (
    "mixer",
    "tractor",
    "pump",
    "generator",
    "milking_machine",
    "cooling_tank",
    "incubator",
    "sprayer",
    "vehicle",
    "building",
    "other",
)
ASSET_STATUSES = ("active", "out_of_service", "retired")
JOB_CODES = ("feed_performance_cycle", "pharmacy_scan", "emergency_reassessment_check")
JOB_TRIGGERS = ("scheduler", "manual", "startup")
RUN_STATUSES = ("running", "succeeded", "failed")
ROUTE_CHANNELS = ("in_app",)


class UnitOfMeasure(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A farm's own unit. `code` is the reference every unit column
    carries (lower-cased); `base_unit_code` names a built-in or farm unit
    of the same dimension the quantities convert through, null for a
    count-like unit that converts to nothing."""

    __tablename__ = "unit_of_measure"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_unit_of_measure_code"),)

    code: Mapped[str] = mapped_column(String(30))
    name_en: Mapped[str] = mapped_column(String(80))
    name_ar: Mapped[str | None] = mapped_column(String(80), nullable=True)
    dimension: Mapped[str] = mapped_column(String(30), default="other")
    base_unit_code: Mapped[str | None] = mapped_column(String(30), nullable=True)
    factor_to_base: Mapped[float] = mapped_column(Float, default=1.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=500)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class FarmAsset(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "farm_asset"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_farm_asset_code"),)

    code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(180))
    asset_type: Mapped[str] = mapped_column(String(60), default="other")
    manufacturer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(150), nullable=True)
    acquisition_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acquisition_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # An opaque location code the farm chooses, like the pharmacy's —
    # there is no locations master yet (docs/FARMOS_API.md, gaps).
    current_location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # active | out_of_service | retired
    status: Mapped[str] = mapped_column(String(30), default="active")
    parent_asset_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class NotificationRoute(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One recipient rule: this farm wants `route_code` to reach this role
    or this person (a user id from the farm's memberships). No active rows
    for a route means the built-in default (managers and owners;
    veterinarians for the clinical routes). Replaced rows are kept
    inactive, never deleted."""

    __tablename__ = "notification_route"
    __table_args__ = (Index("ix_notification_route_tenant_route", "tenant_id", "route_code"),)

    route_code: Mapped[str] = mapped_column(String(60))
    role: Mapped[str | None] = mapped_column(String(30), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # in_app today; push / email / sms belong to the notification subsystem
    channel: Mapped[str] = mapped_column(String(20), default="in_app")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ScheduledJobRun(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "scheduled_job_run"
    __table_args__ = (Index("ix_scheduled_job_run_job", "tenant_id", "job_code", "started_at"),)

    job_code: Mapped[str] = mapped_column(String(60))
    # scheduler | manual | startup
    trigger: Mapped[str] = mapped_column(String(20), default="scheduler")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # running | succeeded | failed
    status: Mapped[str] = mapped_column(String(20), default="running")
    summary_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Who ran it: the member on whose authority it ran (the manager who
    # pressed the button, or the farm owner for the background loop).
    run_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
