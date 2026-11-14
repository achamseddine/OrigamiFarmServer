"""Farm-data-plane (RLS-protected, tenant-scoped) tables specific to the
FarmOS tablet contract. Field names match docs/FARMOS_API.md exactly.
Animal and Task live in app/tenant_api/models.py instead — they predate
this package and app/sync/ already depends on them directly.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Notification(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "notification"

    module_code: Mapped[str] = mapped_column(String(64))
    notification_type: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    entity_type: Mapped[str | None] = mapped_column(nullable=True)
    entity_id: Mapped[str | None] = mapped_column(nullable=True)
    source_type: Mapped[str | None] = mapped_column(nullable=True)
    source_id: Mapped[str | None] = mapped_column(nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FarmSettings(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The farm-level settings a manager changes from the tablet (reference
    `farms.dry_off_days_before_birth` and the calendar columns). The server
    has no tenant-side farm row, so they live here: one row per tenant,
    created with the defaults the first time they are read
    (app/farmos/farm_settings_service.py), updated in place — settings are
    not history-bearing facts, every change is an audit entry instead."""

    __tablename__ = "farm_settings"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_farm_settings_tenant"),)

    # Days before an expected birth that a lactating animal is dried off.
    dry_off_days_before_birth: Mapped[int] = mapped_column(Integer, default=60)
    # The calendar after a birth: milk peak window, voluntary waiting
    # period before the next insemination, heat cycle length.
    milk_peak_start_days: Mapped[int] = mapped_column(Integer, default=40)
    milk_peak_end_days: Mapped[int] = mapped_column(Integer, default=70)
    breeding_wait_days: Mapped[int] = mapped_column(Integer, default=50)
    heat_cycle_days: Mapped[int] = mapped_column(Integer, default=21)
