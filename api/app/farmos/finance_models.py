"""Farm-data-plane (RLS-protected, tenant-scoped) tables for Stage 4:
expenses, sales, and the recommendation engine. Field names match
docs/FARMOS_API.md exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Expense(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Read-only via the FarmOS tablet contract today — GET /expenses has
    no matching POST endpoint in docs/FARMOS_API.md, so rows land here via
    other write paths (feed purchases, platform back-office entry, ...) as
    those get built. See also Sale below, which is explicitly read-only
    for the same reason.
    """

    __tablename__ = "expense"

    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    linked_entity_type: Mapped[str | None] = mapped_column(nullable=True)
    linked_entity_id: Mapped[str | None] = mapped_column(nullable=True)
    incurred_at: Mapped[datetime] = mapped_column()


class Sale(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Read-only for now — every row comes from the Mouneh and Farm Visits
    modules' own sale-recording endpoints (Stage 5); a general manual
    sale-entry endpoint is tracked as follow-on work, matching the
    contract's own note on GET /sales.
    """

    __tablename__ = "sale"

    customer_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    product_type: Mapped[str] = mapped_column(String(64))
    product_label: Mapped[str | None] = mapped_column(nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    payment_status: Mapped[str] = mapped_column(String(32), default="paid")
    sold_at: Mapped[datetime] = mapped_column()


class Recommendation(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Generated only by real rules evaluated against real stored data
    (app/farmos/recommendations.py) — CONSTITUTION.md: never generated
    without persisted evidence. decision/decided_by/decided_at/
    decision_note record the accept/reject/postpone lifecycle event.
    """

    __tablename__ = "recommendation"

    category: Mapped[str] = mapped_column(String(32))
    priority: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column()
    entity_type: Mapped[str | None] = mapped_column(nullable=True)
    entity_id: Mapped[str | None] = mapped_column(nullable=True)
    entity_label: Mapped[str | None] = mapped_column(nullable=True)
    confidence: Mapped[float] = mapped_column(Numeric(4, 3))
    rationale: Mapped[str] = mapped_column()
    suggested_action: Mapped[str] = mapped_column()
    status: Mapped[str] = mapped_column(String(32), default="generated")
    rule_id: Mapped[str | None] = mapped_column(nullable=True)
    generated_at: Mapped[datetime] = mapped_column()
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(nullable=True)
