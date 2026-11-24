"""Farm-data-plane (RLS-protected, tenant-scoped) tables for Stage 4:
expenses, sales, and the recommendation engine. Field names match
docs/FARMOS_API.md exactly.

Stage 13 (reference v0.8.0, `domain/finance_models.py`) made the two money
tables the farm's books. The farm keeps them in its own currency (USD). A
sale or a bill may be agreed in Lebanese pounds: it is recorded in LBP
with the exchange rate of that day and counted in USD everywhere — the
original amount, currency and rate stay on the record (`original_amount`,
`original_currency`, `exchange_rate`).

The rate is the farm manager's to set: "1 USD = 89,500 LBP from 1 Nov".
Rates are a history, never edited: a new rate from a date is a new row,
and a record uses the rate in force on its own day unless the person
entering it gives the rate they actually used.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

# The farm's books are in USD; LBP is the other currency money changes hands in.
BASE_CURRENCY = "USD"
CURRENCIES = ("USD", "LBP")


class ExchangeRate(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """How many units of `currency` make one USD, from `effective_date`."""

    __tablename__ = "exchange_rate"
    __table_args__ = (Index("ix_exchange_rate_currency_date", "tenant_id", "currency", "effective_date"),)

    currency: Mapped[str] = mapped_column(String(10))
    rate_per_usd: Mapped[float] = mapped_column(Float)
    effective_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    set_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class Expense(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Rows land here via POST /expenses and POST /finance/expenses (an
    expense paid, or a bill still to pay — the Sales & Finance screen), or
    via other write paths (platform back-office entry, ...) as those get
    built. A cancelled expense stays on file, out of the totals (`status`).
    """

    __tablename__ = "expense"

    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    category: Mapped[str] = mapped_column(String(64))
    # In the farm's currency (USD); an LBP bill keeps its own amount and rate.
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    linked_entity_type: Mapped[str | None] = mapped_column(nullable=True)
    linked_entity_id: Mapped[str | None] = mapped_column(nullable=True)
    incurred_at: Mapped[datetime] = mapped_column()
    # v0.8.0 — expenses and bills recorded in Sales & Finance.
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    supplier_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # paid | pending (a bill still to pay)
    payment_status: Mapped[str] = mapped_column(String(20), default="paid", server_default="paid")
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # recorded | cancelled — a cancelled expense stays on file, out of the totals.
    status: Mapped[str] = mapped_column(String(20), default="recorded", server_default="recorded")
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    exchange_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # dairy | poultry | livestock | crops | mouneh | visits | general — null: from the category.
    activity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class Sale(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The farm's revenue: rows come from the Farm Visits module's retail
    sales (Stage 5), from milk sold from the tank (Stage 13,
    `milk_models.MilkSale.sale_id`), and from any other product recorded in
    Sales & Finance (POST /finance/sales, POST /sales). A cancelled sale
    stays on file and is undone by a second, negative `reversal` row
    (`reversal_of_id`); nothing is edited.
    """

    __tablename__ = "sale"

    customer_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    product_type: Mapped[str] = mapped_column(String(64))
    product_label: Mapped[str | None] = mapped_column(nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # In the farm's currency (USD). A sale made in LBP keeps what was
    # agreed in `original_amount` / `original_currency` and the rate used.
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    # paid | pending | partial
    payment_status: Mapped[str] = mapped_column(String(32), default="paid")
    sold_at: Mapped[datetime] = mapped_column()
    # v0.8.0 — any sale recorded in Sales & Finance (finance_service.py).
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    buyer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # recorded | cancelled | reversal
    status: Mapped[str] = mapped_column(String(20), default="recorded", server_default="recorded")
    cancel_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    original_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(10), nullable=True)
    exchange_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # dairy | poultry | livestock | crops | mouneh | visits | general — null: from the product.
    activity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


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
