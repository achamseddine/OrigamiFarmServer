"""Farm-data-plane (RLS-protected, tenant-scoped) tables for Stage 3 of the
FarmOS tablet contract: recording work — animal health, observations,
production, and agriculture. Field names match docs/FARMOS_API.md exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Treatment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """entity_type/entity_id are generic (not a foreign key) so the same
    table covers animals, flocks, and anything else that gets treated —
    the API resolves the concrete entity itself when it needs to (e.g.
    propagating withdrawal_until onto an Animal row).
    """

    __tablename__ = "treatment"

    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    diagnosis: Mapped[str | None] = mapped_column(nullable=True)
    medication: Mapped[str] = mapped_column()
    dose: Mapped[str] = mapped_column()
    route: Mapped[str] = mapped_column()
    start_at: Mapped[datetime] = mapped_column()
    end_at: Mapped[datetime | None] = mapped_column(nullable=True)
    withdrawal_until: Mapped[datetime | None] = mapped_column(nullable=True)
    vet_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    responsible_user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32), default="active")
    cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


# Rough, deliberately simple confidence-by-quality lookup: there is no ML
# model behind this yet, just a fixed weight per how the value was
# obtained. See ObservationCreate/ObservationOut in docs/FARMOS_API.md.
OBSERVATION_QUALITY_CONFIDENCE: dict[str, float] = {
    "human_observed": 0.65,
    "sensor": 0.9,
    "ai_estimated": 0.5,
}
DEFAULT_OBSERVATION_CONFIDENCE = 0.5


class Observation(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Workers record observations; workers do not diagnose (Constitution).
    This table structurally has no diagnosis field — Treatment is the only
    place a diagnosis can be recorded, and that endpoint is role-gated.
    """

    __tablename__ = "observation"

    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64))
    observation_type: Mapped[str] = mapped_column(String(64))
    quality: Mapped[str] = mapped_column(String(32), default="human_observed")
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    value_numeric: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    value_text: Mapped[str | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(nullable=True)
    severity: Mapped[str | None] = mapped_column(nullable=True)
    observed_at: Mapped[datetime] = mapped_column()
    observer_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(nullable=True)


class MilkRecord(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "milk_record"

    animal_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    session: Mapped[str] = mapped_column(String(16))
    liters: Mapped[float] = mapped_column(Numeric(8, 2))
    quality_status: Mapped[str] = mapped_column(String(32), default="normal")
    destination: Mapped[str] = mapped_column(String(32), default="stored")
    recorded_at: Mapped[datetime] = mapped_column()
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class EggRecord(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "egg_record"

    flock_id: Mapped[str] = mapped_column(String(64))
    total_eggs: Mapped[int] = mapped_column(default=0)
    sellable_eggs: Mapped[int] = mapped_column(default=0)
    broken_eggs: Mapped[int] = mapped_column(default=0)
    consumed: Mapped[int] = mapped_column(default=0)
    hatched: Mapped[int] = mapped_column(default=0)
    wasted: Mapped[int] = mapped_column(default=0)
    recorded_at: Mapped[datetime] = mapped_column()


class HarvestRecord(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The simple production/harvest ledger — a plain log entry, unlike
    DailyHarvest (POST /harvest) which also moves stock into inventory.
    """

    __tablename__ = "harvest_record"

    field_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    product_name: Mapped[str] = mapped_column()
    quantity: Mapped[float] = mapped_column(Numeric(12, 2))
    unit: Mapped[str] = mapped_column(String(16), default="kg")
    waste_qty: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    destination: Mapped[str | None] = mapped_column(nullable=True)
    recorded_at: Mapped[datetime] = mapped_column()


class Crop(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "crop"

    name: Mapped[str] = mapped_column()
    category: Mapped[str | None] = mapped_column(nullable=True)
    default_cycle_days: Mapped[int | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class CropPlanting(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "crop_planting"

    field_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    crop_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    variety: Mapped[str | None] = mapped_column(nullable=True)
    planted_area: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    area_unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    planted_date: Mapped[datetime | None] = mapped_column(nullable=True)
    expected_harvest_date: Mapped[datetime | None] = mapped_column(nullable=True)
    expected_yield_kg: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    stage: Mapped[str] = mapped_column(String(32), default="planted")
    status: Mapped[str] = mapped_column(String(32), default="active")
    notes: Mapped[str | None] = mapped_column(nullable=True)


class DailyHarvest(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """POST /harvest — records the day's pick and moves the sellable part
    into real InventoryItem stock (see app/farmos/routes_agriculture.py),
    so the Produce screen's "N kg ready for sale" is real inventory.
    """

    __tablename__ = "daily_harvest"

    field_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    product_name: Mapped[str] = mapped_column()
    total_quantity: Mapped[float] = mapped_column(Numeric(12, 2))
    sellable_quantity: Mapped[float] = mapped_column(Numeric(12, 2))
    waste_quantity: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    unit: Mapped[str] = mapped_column(String(16), default="kg")
    recorded_at: Mapped[datetime] = mapped_column()
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
