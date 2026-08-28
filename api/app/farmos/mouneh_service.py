"""Costing math shared by app/farmos/routes_mouneh.py: scaling a recipe to
a batch size, and rolling consumption/cost-component lines up into a
batch's planned/actual unit and total cost. Kept out of the route module
so the arithmetic can be read (and tested) on its own.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.mouneh_models import (
    BatchInputConsumption,
    CostComponent,
    ProductionBatch,
    RawMaterial,
    Recipe,
    RecipeItem,
)


def next_batch_code(db: Session, tenant_id: uuid.UUID, now: datetime) -> str:
    prefix = f"MOU-{now:%Y%m%d}-"
    count = db.execute(
        select(ProductionBatch).where(
            ProductionBatch.tenant_id == tenant_id, ProductionBatch.batch_code.like(f"{prefix}%")
        )
    ).scalars().all()
    return f"{prefix}{len(count) + 1:03d}"


def _scaled_component_amount(component: CostComponent, scale_factor: float) -> float:
    if component.calculation_method == "quantity_x_rate" and (
        component.quantity is not None and component.unit_cost is not None
    ):
        return float(component.quantity) * scale_factor * float(component.unit_cost)
    if component.amount is not None:
        return float(component.amount) * scale_factor
    return 0.0


def scaffold_batch_consumptions(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    batch: ProductionBatch,
    recipe: Recipe,
) -> tuple[list[BatchInputConsumption], float]:
    """One BatchInputConsumption per recipe item, planned_qty scaled to
    this batch's planned_qty and inflated by the item's own loss_percent —
    see docs/FARMOS_API.md's worked example (45kg @ 6% loss, 60/100 batch
    scale -> 28.62kg planned). Returns the rows plus their total planned
    material cost.
    """
    scale_factor = float(batch.planned_qty) / float(recipe.basis_quantity) if recipe.basis_quantity else 0.0
    items = db.execute(select(RecipeItem).where(RecipeItem.recipe_id == recipe.id)).scalars().all()

    rows: list[BatchInputConsumption] = []
    material_cost = 0.0
    for item in items:
        planned_qty = float(item.quantity) * scale_factor * (1 + float(item.loss_percent) / 100)
        material = db.execute(
            select(RawMaterial).where(
                RawMaterial.tenant_id == tenant_id, RawMaterial.id == uuid.UUID(item.material_id)
            )
        ).scalar_one_or_none() if _is_uuid(item.material_id) else None
        unit_cost = float(material.default_unit_cost) if material is not None else 0.0
        row = BatchInputConsumption(
            tenant_id=tenant_id,
            batch_id=batch.id,
            material_id=item.material_id,
            planned_qty=planned_qty,
            unit_cost=unit_cost,
        )
        db.add(row)
        rows.append(row)
        material_cost += planned_qty * unit_cost

    return rows, material_cost


def planned_cost(
    db: Session, *, recipe: Recipe, batch: ProductionBatch, material_cost: float
) -> tuple[float, float]:
    scale_factor = float(batch.planned_qty) / float(recipe.basis_quantity) if recipe.basis_quantity else 0.0
    components = db.execute(
        select(CostComponent).where(CostComponent.recipe_id == recipe.id, CostComponent.batch_id.is_(None))
    ).scalars().all()
    extra = sum(_scaled_component_amount(c, scale_factor) for c in components)
    total = material_cost + extra
    unit = total / float(batch.planned_qty) if batch.planned_qty else 0.0
    return unit, total


def complete_batch_costs(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    batch: ProductionBatch,
    recipe: Recipe,
    extra_components: list,
) -> tuple[float, float]:
    """Fills in any consumption still missing an actual_qty at its planned
    quantity (REQ-MOU-005), deducts stock-tracked raw materials, and
    returns (actual_unit_cost, actual_total_cost). Recipe-level cost
    components (labor, etc — the same ones planned_cost() scaled into
    planned_total_cost) count here too, at the same batch-size scaling,
    since they don't vary with the actual output quantity.
    """
    consumptions = db.execute(
        select(BatchInputConsumption).where(BatchInputConsumption.batch_id == batch.id)
    ).scalars().all()

    total = 0.0
    for row in consumptions:
        if row.actual_qty is None:
            row.actual_qty = row.planned_qty
        row.total_cost = float(row.actual_qty) * float(row.unit_cost)
        total += row.total_cost
        _deduct_material_stock(db, tenant_id, row.material_id, float(row.actual_qty))

    scale_factor = float(batch.planned_qty) / float(recipe.basis_quantity) if recipe.basis_quantity else 0.0
    recipe_components = db.execute(
        select(CostComponent).where(CostComponent.recipe_id == recipe.id, CostComponent.batch_id.is_(None))
    ).scalars().all()
    total += sum(_scaled_component_amount(c, scale_factor) for c in recipe_components)

    for component in extra_components:
        db.add(
            CostComponent(
                tenant_id=tenant_id,
                batch_id=batch.id,
                product_id=batch.product_id,
                label=component.label,
                cost_type=component.cost_type,
                calculation_method=component.calculation_method,
                quantity=component.quantity,
                unit_cost=component.unit_cost,
                amount=component.amount,
                allocation_basis=component.allocation_basis,
            )
        )
        if component.amount is not None:
            total += float(component.amount)
        elif component.quantity is not None and component.unit_cost is not None:
            total += float(component.quantity) * float(component.unit_cost)

    actual_output = float(batch.actual_output_qty) if batch.actual_output_qty else 0.0
    unit = total / actual_output if actual_output else 0.0
    return unit, total


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _deduct_material_stock(db: Session, tenant_id: uuid.UUID, material_id: str, quantity: float) -> None:
    if not _is_uuid(material_id):
        return
    material = db.execute(
        select(RawMaterial).where(
            RawMaterial.tenant_id == tenant_id, RawMaterial.id == uuid.UUID(material_id)
        )
    ).scalar_one_or_none()
    if material is not None and material.stock_tracking_enabled:
        material.current_stock = float(material.current_stock) - quantity
        material.version += 1


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
