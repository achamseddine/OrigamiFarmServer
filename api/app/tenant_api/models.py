"""Farm operational data plane (RLS-protected, tenant-scoped).

These tables live in the shared/dedicated *tenant* database, never the
control database. Every table here has tenant_id (enforced by
SyncedEntityMixin) and Row-Level Security enabled — see
api/migrations/tenant/versions for the policies, and TENANCY.md for why
this is the mandatory isolation boundary rather than just an app-level
filter.

This is a representative slice of the FarmOS domain (animals, fields,
inventory, tasks) — enough to prove the tenant-context -> entitlement ->
RLS pipeline end to end. Milk/eggs/Mouneh/sales/visits follow the same
pattern in later milestones.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.enums import SyncOperation
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class Animal(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Field names match the FarmOS tablet contract's AnimalOut/AnimalCreate
    exactly (docs/FARMOS_API.md) — this is the animal's "digital twin"
    both the tablet app and the generic sync protocol (app/sync/) operate
    on against the same rows.
    """

    __tablename__ = "animal"

    tag: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column()
    species: Mapped[str] = mapped_column(String(64))
    breed: Mapped[str | None] = mapped_column(nullable=True)
    sex: Mapped[str | None] = mapped_column(String(1), nullable=True)
    birth_date: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="healthy")
    location_label: Mapped[str | None] = mapped_column(nullable=True)
    health_score: Mapped[int] = mapped_column(default=100)
    pregnant: Mapped[bool] = mapped_column(default=False)
    pregnancy_days: Mapped[int | None] = mapped_column(nullable=True)
    lactating: Mapped[bool] = mapped_column(default=False)
    lactation_cycle: Mapped[int | None] = mapped_column(nullable=True)
    withdrawal_until: Mapped[datetime | None] = mapped_column(nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    group_name: Mapped[str | None] = mapped_column(nullable=True)
    photo_path: Mapped[str | None] = mapped_column(nullable=True)
    acquisition_date: Mapped[datetime | None] = mapped_column(nullable=True)
    acquisition_source: Mapped[str | None] = mapped_column(nullable=True)
    sire_tag: Mapped[str | None] = mapped_column(nullable=True)
    dam_tag: Mapped[str | None] = mapped_column(nullable=True)
    color_markings: Mapped[str | None] = mapped_column(nullable=True)
    purchase_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    current_value: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(default=True)


class Field(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "field"

    name: Mapped[str] = mapped_column()
    crop: Mapped[str | None] = mapped_column(nullable=True)
    area_hectares: Mapped[float | None] = mapped_column(Numeric(10, 3), nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)


class InventoryItem(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Current on-hand quantity. Mutated only via InventoryMovement so
    stock changes are append-based, matching SYNC_PROTOCOL.md's rule that
    inventory quantities are derived from movements, not overwritten.
    """

    __tablename__ = "inventory_item"

    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column()
    unit: Mapped[str] = mapped_column(String(16), default="unit")
    quantity_on_hand: Mapped[float] = mapped_column(Numeric(14, 3), default=0)


class InventoryMovement(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "inventory_movement"

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    quantity_delta: Mapped[float] = mapped_column(Numeric(14, 3))
    reason: Mapped[str] = mapped_column(default="")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Task(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "task"

    title: Mapped[str] = mapped_column()
    description: Mapped[str | None] = mapped_column(nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(32), default="open")
    source_type: Mapped[str | None] = mapped_column(nullable=True)
    source_id: Mapped[str | None] = mapped_column(nullable=True)


class SyncEvent(UUIDPrimaryKeyMixin, TenantBase):
    """Idempotency ledger for offline sync pushes. event_id is the client's
    own UUID for the change; a repeat push with the same event_id is a
    no-op replay, not a duplicate write — see SYNC_PROTOCOL.md.
    """

    __tablename__ = "sync_event"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    operation: Mapped[SyncOperation] = mapped_column(str_enum(SyncOperation))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
