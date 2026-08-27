from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import ControlBase
from app.common.enums import InvoiceStatus
from app.common.mixins import TimestampMixin, UUIDPrimaryKeyMixin
from app.common.types import str_enum


class BillingAccount(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    """Internal billing state. Payment-provider integration stays behind an
    adapter (app/billing/provider.py) so FarmOS authorization never couples
    to one payment gateway — see LICENSE_ENTITLEMENTS.md.
    """

    __tablename__ = "billing_account"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenant.id", ondelete="CASCADE"), unique=True, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    billing_email: Mapped[str | None] = mapped_column(nullable=True)


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, ControlBase):
    __tablename__ = "invoice"

    billing_account_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("billing_account.id", ondelete="CASCADE"), index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[InvoiceStatus] = mapped_column(str_enum(InvoiceStatus), default=InvoiceStatus.DRAFT)
    period_start: Mapped[datetime] = mapped_column()
    period_end: Mapped[datetime] = mapped_column()
    due_at: Mapped[datetime] = mapped_column()
    paid_at: Mapped[datetime | None] = mapped_column(nullable=True)
