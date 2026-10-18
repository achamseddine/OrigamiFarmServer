"""Farm-data-plane (RLS-protected, tenant-scoped) tables for the farm
pharmacy (OrigamiFarmOS database/MEDICINE-PHARMACY-SCHEMA.md).

A farm pharmacy is not a treatment history: the farm must know what
medicine it owns, where, in which lot with which expiry, what is eligible
to use, what must always be kept and when to reorder — even when no
animal is sick.

Stock stays on the existing `inventory_item` / `inventory_movement`
ledger. This module adds pharmaceutical semantics to an item
(`medicine_product`, whose id *is* the inventory item's id so the wire's
`inventory_item_id` and `medicine_product_id` are one value), the
canonical lot for non-feed stock (`inventory_lot`, with the pharmacy's
opened / storage / recall facts inline), reservations, the farm's own
essential-stock policy, deduplicated stock alerts and the administration
record that binds a dose to the exact lot it came from. Nothing here is a
second stock balance: a lot's on-hand moves only with a posted movement,
and eligibility is computed from lot facts on read.

Medicine categories and the built-in active-ingredient list are
code-defined in app/farmos/pharmacy_catalog.py, like every other catalog
of this port; a product's categories are codes on the product row.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


# -------------------------------------------------------------- product
class MedicineProduct(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Pharmaceutical semantics over one canonical inventory item (§2).
    The item keeps the name, unit and balance; this row says what the
    medicine is, how it is stored, whether it needs a prescription, and
    the withdrawal rule an administration applies. `id` equals
    `inventory_item_id`."""

    __tablename__ = "medicine_product"
    __table_args__ = (UniqueConstraint("tenant_id", "inventory_item_id", name="uq_medicine_product_item"),)

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    generic_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dosage_form: Mapped[str] = mapped_column(String(60), default="injectable")
    strength_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    # A unit is a code of the units-of-measure catalog (app/farmos/feed_catalog.py).
    strength_uom: Mapped[str | None] = mapped_column(String(30), nullable=True)
    strength_basis: Mapped[str | None] = mapped_column(String(80), nullable=True)
    administration_routes: Mapped[list] = mapped_column(JSONB, default=list)
    prescription_required: Mapped[bool] = mapped_column(Boolean, default=False)
    antimicrobial: Mapped[bool] = mapped_column(Boolean, default=False)
    controlled_medicine: Mapped[bool] = mapped_column(Boolean, default=False)
    cold_chain_required: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    storage_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    opened_shelf_life_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_pack_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    pack_uom: Mapped[str | None] = mapped_column(String(30), nullable=True)
    manufacturer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    authorization_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)
    # Species the product is authorised for; empty means any. Checked
    # server-side on administration, like a feed usage policy.
    species_codes: Mapped[list] = mapped_column(JSONB, default=list)
    # Authorised withdrawal rule, e.g. {"milk_days": 3, "meat_days": 14}.
    # Applied by an administration; never invented at the point of care.
    withdrawal_rules_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Codes from pharmacy_catalog.CATEGORIES — organisation and policy,
    # never a diagnosis (§4).
    category_codes: Mapped[list] = mapped_column(JSONB, default=list)
    # The lot/expiry requirement can only be waived by an explicit,
    # recorded exception (§6).
    lot_tracking_exception: Mapped[str | None] = mapped_column(nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    ingredients: Mapped[list["MedicineProductIngredient"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", lazy="selectin"
    )
    lots: Mapped[list["InventoryLot"]] = relationship(
        primaryjoin="MedicineProduct.inventory_item_id == foreign(InventoryLot.inventory_item_id)",
        viewonly=True,
        order_by="InventoryLot.received_at",
    )


class MedicineProductIngredient(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """An active ingredient of a product, by code: the built-in list or a
    code the farm added. Eligibility rules and recorded allergies match
    on the code."""

    __tablename__ = "medicine_product_ingredient"
    __table_args__ = (
        UniqueConstraint("medicine_product_id", "ingredient_code", name="uq_medicine_product_ingredient"),
    )

    medicine_product_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("medicine_product.id"), index=True
    )
    ingredient_code: Mapped[str] = mapped_column(String(80))
    ingredient_name: Mapped[str] = mapped_column(String(180))
    concentration_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    concentration_uom: Mapped[str | None] = mapped_column(String(30), nullable=True)
    concentration_basis: Mapped[str | None] = mapped_column(String(80), nullable=True)

    product: Mapped[MedicineProduct] = relationship(back_populates="ingredients")


# ----------------------------------------------------------------- lots
class InventoryLot(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The canonical lot of a non-feed inventory item (the schema's
    `inventory_lot`): what arrived, what was accepted, what is on hand,
    where it is and until when it is good. On-hand moves only with a
    posted inventory movement. The pharmacy facts of the lot (opened,
    storage, quarantine, recall — the schema's `medicine_lot_details`)
    live on the same row."""

    __tablename__ = "inventory_lot"
    __table_args__ = (
        UniqueConstraint("tenant_id", "inventory_item_id", "lot_code", name="uq_inventory_lot_item_code"),
    )

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    lot_code: Mapped[str] = mapped_column(String(120))
    # The manufacturer's / supplier's own lot number when it differs from
    # the farm's lot code (INVENTORY-SCHEMA §3).
    supplier_lot_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    manufactured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # purchased | opening_balance | donation | return
    source_type: Mapped[str] = mapped_column(String(30), default="purchased")
    # The commercial document the lot came from (goods receipt, requisition).
    source_reference_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    supplier_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The receiving location; later movements carry their own location on
    # the movement, and the per-location balance is derived from them.
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_quantity: Mapped[float] = mapped_column(Float, default=0)
    accepted_quantity: Mapped[float] = mapped_column(Float, default=0)
    rejected_quantity: Mapped[float] = mapped_column(Float, default=0)
    quantity_on_hand: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="items")
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Availability: active | quarantined | blocked | recalled | expired | depleted
    status: Mapped[str] = mapped_column(String(20), default="active")
    # Quality release: RELEASED | QUARANTINED | BLOCKED | RECALLED | EXPIRED
    # (INVENTORY-SCHEMA §3) — kept in step with `status` by the service.
    quality_status: Mapped[str] = mapped_column(String(30), default="RELEASED")
    reference: Mapped[str | None] = mapped_column(String(150), nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Pharmacy facts (§6): opened / use-by after opening, storage
    # compliance, quarantine and recall provenance.
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    use_by_after_opening: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    storage_status: Mapped[str] = mapped_column(String(30), default="COMPLIANT")
    cold_chain_exception: Mapped[bool] = mapped_column(Boolean, default=False)
    storage_note: Mapped[str | None] = mapped_column(nullable=True)
    quarantine_reason: Mapped[str | None] = mapped_column(nullable=True)
    recalled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recall_reference: Mapped[str | None] = mapped_column(String(150), nullable=True)


class InventoryReservation(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Stock held for a purpose (INVENTORY-SCHEMA §5): a protocol run, a
    treatment course, a planned procedure. Reservation is not consumption:
    it lowers what is available to everyone else, and the issue for that
    purpose consumes it."""

    __tablename__ = "inventory_reservation"
    __table_args__ = (Index("ix_inventory_reservation_item_status", "inventory_item_id", "status"),)

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    inventory_lot_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    consumed_quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30))
    # emergency_run | treatment | planned_procedure | other
    purpose_type: Mapped[str] = mapped_column(String(80))
    purpose_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    purpose_label: Mapped[str | None] = mapped_column(String(300), nullable=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # active | consumed | released | expired
    status: Mapped[str] = mapped_column(String(30), default="active")
    created_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


# --------------------------------------------------------------- policy
class PharmacyStockPolicy(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """The farm's own decision of what is essential and how much to keep
    (§5). Thresholds are configuration, never medical advice. One row per
    medicine farm-wide (location_id null) plus optional rows per location."""

    __tablename__ = "pharmacy_stock_policy"
    __table_args__ = (Index("ix_pharmacy_stock_policy_item", "inventory_item_id", "location_id"),)

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    essential: Mapped[bool] = mapped_column(Boolean, default=False)
    minimum_stock_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_stock_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    critical_stock_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_days_cover: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_time_days: Mapped[float | None] = mapped_column(Float, nullable=True)
    preferred_supplier_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    expiry_warning_days: Mapped[int] = mapped_column(Integer, default=60)
    # If true, a LOW/CRITICAL/OUT alert opens one draft requisition
    # (never a purchase order — §9).
    auto_draft_requisition: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The policy's own revision (the sync `version` is the mixin's).
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


# --------------------------------------------------------------- alerts
class PharmacyStockAlert(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """A deduplicated logistics finding (§8): one open row per condition,
    acknowledged by a person, resolved when the stock condition clears.
    A stock alert is never treatment advice."""

    __tablename__ = "pharmacy_stock_alert"
    __table_args__ = (Index("ix_pharmacy_stock_alert_item_status", "inventory_item_id", "status"),)

    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    alert_type: Mapped[str] = mapped_column(String(50))
    # critical | high | medium | low
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eligible_available_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum_stock_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_stock_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    earliest_expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiring_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommended_reorder_base: Mapped[float | None] = mapped_column(Float, nullable=True)
    explanation: Mapped[str] = mapped_column(default="")
    # open | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(30), default="open")
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(nullable=True)
    deduplication_key: Mapped[str] = mapped_column(String(255))
    requisition_task_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # The DRAFT purchase requisition the shortage opened (PROCUREMENT §5);
    # one per unresolved shortage, never approved from here.
    requisition_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


# ------------------------------------------------------- administration
class MedicationAdministration(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """One dose actually given (§11): subject, product, the exact lot it
    came from, dose/route/time, the consumption it posted and the
    withdrawal it applied from the product's authorised rule. Reversed by
    a correction, never edited."""

    __tablename__ = "medication_administration"
    __table_args__ = (Index("ix_medication_administration_subject", "subject_type", "subject_id"),)

    subject_type: Mapped[str] = mapped_column(String(10))  # animal | group
    subject_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    treatment_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    # Set when an approved emergency protocol step prepared this dose.
    protocol_run_step_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    inventory_item_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    inventory_lot_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    # The reservation this dose drew on, when stock was held for the purpose.
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    location_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dose_quantity: Mapped[float] = mapped_column(Float)
    dose_unit: Mapped[str] = mapped_column(String(30))
    route_code: Mapped[str] = mapped_column(String(40))
    head_count: Mapped[int] = mapped_column(Integer, default=1)
    quantity_consumed: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(30))
    administered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    administered_by: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    inventory_movement_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    withdrawal_milk_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawal_meat_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)
    # recorded | reversed
    status: Mapped[str] = mapped_column(String(20), default="recorded")
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(nullable=True)


PHARMACY_TABLES = (
    "medicine_product",
    "medicine_product_ingredient",
    "inventory_lot",
    "inventory_reservation",
    "pharmacy_stock_policy",
    "pharmacy_stock_alert",
    "medication_administration",
)
