from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.schemas import AnimalCreate, AnimalDetailOut, AnimalMove, AnimalOut, AnimalUpdate
from app.tenant_api.models import Animal

router = APIRouter()


def _to_animal_out(animal: Animal, detail: bool = False) -> AnimalOut:
    """Built field-by-field rather than via Pydantic's from_attributes:
    Animal.farm_id (SyncedEntityMixin's own multi-farm-within-a-tenant
    column, still unused by this contract) is a different thing from the
    wire's farm_id, which is Animal.tenant_id spelled the app's way — see
    docs/FARMOS_API.md. Auto-mapping by attribute name would silently
    serialize the wrong value.
    """
    fields = dict(
        id=str(animal.id),
        farm_id=str(animal.tenant_id),
        tag=animal.tag,
        name=animal.name,
        species=animal.species,
        breed=animal.breed,
        sex=animal.sex,
        birth_date=animal.birth_date,
        status=animal.status,
        location_label=animal.location_label,
        health_score=animal.health_score,
        pregnant=animal.pregnant,
        pregnancy_days=animal.pregnancy_days,
        lactating=animal.lactating,
        lactation_cycle=animal.lactation_cycle,
        withdrawal_until=animal.withdrawal_until,
        withdrawal_reason=animal.withdrawal_reason,
        weight_kg=float(animal.weight_kg) if animal.weight_kg is not None else None,
        group_name=animal.group_name,
        photo_path=animal.photo_path,
        acquisition_date=animal.acquisition_date,
        acquisition_source=animal.acquisition_source,
        sire_tag=animal.sire_tag,
        dam_tag=animal.dam_tag,
        color_markings=animal.color_markings,
        purchase_cost=float(animal.purchase_cost) if animal.purchase_cost is not None else None,
        current_value=float(animal.current_value) if animal.current_value is not None else None,
        notes=animal.notes,
        active=animal.active,
    )
    return AnimalDetailOut(**fields) if detail else AnimalOut(**fields)


def _load_animal_or_404(db: Session, animal_id: str) -> Animal:
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


@router.get("/animals", response_model=list[AnimalOut])
def list_animals(
    farm_id: str = Query(...),
    species: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[AnimalOut]:
    check_farm_id(farm_id, access)
    stmt = select(Animal).where(Animal.deleted_at.is_(None))
    if species:
        stmt = stmt.where(Animal.species == species)
    if status:
        stmt = stmt.where(Animal.status == status)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Animal.name.ilike(like)) | (Animal.tag.ilike(like)))
    rows = db.execute(stmt.order_by(Animal.tag)).scalars().all()
    return [_to_animal_out(row) for row in rows]


@router.post("/animals", response_model=AnimalDetailOut, status_code=201)
def create_animal(
    payload: AnimalCreate,
    access: AccessContext = Depends(require_permission("animals", "create")),
    db: Session = Depends(get_farmos_tenant_db),
) -> AnimalDetailOut:
    """Registers a new animal — the start of its digital twin."""
    animal = Animal(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        **payload.model_dump(),
    )
    db.add(animal)
    db.flush()
    return _to_animal_out(animal, detail=True)  # type: ignore[return-value]


@router.get("/animals/{animal_id}", response_model=AnimalDetailOut)
def get_animal(
    animal_id: str,
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> AnimalDetailOut:
    animal = _load_animal_or_404(db, animal_id)
    return _to_animal_out(animal, detail=True)  # type: ignore[return-value]


@router.patch("/animals/{animal_id}", response_model=AnimalDetailOut)
def move_animal(
    animal_id: str,
    payload: AnimalMove,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    animal = _load_animal_or_404(db, animal_id)
    before = animal.location_label
    animal.location_label = payload.location_label
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    if before != payload.location_label:
        record_audit_event(
            control_db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="animal.updated",
            entity_type="animal",
            entity_id=str(animal.id),
            module_code="animals",
            summary=f"Moved {animal.name} to {payload.location_label}",
            changes={"location_label": {"from": before, "to": payload.location_label}},
        )
    return _to_animal_out(animal, detail=True)  # type: ignore[return-value]


@router.put("/animals/{animal_id}", response_model=AnimalDetailOut)
def update_animal(
    animal_id: str,
    payload: AnimalUpdate,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    """Full edit of an animal record."""
    animal = _load_animal_or_404(db, animal_id)
    changes: dict[str, dict[str, object]] = {}
    for field, value in payload.model_dump(exclude_unset=True).items():
        before = getattr(animal, field)
        if before != value:
            changes[field] = {"from": before, "to": value}
        setattr(animal, field, value)
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    if changes:
        record_audit_event(
            control_db,
            actor_id=access.user_id,
            actor_type=ActorType.TENANT_USER,
            actor_role=access.role,
            tenant_id=access.tenant_id,
            action="animal.updated",
            entity_type="animal",
            entity_id=str(animal.id),
            module_code="animals",
            summary=f"Updated {animal.name}",
            changes=changes,
        )
    return _to_animal_out(animal, detail=True)  # type: ignore[return-value]
