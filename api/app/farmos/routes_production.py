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

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import capability_service, duplicates, livestock_service, livestock_state_service
from app.farmos import feed_performance_service as perf
from app.farmos import feeding_program_service as programs
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_farmos_tenant_db,
    require_any_permission,
    require_permission,
)
from app.farmos.livestock_catalog import Cap
from app.farmos.livestock_models import AnimalGroup
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
from app.tenant_api.models import Field

router = APIRouter()

# Reads: the record's own module, plus Produce & Harvest — the tablet's
# ProductionProvider loads fields, eggs and harvest in one batch for
# whoever holds it, and the egg screen reads from that provider.
_FIELDS_VIEW = require_any_permission(("agriculture", "view"), ("produce_harvest", "view"))
_EGGS_VIEW = require_any_permission(("egg_production", "view"), ("produce_harvest", "view"))
_HARVEST_VIEW = require_any_permission(("produce_harvest", "view"), ("agriculture", "view"))
# Writes: the same modules the tablet's record sheet offers each action to.
_HARVEST_CREATE = require_any_permission(("produce_harvest", "create"), ("agriculture", "create"))


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
    access: AccessContext = Depends(_EGGS_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[EggRecordOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.execute(
            select(EggRecord)
            .where(EggRecord.deleted_at.is_(None), EggRecord.recorded_at >= since)
            .order_by(EggRecord.recorded_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_egg_out(row) for row in rows]


@router.post("/production/eggs", response_model=EggRecordOut, status_code=201)
def record_eggs(
    payload: EggRecordCreate,
    access: AccessContext = Depends(require_permission("egg_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
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
    # Laying is a capability (generic animal model §12): when the flock is
    # a registered animal group, a broiler flock or a herd of goats cannot
    # have an egg record. A free-text flock label is accepted as before.
    try:
        group = db.get(AnimalGroup, uuid.UUID(payload.flock_id))
    except ValueError:
        group = None
    if group is not None and group.deleted_at is None:
        try:
            cap_set = capability_service.resolve_for_group(group)
        except capability_service.UnknownSpecies:
            cap_set = None
        livestock_service.check_production_capability(
            cap_set, Cap.EGG_PRODUCTION, group.name, configured=True
        )
    # The same count from the same flock within half an hour is asked about first.
    at = payload.recorded_at or datetime.now(timezone.utc)
    lo, hi = duplicates.near(at)
    earlier = db.scalar(
        select(EggRecord).where(
            EggRecord.deleted_at.is_(None),
            EggRecord.flock_id == payload.flock_id,
            EggRecord.total_eggs == payload.total_eggs,
            EggRecord.recorded_at >= lo,
            EggRecord.recorded_at <= hi,
        )
    )
    flock_name = group.name if group is not None and group.deleted_at is None else payload.flock_id
    match = duplicates.guard(
        earlier,
        f"{payload.total_eggs} eggs from {flock_name} were already recorded within half an hour.",
        confirm=payload.confirm_duplicate,
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
        recorded_at=at,
    )
    db.add(record)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="flock",
        entity_id=payload.flock_id,
        match_id=match.id if match else None,
        what=f"eggs:{record.id}",
        module_code="egg_production",
    )
    # §12 ProductionRecorded: the flock's feed-performance monitors read again.
    if group is not None and group.deleted_at is None:
        perf.on_production_recorded(db, access, "group", group)
    return _to_egg_out(record)


# --- Fields (read-only here; see routes_agriculture.py for writes) -----


@router.get("/production/fields", response_model=list[FieldOut])
def list_production_fields(
    farm_id: str = Query(...),
    access: AccessContext = Depends(_FIELDS_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[FieldOut]:
    check_farm_id(farm_id, access)
    rows = db.execute(select(Field).where(Field.deleted_at.is_(None)).order_by(Field.name)).scalars().all()
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
    access: AccessContext = Depends(_HARVEST_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[HarvestRecordOut]:
    check_farm_id(farm_id, access)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.execute(
            select(HarvestRecord)
            .where(HarvestRecord.deleted_at.is_(None), HarvestRecord.recorded_at >= since)
            .order_by(HarvestRecord.recorded_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_harvest_out(row) for row in rows]


@router.post("/production/harvest", response_model=HarvestRecordOut, status_code=201)
def record_harvest(
    payload: HarvestRecordCreate,
    access: AccessContext = Depends(_HARVEST_CREATE),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> HarvestRecordOut:
    try:
        field = db.get(Field, uuid.UUID(payload.field_id))
    except ValueError:
        field = None
    if field is None or field.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Field not found.")
    at = payload.recorded_at or datetime.now(timezone.utc)
    lo, hi = duplicates.near(at)
    earlier = db.scalar(
        select(HarvestRecord).where(
            HarvestRecord.deleted_at.is_(None),
            HarvestRecord.field_id == field.id,
            HarvestRecord.product_name == payload.product_name,
            HarvestRecord.quantity == payload.quantity,
            HarvestRecord.recorded_at >= lo,
            HarvestRecord.recorded_at <= hi,
        )
    )
    match = duplicates.guard(
        earlier,
        f"{payload.quantity:g} {payload.unit} of {payload.product_name} from {field.name} "
        "was already recorded within half an hour.",
        confirm=payload.confirm_duplicate,
    )
    record = HarvestRecord(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        field_id=field.id,
        product_name=payload.product_name,
        quantity=payload.quantity,
        unit=payload.unit,
        waste_qty=payload.waste_qty,
        destination=payload.destination,
        recorded_at=at,
    )
    db.add(record)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="field",
        entity_id=str(field.id),
        match_id=match.id if match else None,
        what=f"harvest:{record.id}",
        module_code="produce_harvest",
    )
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
    rows = (
        db.execute(
            select(MilkRecord)
            .where(MilkRecord.deleted_at.is_(None), MilkRecord.recorded_at >= since)
            .order_by(MilkRecord.recorded_at.desc())
        )
        .scalars()
        .all()
    )
    return [_to_milk_out(row) for row in rows]


@router.post("/production/milk", response_model=MilkRecordOut, status_code=201)
def record_milk(
    payload: MilkRecordCreate,
    access: AccessContext = Depends(require_permission("milk_production", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> MilkRecordOut:
    """Validation rule (tech spec §14): "Milk from an animal under
    withdrawal must show a hard warning or be blocked from sale
    destination." This endpoint hard-blocks it, matching RULE-WITHDRAWAL
    in the recommendation engine: while the animal is under withdrawal its
    milk is recorded as `withheld` or not at all.
    """
    animal = livestock_service.load_animal_or_404(db, payload.animal_id)
    now = datetime.now(timezone.utc)
    livestock_state_service.ensure_on_farm(animal, "milk")
    # Milk is a capability (generic animal model §12): a bull, a hen or
    # a mare cannot have a milk record; a cow registered with a meat
    # profile cannot either. An animal with no profile on record is
    # held to biology only.
    livestock_service.check_production_capability(
        livestock_service.capabilities_for_animal(animal),
        Cap.MILK_PRODUCTION,
        animal.name,
        configured=animal.management_profile is not None,
    )
    withdrawal_until = livestock_service.ensure_utc(animal.withdrawal_until)
    under_withdrawal = withdrawal_until is not None and withdrawal_until > now

    # RULE-WITHDRAWAL (tech spec §14): milk from an animal under withdrawal
    # must not enter stored, sold, processed or consumed stock — a
    # server-side safety gate, including when an offline tablet is stale.
    if under_withdrawal and payload.destination != "withheld":
        assert withdrawal_until is not None
        raise HTTPException(
            status_code=422,
            detail=(
                f"{animal.name} is under withdrawal until {withdrawal_until.isoformat()}. "
                "Record this milk as withheld; it must not enter stored, sold, processed or consumed stock."
            ),
        )

    # The same cow, the same session, the same farm day: two people (or one
    # person twice) recording one milking. A second milking is confirmed.
    at = livestock_service.ensure_utc(payload.recorded_at) or now
    tz = duplicates.tenant_timezone(control_db, access.tenant_id)
    day_start, day_end = duplicates.farm_day(tz, at)
    earlier = db.scalar(
        select(MilkRecord)
        .where(
            MilkRecord.animal_id == animal.id,
            MilkRecord.session == payload.session,
            MilkRecord.recorded_at >= day_start,
            MilkRecord.recorded_at < day_end,
            MilkRecord.deleted_at.is_(None),
        )
        .order_by(MilkRecord.recorded_at)
        .limit(1)
    )
    match = duplicates.guard(
        earlier,
        (
            f"{animal.name}'s {payload.session} milk is already recorded for this day "
            f"({float(earlier.liters):g} L)."
        )
        if earlier
        else "",
        confirm=payload.confirm_duplicate,
    )

    record = MilkRecord(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=animal.id,
        session=payload.session,
        liters=payload.liters,
        quality_status=payload.quality_status,
        destination=payload.destination,
        recorded_at=at,
        recorded_by=uuid.UUID(payload.recorded_by) if payload.recorded_by else access.user_id,
    )
    db.add(record)
    db.flush()
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="animal",
        entity_id=str(animal.id),
        match_id=match.id if match else None,
        what=f"milk:{record.id}",
        module_code="milk_production",
    )
    if under_withdrawal:
        record_audit_event(
            control_db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="milk.withheld",
            entity_type="animal",
            entity_id=str(animal.id),
            module_code="milk_production",
            summary=f"Recorded {payload.liters:g} L of {animal.name}'s milk as withheld (under withdrawal)",
        )
    # A yield that crosses a production band asks for a feeding review
    # (feed architecture §11) — a task, never an automatic ration change.
    programs.review_after_milk(db, access, animal)
    # §12 ProductionRecorded: the animal's and its herd's monitors read again.
    perf.on_production_recorded(db, access, "animal", animal)
    return _to_milk_out(record, under_withdrawal_warning=under_withdrawal)
