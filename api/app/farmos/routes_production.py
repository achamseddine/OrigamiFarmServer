"""Egg, harvest (simple ledger), milk records, and the read-only field
listing this "production" doc group shares with app/farmos/routes_agriculture.py.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.production_models import EggRecord, HarvestRecord, MilkRecord
from app.farmos.routes_agriculture import to_field_out
from app.farmos.schemas import (
    EggRecordCreate,
    EggRecordOut,
    FieldOut,
    HarvestRecordCreate,
    HarvestRecordOut,
    MilkRecordCreate,
    MilkRecordOut,
)
from app.tenant_api.models import Animal, Field

router = APIRouter()


# --- Eggs --------------------------------------------------------------


def _to_egg_out(e: EggRecord) -> EggRecordOut:
    return EggRecordOut(
        id=str(e.id),
        flock_id=e.flock_id,
        total_eggs=e.total_eggs,
        sellable_eggs=e.sellable_eggs,
        broken_eggs=e.broken_eggs,
        consumed=e.consumed,
        hatched=e.hatched,
        wasted=e.wasted,
        recorded_at=e.recorded_at,
    )


@router.get("/production/eggs", response_model=list[EggRecordOut])
def list_egg_records(
    farm_id: str = Query(...),
    days: int = Query(default=30),
    access: AccessContext = Depends(require_permission("egg_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[EggRecordOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(EggRecord)
        .where(EggRecord.deleted_at.is_(None), EggRecord.recorded_at >= since)
        .order_by(EggRecord.recorded_at.desc())
    ).scalars().all()
    return [_to_egg_out(row) for row in rows]


@router.post("/production/eggs", response_model=EggRecordOut, status_code=201)
def record_eggs(
    payload: EggRecordCreate,
    access: AccessContext = Depends(require_permission("egg_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> EggRecordOut:
    accounted = (
        payload.sellable_eggs + payload.broken_eggs + payload.consumed + payload.hatched + payload.wasted
    )
    if accounted > payload.total_eggs:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Sellable + broken + consumed + hatched + wasted ({accounted}) "
                f"can't be more than the total eggs collected ({payload.total_eggs})."
            ),
        )
    record = EggRecord(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        flock_id=payload.flock_id,
        total_eggs=payload.total_eggs,
        sellable_eggs=payload.sellable_eggs,
        broken_eggs=payload.broken_eggs,
        consumed=payload.consumed,
        hatched=payload.hatched,
        wasted=payload.wasted,
        recorded_at=payload.recorded_at or datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    return _to_egg_out(record)


# --- Fields (read-only here; see routes_agriculture.py for writes) -----


@router.get("/production/fields", response_model=list[FieldOut])
def list_production_fields(
    farm_id: str = Query(...),
    access: AccessContext = Depends(require_permission("agriculture", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[FieldOut]:
    check_farm_id(farm_id, access)
    rows = db.execute(
        select(Field).where(Field.deleted_at.is_(None)).order_by(Field.name)
    ).scalars().all()
    return [to_field_out(row) for row in rows]


# --- Harvest (simple ledger) ---------------------------------------------


def _to_harvest_out(h: HarvestRecord) -> HarvestRecordOut:
    return HarvestRecordOut(
        id=str(h.id),
        field_id=str(h.field_id),
        product_name=h.product_name,
        quantity=float(h.quantity),
        unit=h.unit,
        waste_qty=float(h.waste_qty),
        destination=h.destination,
        recorded_at=h.recorded_at,
    )


@router.get("/production/harvest", response_model=list[HarvestRecordOut])
def list_harvest_records(
    farm_id: str = Query(...),
    days: int = Query(default=90),
    access: AccessContext = Depends(require_permission("produce_harvest", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[HarvestRecordOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(HarvestRecord)
        .where(HarvestRecord.deleted_at.is_(None), HarvestRecord.recorded_at >= since)
        .order_by(HarvestRecord.recorded_at.desc())
    ).scalars().all()
    return [_to_harvest_out(row) for row in rows]


@router.post("/production/harvest", response_model=HarvestRecordOut, status_code=201)
def record_harvest(
    payload: HarvestRecordCreate,
    access: AccessContext = Depends(require_permission("produce_harvest", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> HarvestRecordOut:
    record = HarvestRecord(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        field_id=uuid.UUID(payload.field_id),
        product_name=payload.product_name,
        quantity=payload.quantity,
        unit=payload.unit,
        waste_qty=payload.waste_qty,
        destination=payload.destination,
        recorded_at=payload.recorded_at or datetime.now(timezone.utc),
    )
    db.add(record)
    db.flush()
    return _to_harvest_out(record)


# --- Milk ----------------------------------------------------------------


def _to_milk_out(m: MilkRecord, *, under_withdrawal_warning: bool = False) -> MilkRecordOut:
    return MilkRecordOut(
        id=str(m.id),
        animal_id=str(m.animal_id),
        session=m.session,
        liters=float(m.liters),
        quality_status=m.quality_status,
        destination=m.destination,
        recorded_at=m.recorded_at,
        recorded_by=str(m.recorded_by) if m.recorded_by else None,
        under_withdrawal_warning=under_withdrawal_warning,
    )


@router.get("/production/milk", response_model=list[MilkRecordOut])
def list_milk_records(
    farm_id: str = Query(...),
    days: int = Query(default=30),
    access: AccessContext = Depends(require_permission("milk_production", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[MilkRecordOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(MilkRecord)
        .where(MilkRecord.deleted_at.is_(None), MilkRecord.recorded_at >= since)
        .order_by(MilkRecord.recorded_at.desc())
    ).scalars().all()
    return [_to_milk_out(row) for row in rows]


@router.post("/production/milk", response_model=MilkRecordOut, status_code=201)
def record_milk(
    payload: MilkRecordCreate,
    access: AccessContext = Depends(require_permission("milk_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> MilkRecordOut:
    animal = db.get(Animal, uuid.UUID(payload.animal_id))
    now = datetime.now(timezone.utc)
    under_withdrawal = (
        animal is not None and animal.withdrawal_until is not None and animal.withdrawal_until > now
    )

    # RULE-WITHDRAWAL (tech spec §14): milk from an animal under withdrawal
    # must be hard-blocked from a sale destination, not just warned about.
    if under_withdrawal and payload.destination == "sale":
        raise HTTPException(
            status_code=422,
            detail=(
                "This animal is under a treatment withdrawal period until "
                f"{animal.withdrawal_until:%Y-%m-%d}. Its milk can't be marked for sale."
            ),
        )

    record = MilkRecord(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=uuid.UUID(payload.animal_id),
        session=payload.session,
        liters=payload.liters,
        quality_status=payload.quality_status,
        destination=payload.destination,
        recorded_at=payload.recorded_at or now,
        recorded_by=uuid.UUID(payload.recorded_by) if payload.recorded_by else access.user_id,
    )
    db.add(record)
    db.flush()
    return _to_milk_out(record, under_withdrawal_warning=under_withdrawal)
