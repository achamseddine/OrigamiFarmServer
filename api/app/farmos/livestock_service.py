"""Shared helpers for the generic animal model's routers
(routes_animals.py, routes_livestock.py, routes_production.py): loading an
animal under RLS, validating a species code, the care-record views
(conditions, weights, withdrawals) and the production capability check.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.models import UserIdentity
from app.farmos import capability_service
from app.farmos import livestock_catalog as catalog
from app.farmos.capability_service import CapabilitySet
from app.farmos.deps import AccessContext
from app.farmos.livestock_models import (
    AnimalGroup,
    AnimalHealthCondition,
    AnimalIdentifier,
    AnimalWeightRecord,
)
from app.farmos.schemas import AnimalGroupOut, ConditionOut, IdentifierOut, WeightOut
from app.tenant_api.models import Animal


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def species_or_422(code: str) -> dict[str, Any]:
    try:
        return capability_service.get_species(code)
    except capability_service.UnknownSpecies:
        known = ", ".join(capability_service.known_species_codes())
        raise HTTPException(
            status_code=422,
            detail=f"'{code}' is not a species this farm keeps. Known species: {known}.",
        ) from None


def load_animal_or_404(db: Session, animal_id: str) -> Animal:
    # RLS already scopes this to the caller's own tenant — a cross-tenant
    # guess and a truly missing id look identical here, by design.
    try:
        pk = uuid.UUID(animal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Animal not found.") from exc
    animal = db.get(Animal, pk)
    if animal is None or animal.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Animal not found.")
    return animal


def load_group_or_404(db: Session, group_id: str) -> AnimalGroup:
    try:
        pk = uuid.UUID(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Animal group not found.") from exc
    group = db.get(AnimalGroup, pk)
    if group is None or group.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Animal group not found.")
    return group


def capabilities_for_animal(animal: Animal) -> CapabilitySet | None:
    """None for a row whose species predates the catalog (seeded or synced
    with a code the catalog does not know) — the API then applies no
    capability rule to it rather than refusing every write."""
    try:
        return capability_service.resolve_for_animal(animal)
    except capability_service.UnknownSpecies:
        return None


def check_production_capability(
    cap_set: CapabilitySet | None, code: str, subject_name: str, *, configured: bool
) -> None:
    """Biology always applies — a bull, a hen or a mare never has a milk
    record. The configured management profile applies once the record
    carries one (`configured`): animals registered before the resolver
    existed have no profile, and are not refused for it.
    """
    if cap_set is None:
        return
    if code in cap_set.suppressed or (configured and not cap_set.has(code)):
        raise HTTPException(
            status_code=422, detail=capability_service.describe_missing(cap_set, code, subject_name)
        )


# ------------------------------------------------------------------ views
def to_identifier_out(row: AnimalIdentifier) -> IdentifierOut:
    return IdentifierOut(
        id=str(row.id),
        animal_id=str(row.animal_id),
        identifier_type=row.identifier_type,
        identifier_value=row.identifier_value,
        issuing_authority=row.issuing_authority,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_primary=row.is_primary,
        status=row.status,
        notes=row.notes,
        created_at=row.created_at,
    )


def to_group_out(group: AnimalGroup) -> AnimalGroupOut:
    # farm_id on the wire is the tenant id — see docs/FARMOS_API.md.
    return AnimalGroupOut(
        id=str(group.id),
        farm_id=str(group.tenant_id),
        name=group.name,
        species=group.species,
        group_type=group.group_type,
        count=group.count,
        sex_composition=group.sex_composition,
        life_stage=group.life_stage,
        management_profile=group.management_profile,
        status=group.status,
        location_label=group.location_label,
    )


def user_names(control_db: Session, ids: Iterable[uuid.UUID | None]) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i is not None}
    if not wanted:
        return {}
    rows = control_db.execute(
        select(UserIdentity.id, UserIdentity.display_name).where(UserIdentity.id.in_(wanted))
    ).all()
    return {row[0]: row[1] for row in rows}


def to_condition_out(c: AnimalHealthCondition, names: dict[uuid.UUID, str]) -> ConditionOut:
    return ConditionOut(
        id=str(c.id),
        animal_id=str(c.animal_id),
        condition_type=c.condition_type,
        code=c.code,
        label=c.label,
        severity=c.severity,
        recorded_at=c.recorded_at,
        recorded_by=str(c.recorded_by) if c.recorded_by else None,
        recorded_by_name=names.get(c.recorded_by) if c.recorded_by else None,
        ended_at=c.ended_at,
        active=c.active,
        notes=c.notes,
    )


def to_weight_out(w: AnimalWeightRecord, names: dict[uuid.UUID, str]) -> WeightOut:
    return WeightOut(
        id=str(w.id),
        animal_id=str(w.animal_id),
        weight_kg=float(w.weight_kg),
        measured_at=w.measured_at,
        method=w.method,
        recorded_by=str(w.recorded_by) if w.recorded_by else None,
        recorded_by_name=names.get(w.recorded_by) if w.recorded_by else None,
        source_type=w.source_type,
        source_id=w.source_id,
        notes=w.notes,
    )


# ------------------------------------------------------------ care record
def active_conditions(db: Session, animal_id: uuid.UUID) -> list[AnimalHealthCondition]:
    return list(
        db.scalars(
            select(AnimalHealthCondition)
            .where(
                AnimalHealthCondition.animal_id == animal_id,
                AnimalHealthCondition.active.is_(True),
                AnimalHealthCondition.deleted_at.is_(None),
            )
            .order_by(AnimalHealthCondition.recorded_at.desc())
        )
    )


def latest_weight(db: Session, animal_id: uuid.UUID) -> AnimalWeightRecord | None:
    return db.scalar(
        select(AnimalWeightRecord)
        .where(AnimalWeightRecord.animal_id == animal_id, AnimalWeightRecord.deleted_at.is_(None))
        .order_by(AnimalWeightRecord.measured_at.desc())
        .limit(1)
    )


def weight_history(db: Session, animal_id: uuid.UUID, *, limit: int = 50) -> list[AnimalWeightRecord]:
    return list(
        db.scalars(
            select(AnimalWeightRecord)
            .where(AnimalWeightRecord.animal_id == animal_id, AnimalWeightRecord.deleted_at.is_(None))
            .order_by(AnimalWeightRecord.measured_at.desc())
            .limit(limit)
        )
    )


def record_weight(
    db: Session,
    animal: Animal,
    *,
    access: AccessContext,
    weight_kg: float,
    measured_at: datetime | None = None,
    method: str = "scale",
    source_type: str = "manual",
    source_id: str | None = None,
    notes: str | None = None,
) -> AnimalWeightRecord:
    """A dated weight with its method; the animal's `weight_kg` becomes the
    latest of them (never an older back-dated one)."""
    if weight_kg is None or weight_kg <= 0:
        raise HTTPException(status_code=422, detail="Weight must be greater than zero.")
    if method not in catalog.WEIGHT_METHODS:
        raise HTTPException(status_code=422, detail=f"method must be one of {list(catalog.WEIGHT_METHODS)}")
    at = ensure_utc(measured_at) or now_utc()
    if at > now_utc() + timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="A weight cannot be measured in the future.")
    row = AnimalWeightRecord(
        tenant_id=animal.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=animal.id,
        weight_kg=weight_kg,
        measured_at=at,
        method=method,
        recorded_by=access.user_id,
        source_type=source_type,
        source_id=source_id,
        notes=notes,
    )
    db.add(row)
    db.flush()
    latest = latest_weight(db, animal.id)
    if latest is None or latest.id == row.id or (ensure_utc(latest.measured_at) or at) <= at:
        animal.weight_kg = weight_kg
    return row


def withdrawals_for(animal: Animal) -> dict[str, Any]:
    """The withdrawal windows the animal is under. Milk/meat windows come
    from medication administrations (pharmacy, a later stage); until then
    only the treatment-set `withdrawal_until` on the record applies."""
    at = now_utc()
    record_until = ensure_utc(animal.withdrawal_until)
    if record_until is not None and record_until <= at:
        record_until = None
    return {
        "active": record_until is not None,
        "milk_until": None,
        "meat_until": None,
        "record_until": record_until.isoformat() if record_until else None,
        "reason": animal.withdrawal_reason if record_until else None,
    }
