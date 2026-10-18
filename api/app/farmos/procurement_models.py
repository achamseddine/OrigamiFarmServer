"""Farm-data-plane (RLS-protected, tenant-scoped) tables for purchase
requisitions and their approvals (OrigamiFarmOS database/PROCUREMENT-SCHEMA.md
§5–§6).

Procurement owns sourcing, requisition and approval; inventory owns stock.
An approved requisition is not a purchase and never a receipt. A
replenishment rule (a pharmacy threshold) may open one DRAFT requisition
for a shortage; a person submits and a manager approves — the approval
authority is snapshotted at decision time so a later change of delegation
does not rewrite who could decide.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin

REQUISITION_STATUSES = ("draft", "submitted", "approved", "rejected", "cancelled", "ordered", "received")
LINE_STATUSES = ("open", "approved", "rejected", "cancelled", "ordered", "received")
APPROVAL_DECISIONS = ("approved", "rejected")


class PurchaseRequisition(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "purchase_requisition"
    __table_args__ = (
        UniqueConstraint("tenant_id", "requisition_no", name="uq_purchase_requisition_no"),
        Index("ix_purchase_requisition_status", "status"),
    )

    requisition_no: Mapped[str] = mapped_column(String(80))
    requested_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    needed_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # pharmacy_replenishment | feed_replenishment | manual | maintenance
    purpose_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    justification: Mapped[str | None] = mapped_column(nullable=True)
    # pharmacy_stock_alert | feed_performance_alert | feed_demand_forecast | manual
    source_type: Mapped[str | None] = mapped_column(String(60), nullable=True)
    source_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    total_estimated_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    lines: Mapped[list["PurchaseRequisitionLine"]] = relationship(
        back_populates="requisition",
        cascade="all, delete-orphan",
        order_by="PurchaseRequisitionLine.line_no",
        lazy="selectin",
    )


class PurchaseRequisitionLine(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "purchase_requisition_line"
    __table_args__ = (UniqueConstraint("requisition_id", "line_no", name="uq_purchase_requisition_line_no"),)

    requisition_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("purchase_requisition.id"), index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    requested_quantity: Mapped[float] = mapped_column(Float)
    uom_code: Mapped[str] = mapped_column(String(30))
    estimated_unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specification: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open")
    source_recommendation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    requisition: Mapped[PurchaseRequisition] = relationship(back_populates="lines")


class ProcurementApproval(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "procurement_approval"

    # purchase_requisition | purchase_order
    document_type: Mapped[str] = mapped_column(String(40))
    document_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    approval_stage: Mapped[str] = mapped_column(String(60), default="manager")
    decision: Mapped[str] = mapped_column(String(30))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authority_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    comments: Mapped[str | None] = mapped_column(nullable=True)


PROCUREMENT_TABLES = ("purchase_requisition", "purchase_requisition_line", "procurement_approval")
