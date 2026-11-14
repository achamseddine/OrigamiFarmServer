"""Reproduction and lactation endpoints (OrigamiFarmOS
database/REPRODUCTION-SCHEMA.md, PRODUCTION-SCHEMA.md §8, generic animal
model §5).

Every write is checked against the animal's resolved capabilities by
app/farmos/reproduction_service.py; the tablet offers the same actions from
the same `summary.applies` flags, but hiding a button is never the guard.
Responses carry the animal's refreshed reproduction summary so a screen
can redraw from one call.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import duplicates, livestock_service
from app.farmos import reproduction_models as rm
from app.farmos import reproduction_service as repro
from app.farmos.deps import AccessContext, get_farmos_tenant_db, require_any_permission, require_permission
from app.farmos.livestock_service import ensure_utc
from app.farmos.livestock_state_service import LivestockError
from app.farmos.reproduction_schemas import (
    BirthIn,
    BreedingIn,
    BreedingVoidIn,
    DryOffIn,
    HatchIn,
    IncubationCancelIn,
    IncubationIn,
    LactationCorrectIn,
    LactationStartIn,
    OffspringRegisterIn,
    PregnancyCheckIn,
    PregnancyLossIn,
)
from app.tenant_api.models import Animal

router = APIRouter()

_VIEW = require_any_permission(
    ("animals", "view"),
    ("animal_health", "view"),
    ("milk_production", "view"),
    ("feed_nutrition", "view"),
    ("egg_production", "view"),
)
# Breeding, checks and births are recorded by whoever looks after the
# animals; a pregnancy check is often the veterinarian's.
_RECORD = require_any_permission(("animals", "create"), ("animal_health", "create"))
_LACTATION = require_any_permission(("animals", "create"), ("milk_production", "create"))
_LACTATION_EDIT = require_any_permission(("animals", "edit"), ("milk_production", "edit"))
_CORRECT = require_permission("animals", "edit")
_INCUBATION = require_any_permission(("animals", "create"), ("egg_production", "create"))


def _audit(control_db: Session, access: AccessContext, **kwargs: Any) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        **kwargs,
    )


def _animal(db: Session, animal_id: str) -> Animal:
    return livestock_service.load_animal_or_404(db, animal_id)


def _summary(db: Session, animal: Animal) -> dict[str, Any]:
    return repro.summary(db, animal)


def _audit_newborns(
    control_db: Session, access: AccessContext, dam: Animal, registered: list[Animal]
) -> None:
    for child in registered:
        _audit(
            control_db,
            access,
            action="animal.created",
            entity_type="animal",
            entity_id=str(child.id),
            module_code="animals",
            summary=f"Registered {child.name}, born to {dam.name}",
        )


# ----------------------------------------------------------------- reads
@router.get("/animals/{animal_id}/reproduction")
def reproduction_record(
    animal_id: str,
    _access: AccessContext = Depends(_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict[str, Any]:
    """The animal's whole reproductive life: services, checks, pregnancies,
    births with their newborns, lactations and recorded parentage."""
    return repro.timeline(db, _animal(db, animal_id))


@router.get("/reproduction/overview")
def reproduction_overview(
    access: AccessContext = Depends(_VIEW), db: Session = Depends(get_farmos_tenant_db)
) -> dict[str, Any]:
    """Who is due, who is waiting for a pregnancy check, recent births, how
    many are in milk and what is in the incubator."""
    return repro.overview(db, access.tenant_id)


# ----------------------------------------------------------------- breeding
@router.post("/animals/{animal_id}/breedings", status_code=201)
def record_breeding(
    animal_id: str,
    payload: BreedingIn,
    access: AccessContext = Depends(_RECORD),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    animal = _animal(db, animal_id)
    repro.ensure_farm_user(control_db, access.tenant_id, payload.technician_user_id, "technician")
    tz = duplicates.tenant_timezone(control_db, access.tenant_id)
    day_start, day_end = duplicates.farm_day(tz, payload.occurred_at)
    earlier = db.scalar(
        select(rm.BreedingEvent).where(
            rm.BreedingEvent.female_animal_id == animal.id,
            rm.BreedingEvent.event_type == payload.event_type,
            rm.BreedingEvent.status == "recorded",
            rm.BreedingEvent.occurred_at >= day_start,
            rm.BreedingEvent.occurred_at < day_end,
        )
    )
    match = duplicates.guard(
        earlier,
        f"{animal.name} already has a {payload.event_type.replace('_', ' ').lower()} recorded on this day.",
        confirm=payload.confirm_duplicate,
    )
    row = repro.record_breeding(
        db,
        access,
        animal,
        event_type=payload.event_type,
        at=payload.occurred_at,
        male_animal_id=payload.male_animal_id,
        sire_reference=payload.sire_reference,
        semen_batch_reference=payload.semen_batch_reference,
        technician_user_id=payload.technician_user_id,
        notes=payload.notes,
    )
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="animal",
        entity_id=str(animal.id),
        match_id=match.id if match else None,
        what=f"breeding:{row.id}",
        module_code="animals",
    )
    _audit(
        control_db,
        access,
        action="animal.breeding_recorded",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Recorded {payload.event_type.replace('_', ' ').lower()} on {animal.name}",
    )
    return {"breeding": repro.breeding_dict(db, row), "summary": _summary(db, animal)}


@router.post("/animals/{animal_id}/breedings/{breeding_id}/void")
def void_breeding(
    animal_id: str,
    breeding_id: str,
    payload: BreedingVoidIn,
    access: AccessContext = Depends(_CORRECT),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    animal = _animal(db, animal_id)
    pk = repro._parse_uuid(breeding_id)
    row = db.get(rm.BreedingEvent, pk) if pk is not None else None
    if row is None or row.female_animal_id != animal.id:
        raise LivestockError("Breeding record not found", 404)
    repro.void_breeding(db, access, row, reason=payload.reason)
    _audit(
        control_db,
        access,
        action="animal.breeding_voided",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Voided a {row.event_type.replace('_', ' ').lower()} on {animal.name}: {payload.reason}",
    )
    return {"breeding": repro.breeding_dict(db, row), "summary": _summary(db, animal)}


# ----------------------------------------------------------------- pregnancy
@router.post("/animals/{animal_id}/pregnancy-checks", status_code=201)
def record_pregnancy_check(
    animal_id: str,
    payload: PregnancyCheckIn,
    access: AccessContext = Depends(_RECORD),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    animal = _animal(db, animal_id)
    repro.ensure_farm_user(control_db, access.tenant_id, payload.examiner_user_id, "examiner")
    tz = duplicates.tenant_timezone(control_db, access.tenant_id)
    day_start, day_end = duplicates.farm_day(tz, payload.assessed_at)
    earlier = db.scalar(
        select(rm.PregnancyAssessment).where(
            rm.PregnancyAssessment.animal_id == animal.id,
            rm.PregnancyAssessment.result_code == payload.result_code,
            rm.PregnancyAssessment.assessed_at >= day_start,
            rm.PregnancyAssessment.assessed_at < day_end,
        )
    )
    duplicates.guard(
        earlier,
        f"A {payload.result_code.lower()} pregnancy check of {animal.name} is already recorded on this day.",
        confirm=payload.confirm_duplicate,
    )
    assessment, episode = repro.record_assessment(
        db,
        access,
        animal,
        assessment_type=payload.assessment_type,
        result_code=payload.result_code,
        at=payload.assessed_at,
        estimated_gestation_days=payload.estimated_gestation_days,
        expected_due_date=payload.expected_due_date,
        examiner_user_id=payload.examiner_user_id,
        notes=payload.notes,
    )
    _audit(
        control_db,
        access,
        action="animal.pregnancy_checked",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Recorded a {payload.result_code.lower()} pregnancy check on {animal.name}",
    )
    return {
        "assessment": repro.assessment_dict(assessment),
        "pregnancy": repro.pregnancy_dict(episode) if episode else None,
        "summary": _summary(db, animal),
    }


@router.post("/animals/{animal_id}/pregnancy-loss")
def record_pregnancy_loss(
    animal_id: str,
    payload: PregnancyLossIn,
    access: AccessContext = Depends(_RECORD),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    animal = _animal(db, animal_id)
    episode = repro.end_pregnancy(
        db, access, animal, outcome_code=payload.outcome_code, at=payload.occurred_at, notes=payload.notes
    )
    _audit(
        control_db,
        access,
        action="animal.pregnancy_ended",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Recorded a pregnancy {payload.outcome_code.lower()} on {animal.name}",
    )
    return {"pregnancy": repro.pregnancy_dict(episode), "summary": _summary(db, animal)}


# ----------------------------------------------------------------- birth
@router.post("/animals/{animal_id}/births", status_code=201)
def record_birth(
    animal_id: str,
    payload: BirthIn,
    access: AccessContext = Depends(_RECORD),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """A calving / lambing / kidding / foaling. Newborns marked
    `register_now` become animals in the same transaction — with the
    identifiers their species requires — and any refusal refuses the birth."""
    dam = _animal(db, animal_id)
    lo, hi = duplicates.near(payload.occurred_at, timedelta(days=30))
    earlier = db.scalar(
        select(rm.BirthEvent).where(
            rm.BirthEvent.dam_animal_id == dam.id,
            rm.BirthEvent.occurred_at >= lo,
            rm.BirthEvent.occurred_at <= hi,
        )
    )
    when = ensure_utc(earlier.occurred_at) if earlier else None
    match = duplicates.guard(
        earlier,
        f"A birth of {dam.name} is already recorded on {when.date().isoformat() if when else ''}; "
        "twins are one birth with two newborns.",
        confirm=payload.confirm_duplicate,
    )
    birth, extra, registered = repro.record_birth(
        db,
        access,
        dam,
        offspring=[o.model_dump() for o in payload.offspring],
        at=payload.occurred_at,
        assistance_code=payload.assistance_code,
        sire_animal_id=payload.sire_animal_id,
        sire_reference=payload.sire_reference,
        start_lactation=payload.start_lactation,
        notes=payload.notes,
    )
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="animal",
        entity_id=str(dam.id),
        match_id=match.id if match else None,
        what=f"birth:{birth.id}",
        module_code="animals",
    )
    _audit_newborns(control_db, access, dam, registered)
    _audit(
        control_db,
        access,
        action="animal.birth_recorded",
        entity_type="animal",
        entity_id=str(dam.id),
        module_code="animals",
        summary=(
            f"Recorded a birth of {dam.name}: {birth.live_count} live, {birth.stillborn_count} stillborn, "
            f"{len(registered)} registered"
        ),
    )
    return {"birth": repro.birth_dict(db, birth), **extra, "summary": _summary(db, dam)}


@router.post("/births/{birth_id}/offspring/{sequence_no}/register", status_code=201)
def register_offspring(
    birth_id: str,
    sequence_no: int,
    payload: OffspringRegisterIn,
    access: AccessContext = Depends(_RECORD),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    pk = repro._parse_uuid(birth_id)
    birth = db.get(rm.BirthEvent, pk) if pk is not None else None
    if birth is None or birth.deleted_at is not None:
        raise LivestockError("Birth not found", 404)
    child = repro.register_offspring(db, access, birth, sequence_no, payload.model_dump())
    dam = db.get(Animal, birth.dam_animal_id)
    if dam is not None:
        _audit_newborns(control_db, access, dam, [child])
    return {"birth": repro.birth_dict(db, birth), "offspring_animal_id": str(child.id)}


# ----------------------------------------------------------------- lactation
@router.post("/animals/{animal_id}/lactations", status_code=201)
def start_lactation(
    animal_id: str,
    payload: LactationStartIn,
    access: AccessContext = Depends(_LACTATION),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """A lactation that did not start from a recorded birth. After a
    recorded birth the dam's lactation starts by itself."""
    animal = _animal(db, animal_id)
    row = repro.start_lactation(
        db, access, animal, at=payload.started_at, lactation_number=payload.lactation_number
    )
    _audit(
        control_db,
        access,
        action="animal.lactation_started",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="milk_production",
        summary=f"Started lactation {row.lactation_number} of {animal.name}",
    )
    return {"lactation": repro.lactation_dict(row), "summary": _summary(db, animal)}


@router.patch("/animals/{animal_id}/lactation")
def correct_lactation(
    animal_id: str,
    payload: LactationCorrectIn,
    access: AccessContext = Depends(_LACTATION_EDIT),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """Corrects the calving / start-of-milking date of the current lactation
    (audited, with the old value). A lactation a recorded birth started is
    corrected through that birth."""
    animal = _animal(db, animal_id)
    row, before = repro.correct_lactation_start(db, access, animal, started_at=payload.started_at)
    new_at = ensure_utc(row.started_at)
    assert new_at is not None
    _audit(
        control_db,
        access,
        action="animal.lactation_corrected",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="milk_production",
        summary=f"Corrected {animal.name}'s calving / start of milking to {new_at.date().isoformat()}"
        + (f": {payload.reason}" if payload.reason else ""),
        changes={
            "lactation_started_at": {
                "from": before.isoformat() if before else None,
                "to": new_at.isoformat(),
            }
        },
    )
    return {"lactation": repro.lactation_dict(row), "summary": _summary(db, animal)}


@router.post("/animals/{animal_id}/dry-off")
def dry_off(
    animal_id: str,
    payload: DryOffIn,
    access: AccessContext = Depends(_LACTATION),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    animal = _animal(db, animal_id)
    row = repro.end_lactation(db, access, animal, at=payload.ended_at)
    _audit(
        control_db,
        access,
        action="animal.dried_off",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="milk_production",
        summary=f"Dried off {animal.name} (lactation {row.lactation_number})",
    )
    return {"lactation": repro.lactation_dict(row), "summary": _summary(db, animal)}


# ----------------------------------------------------------------- incubation
def _batch(db: Session, batch_id: str) -> rm.IncubationBatch:
    pk = repro._parse_uuid(batch_id)
    batch = db.get(rm.IncubationBatch, pk) if pk is not None else None
    if batch is None or batch.deleted_at is not None:
        raise LivestockError("Incubation batch not found", 404)
    return batch


@router.get("/incubations")
def list_incubations(
    status_filter: str | None = None,
    _access: AccessContext = Depends(_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    stmt = select(rm.IncubationBatch).where(rm.IncubationBatch.deleted_at.is_(None))
    if status_filter:
        stmt = stmt.where(rm.IncubationBatch.status == status_filter)
    return [repro.incubation_dict(db, b) for b in db.scalars(stmt.order_by(rm.IncubationBatch.set_at.desc()))]


@router.post("/incubations", status_code=201)
def start_incubation(
    payload: IncubationIn,
    access: AccessContext = Depends(_INCUBATION),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    batch = repro.start_incubation(
        db,
        access,
        species_code=payload.species_code,
        eggs_set=payload.eggs_set,
        at=payload.set_at,
        source_group_id=payload.source_group_id,
        expected_hatch_date=payload.expected_hatch_date,
        code=payload.code,
        notes=payload.notes,
    )
    _audit(
        control_db,
        access,
        action="incubation.started",
        entity_type="incubation_batch",
        entity_id=str(batch.id),
        module_code="animals",
        summary=f"Set {batch.eggs_set} {batch.species_code.replace('_', ' ')} eggs ({batch.code})",
    )
    return repro.incubation_dict(db, batch)


@router.post("/incubations/{batch_id}/hatch")
def record_hatch(
    batch_id: str,
    payload: HatchIn,
    access: AccessContext = Depends(_INCUBATION),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """The hatch: live chicks counted into the destination flock's ledger."""
    batch = _batch(db, batch_id)
    repro.record_hatch(
        db,
        access,
        batch,
        hatched_live=payload.hatched_live,
        at=payload.occurred_at,
        unhatched=payload.unhatched,
        culled=payload.culled,
        destination_group_id=payload.destination_group_id,
        notes=payload.notes,
    )
    _audit(
        control_db,
        access,
        action="incubation.hatched",
        entity_type="incubation_batch",
        entity_id=str(batch.id),
        module_code="animals",
        summary=f"Recorded the hatch of {batch.code}: {payload.hatched_live} live",
    )
    return repro.incubation_dict(db, batch)


@router.post("/incubations/{batch_id}/cancel")
def cancel_incubation(
    batch_id: str,
    payload: IncubationCancelIn,
    access: AccessContext = Depends(_INCUBATION),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    batch = _batch(db, batch_id)
    repro.cancel_incubation(db, access, batch, reason=payload.reason)
    _audit(
        control_db,
        access,
        action="incubation.cancelled",
        entity_type="incubation_batch",
        entity_id=str(batch.id),
        module_code="animals",
        summary=f"Ended incubation {batch.code} without a hatch: {payload.reason}",
    )
    return repro.incubation_dict(db, batch)
