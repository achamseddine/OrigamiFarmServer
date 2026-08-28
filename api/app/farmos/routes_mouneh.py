"""Mouneh production: raw materials, products, versioned recipes,
production batches (plan -> consume -> complete -> finished goods), and
sales. A licensed add-on (see app/farmos/routes_modules.py) — the app
hides this screen entirely when the farm's "mouneh" licence isn't active,
so routes here don't re-check entitlement per request, only the normal
permission grid (module codes mouneh_production / mouneh_inventory).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_permission
from app.farmos.mouneh_models import (
    BatchInputConsumption,
    CostComponent,
    FinishedGoodsStock,
    MounehProduct,
    MounehSale,
    ProductionBatch,
    RawMaterial,
    Recipe,
    RecipeItem,
)
from app.farmos.mouneh_service import (
    complete_batch_costs,
    next_batch_code,
    now_utc,
    planned_cost,
    scaffold_batch_consumptions,
)
from app.farmos.schemas import (
    BatchCompleteRequest,
    BatchConsumeRequest,
    BatchInputConsumptionOut,
    CostComponentOut,
    FinishedGoodsStockOut,
    MounehProductCreate,
    MounehProductDetailOut,
    MounehProductOut,
    MounehSaleCreate,
    MounehSaleOut,
    ProductionBatchCreate,
    ProductionBatchOut,
    RawMaterialCreate,
    RawMaterialOut,
    RecipeCreate,
    RecipeItemOut,
    RecipeOut,
)

router = APIRouter()


# --- Raw materials -----------------------------------------------------


def _to_material_out(m: RawMaterial) -> RawMaterialOut:
    return RawMaterialOut(
        id=str(m.id),
        farm_id=str(m.tenant_id),
        name=m.name,
        category=m.category,
        source_type=m.source_type,
        inventory_item_id=str(m.inventory_item_id) if m.inventory_item_id else None,
        unit=m.unit,
        default_unit_cost=float(m.default_unit_cost),
        stock_tracking_enabled=m.stock_tracking_enabled,
        current_stock=float(m.current_stock),
        loss_percent_default=float(m.loss_percent_default),
        active=m.active,
    )


@router.get("/mouneh/raw-materials", response_model=list[RawMaterialOut])
def list_raw_materials(
    _access: AccessContext = Depends(require_permission("mouneh_inventory", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[RawMaterialOut]:
    rows = db.execute(
        select(RawMaterial).where(RawMaterial.deleted_at.is_(None)).order_by(RawMaterial.name)
    ).scalars().all()
    return [_to_material_out(row) for row in rows]


@router.post("/mouneh/raw-materials", response_model=RawMaterialOut, status_code=201)
def create_raw_material(
    payload: RawMaterialCreate,
    access: AccessContext = Depends(require_permission("mouneh_inventory", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> RawMaterialOut:
    material = RawMaterial(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        name=payload.name,
        category=payload.category,
        source_type=payload.source_type,
        inventory_item_id=uuid.UUID(payload.inventory_item_id) if payload.inventory_item_id else None,
        unit=payload.unit,
        default_unit_cost=payload.default_unit_cost,
        stock_tracking_enabled=payload.stock_tracking_enabled,
        current_stock=payload.current_stock,
        loss_percent_default=payload.loss_percent_default,
    )
    db.add(material)
    db.flush()
    return _to_material_out(material)


# --- Products & recipes --------------------------------------------------


def _to_product_out(p: MounehProduct) -> MounehProductOut:
    return MounehProductOut(
        id=str(p.id),
        farm_id=str(p.tenant_id),
        name=p.name,
        category=p.category,
        photo_path=p.photo_path,
        output_unit=p.output_unit,
        custom_output_unit_label=p.custom_output_unit_label,
        default_batch_size=float(p.default_batch_size),
        shelf_life_days=p.shelf_life_days,
        warehouse_rules=p.warehouse_rules,
        low_stock_threshold=float(p.low_stock_threshold) if p.low_stock_threshold is not None else None,
        target_price=float(p.target_price) if p.target_price is not None else None,
        wholesale_price=float(p.wholesale_price) if p.wholesale_price is not None else None,
        target_margin_pct=float(p.target_margin_pct) if p.target_margin_pct is not None else None,
        status=p.status,
        created_at=p.created_at,
    )


def _to_cost_component_out(c: CostComponent) -> CostComponentOut:
    return CostComponentOut(
        id=str(c.id),
        product_id=str(c.product_id) if c.product_id else None,
        batch_id=str(c.batch_id) if c.batch_id else None,
        label=c.label,
        cost_type=c.cost_type,
        calculation_method=c.calculation_method,
        quantity=float(c.quantity) if c.quantity is not None else None,
        unit_cost=float(c.unit_cost) if c.unit_cost is not None else None,
        amount=float(c.amount) if c.amount is not None else None,
        allocation_basis=c.allocation_basis,
    )


def _to_recipe_out(db: Session, r: Recipe) -> RecipeOut:
    items = db.execute(select(RecipeItem).where(RecipeItem.recipe_id == r.id)).scalars().all()
    components = db.execute(
        select(CostComponent).where(CostComponent.recipe_id == r.id, CostComponent.batch_id.is_(None))
    ).scalars().all()
    return RecipeOut(
        id=str(r.id),
        product_id=str(r.product_id),
        version=r.version,
        effective_from=r.effective_from,
        basis_quantity=float(r.basis_quantity),
        basis_unit=r.basis_unit,
        active=r.active,
        notes=r.notes,
        items=[
            RecipeItemOut(
                id=str(i.id),
                material_id=i.material_id,
                material_type=i.material_type,
                quantity=float(i.quantity),
                unit=i.unit,
                loss_percent=float(i.loss_percent),
                is_optional=i.is_optional,
            )
            for i in items
        ],
        cost_components=[_to_cost_component_out(c) for c in components],
    )


def _load_product_or_404(db: Session, product_id: str) -> MounehProduct:
    try:
        pk = uuid.UUID(product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Product not found.") from exc
    product = db.get(MounehProduct, pk)
    if product is None or product.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


@router.get("/mouneh/products", response_model=list[MounehProductOut])
def list_products(
    _access: AccessContext = Depends(require_permission("mouneh_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[MounehProductOut]:
    rows = db.execute(
        select(MounehProduct).where(MounehProduct.deleted_at.is_(None)).order_by(MounehProduct.name)
    ).scalars().all()
    return [_to_product_out(row) for row in rows]


@router.post("/mouneh/products", response_model=MounehProductOut, status_code=201)
def create_product(
    payload: MounehProductCreate,
    access: AccessContext = Depends(require_permission("mouneh_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> MounehProductOut:
    product = MounehProduct(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump()
    )
    db.add(product)
    db.flush()
    return _to_product_out(product)


@router.get("/mouneh/products/{product_id}", response_model=MounehProductDetailOut)
def get_product(
    product_id: str,
    _access: AccessContext = Depends(require_permission("mouneh_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> MounehProductDetailOut:
    product = _load_product_or_404(db, product_id)
    active_recipe = db.execute(
        select(Recipe).where(Recipe.product_id == product.id, Recipe.active.is_(True))
    ).scalar_one_or_none()
    return MounehProductDetailOut(
        **_to_product_out(product).model_dump(),
        active_recipe=_to_recipe_out(db, active_recipe) if active_recipe else None,
    )


@router.post("/mouneh/products/{product_id}/recipes", response_model=RecipeOut, status_code=201)
def create_recipe(
    product_id: str,
    payload: RecipeCreate,
    access: AccessContext = Depends(require_permission("mouneh_production", "configure")),
    db: Session = Depends(get_farmos_tenant_db),
) -> RecipeOut:
    product = _load_product_or_404(db, product_id)

    previous = db.execute(
        select(Recipe).where(Recipe.product_id == product.id, Recipe.active.is_(True))
    ).scalar_one_or_none()
    if previous is not None:
        previous.active = False

    max_version = db.execute(
        select(Recipe).where(Recipe.product_id == product.id).order_by(Recipe.version.desc())
    ).scalars().first()
    next_version = (max_version.version + 1) if max_version else 1

    recipe = Recipe(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        product_id=product.id,
        version=next_version,
        effective_from=now_utc(),
        basis_quantity=payload.basis_quantity,
        basis_unit=payload.basis_unit,
        active=True,
        notes=payload.notes,
    )
    db.add(recipe)
    db.flush()

    for item in payload.items:
        db.add(
            RecipeItem(
                tenant_id=access.tenant_id,
                recipe_id=recipe.id,
                material_id=item.material_id,
                material_type=item.material_type,
                quantity=item.quantity,
                unit=item.unit,
                loss_percent=item.loss_percent,
                is_optional=item.is_optional,
            )
        )
    for component in payload.cost_components:
        db.add(
            CostComponent(
                tenant_id=access.tenant_id,
                recipe_id=recipe.id,
                product_id=product.id,
                label=component.label,
                cost_type=component.cost_type,
                calculation_method=component.calculation_method,
                quantity=component.quantity,
                unit_cost=component.unit_cost,
                amount=component.amount,
                allocation_basis=component.allocation_basis,
            )
        )
    db.flush()
    return _to_recipe_out(db, recipe)


# --- Production batches --------------------------------------------------


def _to_consumption_out(c: BatchInputConsumption) -> BatchInputConsumptionOut:
    return BatchInputConsumptionOut(
        id=str(c.id),
        material_id=c.material_id,
        planned_qty=float(c.planned_qty),
        actual_qty=float(c.actual_qty) if c.actual_qty is not None else None,
        unit_cost=float(c.unit_cost),
        total_cost=float(c.total_cost) if c.total_cost is not None else None,
    )


def _to_batch_out(db: Session, b: ProductionBatch) -> ProductionBatchOut:
    consumptions = db.execute(
        select(BatchInputConsumption).where(BatchInputConsumption.batch_id == b.id)
    ).scalars().all()
    return ProductionBatchOut(
        id=str(b.id),
        farm_id=str(b.tenant_id),
        product_id=str(b.product_id),
        recipe_version_id=str(b.recipe_version_id),
        batch_code=b.batch_code,
        planned_qty=float(b.planned_qty),
        actual_output_qty=float(b.actual_output_qty) if b.actual_output_qty is not None else None,
        waste_qty=float(b.waste_qty),
        damaged_qty=float(b.damaged_qty),
        quality_status=b.quality_status,
        expiry_date=b.expiry_date,
        warehouse_location=b.warehouse_location,
        status=b.status,
        planned_unit_cost=float(b.planned_unit_cost) if b.planned_unit_cost is not None else None,
        planned_total_cost=float(b.planned_total_cost) if b.planned_total_cost is not None else None,
        actual_unit_cost=float(b.actual_unit_cost) if b.actual_unit_cost is not None else None,
        actual_total_cost=float(b.actual_total_cost) if b.actual_total_cost is not None else None,
        labor_hours=float(b.labor_hours) if b.labor_hours is not None else None,
        started_at=b.started_at,
        completed_at=b.completed_at,
        notes=b.notes,
        consumptions=[_to_consumption_out(c) for c in consumptions],
    )


def _load_batch_or_404(db: Session, batch_id: str) -> ProductionBatch:
    try:
        pk = uuid.UUID(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Batch not found.") from exc
    batch = db.get(ProductionBatch, pk)
    if batch is None or batch.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return batch


@router.get("/mouneh/batches", response_model=list[ProductionBatchOut])
def list_batches(
    product_id: str | None = Query(default=None),
    status_filter: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("mouneh_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[ProductionBatchOut]:
    stmt = select(ProductionBatch).where(ProductionBatch.deleted_at.is_(None))
    if product_id:
        stmt = stmt.where(ProductionBatch.product_id == uuid.UUID(product_id))
    if status_filter:
        stmt = stmt.where(ProductionBatch.status == status_filter)
    rows = db.execute(stmt.order_by(ProductionBatch.started_at.desc())).scalars().all()
    return [_to_batch_out(db, row) for row in rows]


@router.post("/mouneh/batches", response_model=ProductionBatchOut, status_code=201)
def create_batch(
    payload: ProductionBatchCreate,
    access: AccessContext = Depends(require_permission("mouneh_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> ProductionBatchOut:
    product = _load_product_or_404(db, payload.product_id)
    recipe = db.execute(
        select(Recipe).where(Recipe.product_id == product.id, Recipe.active.is_(True))
    ).scalar_one_or_none()
    if recipe is None:
        raise HTTPException(
            status_code=422,
            detail=f"{product.name} doesn't have a recipe yet — add one before starting a batch.",
        )

    now = now_utc()
    batch = ProductionBatch(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        product_id=product.id,
        recipe_version_id=recipe.id,
        batch_code=payload.batch_code or next_batch_code(db, access.tenant_id, now),
        planned_qty=payload.planned_qty,
        warehouse_location=payload.warehouse_location,
        notes=payload.notes,
        started_at=now,
    )
    db.add(batch)
    db.flush()

    _consumptions, material_cost = scaffold_batch_consumptions(
        db, tenant_id=access.tenant_id, batch=batch, recipe=recipe
    )
    unit_cost, total_cost = planned_cost(db, recipe=recipe, batch=batch, material_cost=material_cost)
    batch.planned_unit_cost = unit_cost
    batch.planned_total_cost = total_cost
    db.flush()
    return _to_batch_out(db, batch)


@router.post("/mouneh/batches/{batch_id}/complete", response_model=ProductionBatchOut)
def complete_batch(
    batch_id: str,
    payload: BatchCompleteRequest,
    access: AccessContext = Depends(require_permission("mouneh_production", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
) -> ProductionBatchOut:
    batch = _load_batch_or_404(db, batch_id)
    if batch.status == "completed":
        raise HTTPException(status_code=422, detail="This batch has already been completed.")

    product = db.get(MounehProduct, batch.product_id)
    now = now_utc()
    batch.actual_output_qty = payload.actual_output_qty
    batch.waste_qty = payload.waste_qty
    batch.damaged_qty = payload.damaged_qty
    batch.quality_status = payload.quality_status
    batch.warehouse_location = payload.warehouse_location or batch.warehouse_location
    batch.labor_hours = payload.labor_hours
    batch.status = "completed"
    batch.completed_at = now
    if payload.expiry_date is not None:
        batch.expiry_date = payload.expiry_date
    elif product is not None and product.shelf_life_days:
        batch.expiry_date = now + timedelta(days=product.shelf_life_days)

    recipe = db.get(Recipe, batch.recipe_version_id)
    assert recipe is not None  # set from an active recipe when the batch was created
    unit_cost, total_cost = complete_batch_costs(
        db,
        tenant_id=access.tenant_id,
        batch=batch,
        recipe=recipe,
        extra_components=payload.extra_cost_components,
    )
    batch.actual_unit_cost = unit_cost
    batch.actual_total_cost = total_cost
    batch.version += 1
    batch.last_modified_by = access.membership_id

    db.add(
        FinishedGoodsStock(
            tenant_id=access.tenant_id,
            last_modified_by=access.membership_id,
            batch_id=batch.id,
            product_id=batch.product_id,
            quantity_produced=payload.actual_output_qty,
            quantity_available=payload.actual_output_qty,
            quantity_damaged=payload.damaged_qty,
            unit_cost=unit_cost,
            expiry_date=batch.expiry_date,
            warehouse_location=batch.warehouse_location,
        )
    )
    db.flush()
    return _to_batch_out(db, batch)


@router.post("/mouneh/batches/{batch_id}/consume", response_model=ProductionBatchOut)
def consume_batch_inputs(
    batch_id: str,
    payload: BatchConsumeRequest,
    access: AccessContext = Depends(require_permission("mouneh_production", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
) -> ProductionBatchOut:
    """Records what was actually used for one or more materials, ahead of
    completion. Stock is only deducted once, at /complete — this endpoint
    just checks (without applying) whether the requested quantity would
    take a stock-tracked material negative.
    """
    batch = _load_batch_or_404(db, batch_id)

    for line in payload.lines:
        if not payload.allow_negative:
            material = db.execute(
                select(RawMaterial).where(
                    RawMaterial.tenant_id == access.tenant_id, RawMaterial.id == uuid.UUID(line.material_id)
                )
            ).scalar_one_or_none()
            if (
                material is not None
                and material.stock_tracking_enabled
                and float(material.current_stock) - line.actual_qty < 0
            ):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"This would take {material.name} below zero "
                        f"({float(material.current_stock) - line.actual_qty:.2f} {material.unit}). "
                        "Check the quantity, or confirm the override."
                    ),
                )

        row = db.execute(
            select(BatchInputConsumption).where(
                BatchInputConsumption.batch_id == batch.id,
                BatchInputConsumption.material_id == line.material_id,
            )
        ).scalar_one_or_none()
        unit_cost = line.unit_cost if line.unit_cost is not None else (row.unit_cost if row else 0)
        if row is None:
            row = BatchInputConsumption(
                tenant_id=access.tenant_id,
                batch_id=batch.id,
                material_id=line.material_id,
                planned_qty=line.actual_qty,
                unit_cost=unit_cost,
            )
            db.add(row)
        row.actual_qty = line.actual_qty
        row.unit_cost = unit_cost
        row.total_cost = line.actual_qty * float(unit_cost)

    db.flush()
    return _to_batch_out(db, batch)


# --- Finished goods --------------------------------------------------------


def _to_finished_goods_out(r: FinishedGoodsStock) -> FinishedGoodsStockOut:
    return FinishedGoodsStockOut(
        id=str(r.id),
        batch_id=str(r.batch_id),
        product_id=str(r.product_id),
        quantity_produced=float(r.quantity_produced),
        quantity_available=float(r.quantity_available),
        quantity_reserved=float(r.quantity_reserved),
        quantity_sold=float(r.quantity_sold),
        quantity_damaged=float(r.quantity_damaged),
        quantity_expired=float(r.quantity_expired),
        unit_cost=float(r.unit_cost),
        expiry_date=r.expiry_date,
        warehouse_location=r.warehouse_location,
    )


@router.get("/mouneh/finished-goods", response_model=list[FinishedGoodsStockOut])
def list_finished_goods(
    product_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("mouneh_inventory", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[FinishedGoodsStockOut]:
    stmt = select(FinishedGoodsStock).where(FinishedGoodsStock.deleted_at.is_(None))
    if product_id:
        stmt = stmt.where(FinishedGoodsStock.product_id == uuid.UUID(product_id))
    rows = db.execute(stmt.order_by(FinishedGoodsStock.created_at)).scalars().all()
    return [_to_finished_goods_out(row) for row in rows]


# --- Sales -----------------------------------------------------------------


def _to_sale_out(s: MounehSale) -> MounehSaleOut:
    return MounehSaleOut(
        id=str(s.id),
        farm_id=str(s.tenant_id),
        product_id=str(s.product_id),
        batch_id=str(s.batch_id),
        finished_goods_stock_id=str(s.finished_goods_stock_id),
        quantity=float(s.quantity),
        unit_price=float(s.unit_price),
        discount=float(s.discount),
        customer_id=str(s.customer_id) if s.customer_id else None,
        channel=s.channel,
        cost_per_unit=float(s.cost_per_unit),
        revenue=float(s.revenue),
        margin=float(s.margin),
        sold_at=s.sold_at,
    )


@router.get("/mouneh/sales", response_model=list[MounehSaleOut])
def list_mouneh_sales(
    product_id: str | None = Query(default=None),
    _access: AccessContext = Depends(require_permission("mouneh_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[MounehSaleOut]:
    stmt = select(MounehSale).where(MounehSale.deleted_at.is_(None))
    if product_id:
        stmt = stmt.where(MounehSale.product_id == uuid.UUID(product_id))
    rows = db.execute(stmt.order_by(MounehSale.sold_at.desc())).scalars().all()
    return [_to_sale_out(row) for row in rows]


@router.post("/mouneh/sales", response_model=MounehSaleOut, status_code=201)
def record_mouneh_sale(
    payload: MounehSaleCreate,
    access: AccessContext = Depends(require_permission("mouneh_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> MounehSaleOut:
    if payload.finished_goods_stock_id:
        stock = db.get(FinishedGoodsStock, uuid.UUID(payload.finished_goods_stock_id))
    else:
        stock = db.execute(
            select(FinishedGoodsStock)
            .where(
                FinishedGoodsStock.product_id == uuid.UUID(payload.product_id),
                FinishedGoodsStock.deleted_at.is_(None),
                FinishedGoodsStock.quantity_available > 0,
            )
            .order_by(FinishedGoodsStock.created_at)
        ).scalars().first()
    if stock is None or stock.deleted_at is not None:
        raise HTTPException(status_code=422, detail="No finished-goods stock available to sell.")
    if float(stock.quantity_available) < payload.quantity:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Only {float(stock.quantity_available):.2f} units available in this stock lot — "
                f"can't sell {payload.quantity}."
            ),
        )

    revenue = payload.quantity * payload.unit_price - payload.discount
    cost_per_unit = float(stock.unit_cost)
    margin = revenue - payload.quantity * cost_per_unit

    sale = MounehSale(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        product_id=uuid.UUID(payload.product_id),
        batch_id=stock.batch_id,
        finished_goods_stock_id=stock.id,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
        discount=payload.discount,
        customer_id=uuid.UUID(payload.customer_id) if payload.customer_id else None,
        channel=payload.channel,
        cost_per_unit=cost_per_unit,
        revenue=revenue,
        margin=margin,
        sold_at=now_utc(),
    )
    db.add(sale)

    stock.quantity_available = float(stock.quantity_available) - payload.quantity
    stock.quantity_sold = float(stock.quantity_sold) + payload.quantity
    stock.version += 1
    stock.last_modified_by = access.membership_id

    db.flush()
    return _to_sale_out(sale)
