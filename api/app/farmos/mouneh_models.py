"""Farm-data-plane (RLS-protected, tenant-scoped) tables for the Mouneh
production module (a licensed add-on — see app/farmos/routes_modules.py).
Field names match docs/FARMOS_API.md exactly.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import TenantBase
from app.common.mixins import SyncedEntityMixin, TimestampMixin, UUIDPrimaryKeyMixin


class RawMaterial(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "raw_material"

    name: Mapped[str] = mapped_column()
    category: Mapped[str] = mapped_column(String(64), default="raw_material")
    source_type: Mapped[str] = mapped_column(String(32), default="purchased")
    inventory_item_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    unit: Mapped[str] = mapped_column(String(16))
    default_unit_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    stock_tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    current_stock: Mapped[float] = mapped_column(Numeric(14, 3), default=0)
    loss_percent_default: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class MounehProduct(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "mouneh_product"

    name: Mapped[str] = mapped_column()
    category: Mapped[str] = mapped_column(String(64), default="general")
    photo_path: Mapped[str | None] = mapped_column(nullable=True)
    output_unit: Mapped[str] = mapped_column(String(32))
    custom_output_unit_label: Mapped[str | None] = mapped_column(nullable=True)
    default_batch_size: Mapped[float] = mapped_column(Numeric(12, 3), default=1)
    shelf_life_days: Mapped[int | None] = mapped_column(nullable=True)
    warehouse_rules: Mapped[str | None] = mapped_column(nullable=True)
    low_stock_threshold: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    target_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    wholesale_price: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    target_margin_pct: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")


class Recipe(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    """Versioned per product — only one active=True row per product_id at
    a time; creating a new recipe deactivates the previous one rather than
    replacing it, so past batches keep pointing at the recipe they were
    actually built from.
    """

    __tablename__ = "mouneh_recipe"

    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    version: Mapped[int] = mapped_column(default=1)
    effective_from: Mapped[datetime] = mapped_column()
    basis_quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    basis_unit: Mapped[str] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


class RecipeItem(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "mouneh_recipe_item"

    recipe_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    material_id: Mapped[str] = mapped_column(String(64))
    material_type: Mapped[str] = mapped_column(String(32), default="raw_material")
    quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    unit: Mapped[str] = mapped_column(String(16))
    loss_percent: Mapped[float] = mapped_column(Numeric(5, 2), default=0)
    is_optional: Mapped[bool] = mapped_column(Boolean, default=False)


class CostComponent(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    """Attached either to a product's recipe (batch_id null — the standard
    cost) or to one specific ProductionBatch (extra_cost_components passed
    at completion).
    """

    __tablename__ = "mouneh_cost_component"

    recipe_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    label: Mapped[str] = mapped_column()
    cost_type: Mapped[str] = mapped_column(String(32))
    calculation_method: Mapped[str] = mapped_column(String(32), default="fixed_amount")
    quantity: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    allocation_basis: Mapped[str | None] = mapped_column(nullable=True)


class ProductionBatch(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "mouneh_production_batch"

    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    recipe_version_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    batch_code: Mapped[str] = mapped_column(String(64))
    planned_qty: Mapped[float] = mapped_column(Numeric(12, 3))
    actual_output_qty: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    waste_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    damaged_qty: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    quality_status: Mapped[str] = mapped_column(String(32), default="good")
    expiry_date: Mapped[datetime | None] = mapped_column(nullable=True)
    warehouse_location: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="in_progress")
    planned_unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    planned_total_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    actual_unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    actual_total_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)
    labor_hours: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    started_at: Mapped[datetime] = mapped_column()
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    notes: Mapped[str | None] = mapped_column(nullable=True)


class BatchInputConsumption(UUIDPrimaryKeyMixin, SyncedEntityMixin, TenantBase):
    __tablename__ = "mouneh_batch_input_consumption"

    batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    material_id: Mapped[str] = mapped_column(String(64))
    planned_qty: Mapped[float] = mapped_column(Numeric(12, 3))
    actual_qty: Mapped[float | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    total_cost: Mapped[float | None] = mapped_column(Numeric(12, 4), nullable=True)


class FinishedGoodsStock(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "mouneh_finished_goods_stock"

    batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    quantity_produced: Mapped[float] = mapped_column(Numeric(12, 3))
    quantity_available: Mapped[float] = mapped_column(Numeric(12, 3))
    quantity_reserved: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    quantity_sold: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    quantity_damaged: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    quantity_expired: Mapped[float] = mapped_column(Numeric(12, 3), default=0)
    unit_cost: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    expiry_date: Mapped[datetime | None] = mapped_column(nullable=True)
    warehouse_location: Mapped[str | None] = mapped_column(nullable=True)


class MounehSale(UUIDPrimaryKeyMixin, SyncedEntityMixin, TimestampMixin, TenantBase):
    __tablename__ = "mouneh_sale"

    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    batch_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    finished_goods_stock_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    quantity: Mapped[float] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[float] = mapped_column(Numeric(12, 2))
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(String(32), default="retail")
    cost_per_unit: Mapped[float] = mapped_column(Numeric(12, 4), default=0)
    revenue: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    margin: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    sold_at: Mapped[datetime] = mapped_column()
