"""Crop planning (fields, crops, plantings) plus POST /harvest — "Record
Daily Harvest" — which records the day's pick and moves the sellable part
into real InventoryItem stock, so "N kg ready for sale" on the Produce
screen is stock that exists rather than a number someone typed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_permission
from app.farmos.production_models import Crop, CropPlanting, DailyHarvest
from app.farmos.schemas import (
    CropCreate,
    CropOut,
    CropPlantingCreate,
    CropPlantingOut,
    CropPlantingUpdate,
    DailyHarvestCreate,
    DailyHarvestOut,
    FieldCreate,
    FieldOut,
    FieldUpdate,
)
from app.tenant_api.models import Field, InventoryItem, InventoryMovement

router = APIRouter()


def _parse_uuid(value: str, *, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"'{value}' is not a valid {what}.") from exc


# --- Fields ----------------------------------------------------------------


def to_field_out(field: Field) -> FieldOut:
    return FieldOut(
        id=str(field.id),
        name=field.name,
        crop_type=field.crop_type,
        area_value=float(field.area_value) if field.area_value is not None else None,
        area_unit=field.area_unit,
        stage=field.stage,
        expected_harvest_date=field.expected_harvest_date,
        est_yield_kg=float(field.est_yield_kg) if field.est_yield_kg is not None else None,
        field_code=field.field_code,
        location_label=field.location_label,
        soil_type=field.soil_type,
        irrigation_method=field.irrigation_method,
        status=field.status,
        notes=field.notes,
    )


@router.post("/fields", response_model=FieldOut, status_code=201)
def create_field(
    payload: FieldCreate,
    access: AccessContext = Depends(require_permission("agriculture", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> FieldOut:
    field = Field(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        **payload.model_dump(),
    )
    db.add(field)
    db.flush()
    return to_field_out(field)


@router.patch("/fields/{field_id}", response_model=FieldOut)
def update_field(
    field_id: str,
    payload: FieldUpdate,
    access: AccessContext = Depends(require_permission("agriculture", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
) -> FieldOut:
    field = db.get(Field, _parse_uuid(field_id, what="field id"))
    if field is None or field.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Field not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(field, key, value)
    field.version += 1
    field.last_modified_by = access.membership_id
    db.flush()
    return to_field_out(field)


# --- Crops -----------------------------------------------------------------


def _to_crop_out(crop: Crop) -> CropOut:
    return CropOut(
        id=str(crop.id),
        name=crop.name,
        category=crop.category,
        default_cycle_days=crop.default_cycle_days,
        active=crop.active,
    )


@router.get("/crops", response_model=list[CropOut])
def list_crops(
    include_inactive: bool = Query(default=False),
    _access: AccessContext = Depends(require_permission("agriculture", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[CropOut]:
    stmt = select(Crop).where(Crop.deleted_at.is_(None))
    if not include_inactive:
        stmt = stmt.where(Crop.active.is_(True))
    rows = db.execute(stmt.order_by(Crop.name)).scalars().all()
    return [_to_crop_out(row) for row in rows]


@router.post("/crops", response_model=CropOut, status_code=201)
def create_crop(
    payload: CropCreate,
    access: AccessContext = Depends(require_permission("agriculture", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> CropOut:
    crop = Crop(tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump())
    db.add(crop)
    db.flush()
    return _to_crop_out(crop)


@router.delete("/crops/{crop_id}", status_code=204, response_model=None)
def archive_crop(
    crop_id: str,
    access: AccessContext = Depends(require_permission("agriculture", "delete")),
    db: Session = Depends(get_farmos_tenant_db),
) -> None:
    crop = db.get(Crop, _parse_uuid(crop_id, what="crop id"))
    if crop is None or crop.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Crop not found.")
    # Archived, not soft-deleted: a crop stays a valid reference for its
    # past plantings/harvests even once retired from new-planting pickers.
    crop.active = False
    crop.version += 1
    crop.last_modified_by = access.membership_id
    db.flush()


# --- Crop plantings ----------------------------------------------------


def _to_planting_out(p: CropPlanting) -> CropPlantingOut:
    return CropPlantingOut(
        id=str(p.id),
        field_id=str(p.field_id),
        crop_id=str(p.crop_id),
        variety=p.variety,
        planted_area=float(p.planted_area) if p.planted_area is not None else None,
        area_unit=p.area_unit,
        planted_date=p.planted_date,
        expected_harvest_date=p.expected_harvest_date,
        expected_yield_kg=float(p.expected_yield_kg) if p.expected_yield_kg is not None else None,
        stage=p.stage,
        status=p.status,
        notes=p.notes,
        created_at=p.created_at,
    )


@router.get("/crop-plantings", response_model=list[CropPlantingOut])
def list_plantings(
    field_id: str | None = Query(default=None),
    active_only: bool = Query(default=True),
    _access: AccessContext = Depends(require_permission("agriculture", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[CropPlantingOut]:
    stmt = select(CropPlanting).where(CropPlanting.deleted_at.is_(None))
    if field_id:
        stmt = stmt.where(CropPlanting.field_id == _parse_uuid(field_id, what="field id"))
    if active_only:
        stmt = stmt.where(CropPlanting.status == "active")
    rows = db.execute(stmt.order_by(CropPlanting.planted_date.desc().nulls_last())).scalars().all()
    return [_to_planting_out(row) for row in rows]


@router.post("/crop-plantings", response_model=CropPlantingOut, status_code=201)
def create_planting(
    payload: CropPlantingCreate,
    access: AccessContext = Depends(require_permission("agriculture", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> CropPlantingOut:
    planting = CropPlanting(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        field_id=_parse_uuid(payload.field_id, what="field id"),
        crop_id=_parse_uuid(payload.crop_id, what="crop id"),
        variety=payload.variety,
        planted_area=payload.planted_area,
        area_unit=payload.area_unit,
        planted_date=payload.planted_date,
        expected_harvest_date=payload.expected_harvest_date,
        expected_yield_kg=payload.expected_yield_kg,
        stage=payload.stage,
        notes=payload.notes,
    )
    db.add(planting)
    db.flush()
    return _to_planting_out(planting)


@router.patch("/crop-plantings/{planting_id}", response_model=CropPlantingOut)
def update_planting(
    planting_id: str,
    payload: CropPlantingUpdate,
    access: AccessContext = Depends(require_permission("agriculture", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
) -> CropPlantingOut:
    planting = db.get(CropPlanting, _parse_uuid(planting_id, what="planting id"))
    if planting is None or planting.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Planting not found.")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(planting, key, value)
    planting.version += 1
    planting.last_modified_by = access.membership_id
    db.flush()
    return _to_planting_out(planting)


# --- Daily harvest -------------------------------------------------------


def _resolve_product_name(db: Session, payload: DailyHarvestCreate) -> str:
    if payload.product_name:
        return payload.product_name
    crop_id = payload.crop_id
    if crop_id is None and payload.planting_id:
        planting = db.get(CropPlanting, _parse_uuid(payload.planting_id, what="planting id"))
        if planting is not None:
            crop_id = str(planting.crop_id)
    if crop_id:
        crop = db.get(Crop, _parse_uuid(crop_id, what="crop id"))
        if crop is not None:
            return crop.name
    raise HTTPException(
        status_code=422, detail="Tell us what was harvested — a product name, crop, or planting."
    )


@router.post("/harvest", response_model=DailyHarvestOut, status_code=201)
def record_daily_harvest(
    payload: DailyHarvestCreate,
    access: AccessContext = Depends(require_permission("produce_harvest", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> DailyHarvestOut:
    product_name = _resolve_product_name(db, payload)
    sellable_quantity = (
        payload.sellable_quantity
        if payload.sellable_quantity is not None
        else payload.total_quantity - payload.waste_quantity
    )
    recorded_at = payload.recorded_at or datetime.now(timezone.utc)

    harvest = DailyHarvest(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        field_id=_parse_uuid(payload.field_id, what="field id"),
        product_name=product_name,
        total_quantity=payload.total_quantity,
        sellable_quantity=sellable_quantity,
        waste_quantity=payload.waste_quantity,
        unit=payload.unit,
        recorded_at=recorded_at,
    )
    db.add(harvest)

    item = db.execute(
        select(InventoryItem).where(
            InventoryItem.deleted_at.is_(None),
            InventoryItem.name == product_name,
            InventoryItem.unit == payload.unit,
        )
    ).scalar_one_or_none()
    if item is None:
        item = InventoryItem(
            tenant_id=access.tenant_id,
            name=product_name,
            category="produce",
            unit=payload.unit,
            current_qty=0,
            last_modified_by=access.membership_id,
        )
        db.add(item)
        db.flush()

    if sellable_quantity:
        db.add(
            InventoryMovement(
                tenant_id=access.tenant_id,
                inventory_item_id=item.id,
                quantity_delta=sellable_quantity,
                reason=f"Daily harvest — {product_name}",
                linked_entity_type="daily_harvest",
                linked_entity_id=str(harvest.id),
                occurred_at=recorded_at,
                last_modified_by=access.membership_id,
            )
        )
        item.current_qty = float(item.current_qty) + sellable_quantity
        item.version += 1
        item.last_modified_by = access.membership_id

    harvest.inventory_item_id = item.id
    db.flush()

    return DailyHarvestOut(
        id=str(harvest.id),
        field_id=str(harvest.field_id),
        product_name=harvest.product_name,
        total_quantity=float(harvest.total_quantity),
        sellable_quantity=float(harvest.sellable_quantity),
        waste_quantity=float(harvest.waste_quantity),
        unit=harvest.unit,
        recorded_at=harvest.recorded_at,
        inventory_item_id=str(harvest.inventory_item_id),
        inventory_qty_after=float(item.current_qty),
    )
