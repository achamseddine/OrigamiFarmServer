"""Selling milk, and the milk tank it is sold from (reference v0.7.9,
`domain/milk_models.py`; PR 24).

Milk recorded as *stored* goes into the farm's tank. It leaves the tank
when it is sold — a milk sale: the litres, the price per litre, the buyer,
paid or still to be paid — or when it is otherwise lost or used (spoiled,
fed to calves, taken home), recorded as a tank adjustment. A count of the
tank is an adjustment too: the difference between what the records say
and what is really there.

The tank is never a stored, editable number (CLAUDE.md: "Inventory is
ledger-driven", "No duplicate shadow inventory"): it is worked out every
time from the milk records, the sales and the adjustments.

Each sale also writes the farm's revenue record (`sale`, product_type
"milk") that Sales & Finance and the reports read; a cancelled sale is
reversed by a second, negative revenue record — history is never edited.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

MILK_PAYMENT_STATUSES = ("paid", "pending")
MILK_SALE_STATUSES = ("recorded", "cancelled")
# Why milk left (or came back to) the tank outside a sale.
TANK_ADJUSTMENT_REASONS = ("spoiled", "calves", "home_use", "count", "other")


class MilkSale(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "milk_sale"
    __table_args__ = (Index("ix_milk_sale_sold", "tenant_id", "sold_at"),)

    sold_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    liters: Mapped[float] = mapped_column(Float)
    price_per_liter: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    # The buyer: an opaque customer id the farm chooses (no customers
    # master on the server — "Known, deliberate gaps") and the name as
    # given on the sale.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payment_status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="recorded")
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The revenue record this sale wrote, and the one that reversed it.
    sale_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    reversal_sale_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class MilkTankAdjustment(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "milk_tank_adjustment"
    __table_args__ = (Index("ix_milk_tank_adjustment_occurred", "tenant_id", "occurred_at"),)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Signed: milk out of the tank is negative, milk found by a count positive.
    liters: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(String(20))
    # For a count: the litres really in the tank.
    counted_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
