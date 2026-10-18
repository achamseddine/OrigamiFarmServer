"""The animal registry — the generic animal model's individual subject
(OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md §2, §12, §13).

Species, sex, stage and profile go through the capability resolver
(app/farmos/capability_service.py) before a write is accepted: it decides
which identifiers this animal may and must carry and whether it can be
pregnant or lactating. Identifiers live in app/farmos/routes_livestock.py;
this module only creates them alongside the animal.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import capability_service, identifier_service, livestock_service
from app.farmos import feeding_program_service as programs
from app.farmos.capability_service import CapabilitySet
from app.farmos.deps import AccessContext, check_farm_id, get_farmos_tenant_db, require_permission
from app.farmos.livestock_catalog import Cap
from app.farmos.livestock_models import AnimalIdentifier
from app.farmos.production_models import MilkRecord, Observation, Treatment
from app.farmos.schemas import AnimalCreate, AnimalDetailOut, AnimalMove, AnimalOut, AnimalUpdate
from app.tenant_api.models import Animal

router = APIRouter()

# Fields the audit trail follows on an animal — everything a manager might
# need to explain later ("who changed this cow's status?").
_AUDITED_ANIMAL_FIELDS = (
    "tag", "name", "species", "breed", "breed_id", "sex", "life_stage", "management_profile",
    "birth_date_estimated", "status", "location_label", "health_score", "weight_kg", "group_name",
    "pregnant", "lactating", "purchase_cost", "current_value", "active", "notes",
)  # fmt: skip


def _animal_fields(animal: Animal, identifiers: list[AnimalIdentifier]) -> dict[str, Any]:
    """Built field-by-field rather than via Pydantic's from_attributes:
    Animal.farm_id (SyncedEntityMixin's own multi-farm-within-a-tenant
    column, still unused by this contract) is a different thing from the
    wire's farm_id, which is Animal.tenant_id spelled the app's way — see
    docs/FARMOS_API.md. Auto-mapping by attribute name would silently
    serialize the wrong value.
    """
    return dict(
        id=str(animal.id),
        farm_id=str(animal.tenant_id),
        tag=animal.tag,
        name=animal.name,
        species=animal.species,
        breed=animal.breed,
        breed_id=animal.breed_id,
        sex=animal.sex,
        birth_date=animal.birth_date,
        birth_date_estimated=bool(animal.birth_date_estimated),
        life_stage=animal.life_stage,
        management_profile=animal.management_profile,
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
        identifiers=[livestock_service.to_identifier_out(i) for i in identifiers],
    )


def _to_animal_out(animal: Animal, identifiers: list[AnimalIdentifier]) -> AnimalOut:
    return AnimalOut(**_animal_fields(animal, identifiers))


def _to_animal_detail(db: Session, control_db: Session, animal: Animal) -> AnimalDetailOut:
    """The digital twin: the record, every identifier it carries, what it
    can do, and its care record. Medication administrations, the open
    health case and the live emergency are empty until the pharmacy and
    clinical modules land (docs/FARMOS_API.md, "Known, deliberate gaps")
    — the keys are already there so the tablet renders one shape."""
    identifiers = identifier_service.all_identifiers(db, animal.id)
    cap_set = livestock_service.capabilities_for_animal(animal)
    conditions = livestock_service.active_conditions(db, animal.id)
    weight = livestock_service.latest_weight(db, animal.id)
    names = livestock_service.user_names(
        control_db, [c.recorded_by for c in conditions] + ([weight.recorded_by] if weight else [])
    )
    return AnimalDetailOut(
        **_animal_fields(animal, identifiers),
        capabilities=cap_set.to_dict() if cap_set else {},
        recent_administrations=[],
        withdrawals=livestock_service.withdrawals_for(animal),
        active_conditions=[livestock_service.to_condition_out(c, names).model_dump() for c in conditions],
        latest_weight=livestock_service.to_weight_out(weight, names).model_dump() if weight else None,
        open_health_case=None,
        emergency=None,
    )


def _check_state_against(cap_set: CapabilitySet, *, name: str, pregnant: bool, lactating: bool) -> None:
    """Generic animal model §12: pregnancy needs PREGNANCY, lactation needs
    LACTATION. A male, a hen, or a meat ewe cannot be recorded as either."""
    if pregnant and not cap_set.has(Cap.PREGNANCY):
        raise HTTPException(
            status_code=422, detail=capability_service.describe_missing(cap_set, Cap.PREGNANCY, name)
        )
    if lactating and not cap_set.has(Cap.LACTATION):
        raise HTTPException(
            status_code=422, detail=capability_service.describe_missing(cap_set, Cap.LACTATION, name)
        )


def _has_operational_history(db: Session, animal: Animal) -> bool:
    """Anything recorded *about* the animal since it was registered. While
    this is true its species cannot change (§12) — the history would no
    longer mean what it meant."""
    key = str(animal.id)
    if db.scalar(select(MilkRecord.id).where(MilkRecord.animal_id == animal.id).limit(1)):
        return True
    if db.scalar(
        select(Observation.id)
        .where(Observation.entity_type == "animal", Observation.entity_id == key)
        .limit(1)
    ):
        return True
    return bool(
        db.scalar(
            select(Treatment.id).where(Treatment.entity_type == "animal", Treatment.entity_id == key).limit(1)
        )
    )


def _audit_animal(
    control_db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    action: str,
    summary: str,
    changes: dict | None = None,
) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action=action,
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=summary,
        changes=changes,
    )


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
    identifiers = identifier_service.active_identifiers_for(db, [row.id for row in rows])
    return [_to_animal_out(row, identifiers.get(row.id, [])) for row in rows]


@router.post("/animals", response_model=AnimalDetailOut, status_code=201)
def create_animal(
    payload: AnimalCreate,
    access: AccessContext = Depends(require_permission("animals", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    """Registers a new animal — the start of its digital twin.

    Only name and species are required. Identity comes as a list of typed
    identifiers — which ones are allowed, and which are required, is
    decided by the capability resolver for the species / sex / stage /
    profile, not by a universal ear-tag rule. `tag` is still accepted from
    older clients and becomes the primary identifier.
    """
    livestock_service.species_or_422(payload.species)
    cap_set = capability_service.resolve(
        payload.species, payload.sex, payload.life_stage, payload.management_profile
    )
    _check_state_against(cap_set, name=payload.name, pregnant=payload.pregnant, lactating=payload.lactating)
    identifiers = identifier_service.collect(payload.identifiers, payload.tag, cap_set)
    identifier_service.ensure_unique(db, identifiers)

    animal = Animal(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        **payload.model_dump(exclude={"identifiers", "tag"}),
    )
    db.add(animal)
    db.flush()
    identifier_service.attach(db, animal, identifiers, actor=access.membership_id)
    if animal.weight_kg:
        livestock_service.record_weight(
            db,
            animal,
            access=access,
            weight_kg=float(animal.weight_kg),
            method="scale",
            source_type="animal_update",
        )
    db.flush()
    _audit_animal(
        control_db,
        access,
        animal,
        action="animal.created",
        summary=f"Registered {animal.name}" + (f" #{animal.tag}" if animal.tag else ""),
    )
    return _to_animal_detail(db, control_db, animal)


@router.get("/animals/{animal_id}", response_model=AnimalDetailOut)
def get_animal(
    animal_id: str,
    access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    return _to_animal_detail(db, control_db, animal)


@router.patch("/animals/{animal_id}", response_model=AnimalDetailOut)
def move_animal(
    animal_id: str,
    payload: AnimalMove,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    before = animal.location_label
    animal.location_label = payload.location_label
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    if before != payload.location_label:
        _audit_animal(
            control_db,
            access,
            animal,
            action="animal.updated",
            summary=f"Moved {animal.name} to {payload.location_label}",
            changes={"location_label": {"from": before, "to": payload.location_label}},
        )
    return _to_animal_detail(db, control_db, animal)


@router.put("/animals/{animal_id}", response_model=AnimalDetailOut)
def update_animal(
    animal_id: str,
    payload: AnimalUpdate,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    """Full edit of an animal record (§12)."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("active") is False and animal.active and not access.has("animals", "delete"):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to archive an animal. Ask your farm owner or manager.",
        )

    # Species is the one thing history depends on (§12): once anything has
    # been recorded about the animal, it stays what it was registered as.
    new_species = changes.get("species", animal.species)
    if new_species != animal.species:
        livestock_service.species_or_422(new_species)
        if _has_operational_history(db, animal):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{animal.name} already has milk, treatment or observation records as a "
                    f"{animal.species.replace('_', ' ')}. Its species cannot be changed; "
                    "register a new animal instead."
                ),
            )

    # Sex, stage or profile changing re-evaluates what the animal can be —
    # a cow that becomes 'meat' cannot stay flagged as lactating. A flag
    # that is merely restated (an edit form posting the whole record)
    # is not re-judged, so an animal registered before the resolver
    # existed is not refused for the profile it never had.
    resolver_inputs = ("species", "sex", "life_stage", "management_profile")
    inputs_changed = any(changes.get(f, getattr(animal, f)) != getattr(animal, f) for f in resolver_inputs)
    turning_pregnant = changes.get("pregnant") is True and not animal.pregnant
    turning_lactating = changes.get("lactating") is True and not animal.lactating
    cap_set: CapabilitySet | None = None
    if inputs_changed or turning_pregnant or turning_lactating:
        cap_set = capability_service.resolve(
            new_species,
            changes.get("sex", animal.sex),
            changes.get("life_stage", animal.life_stage),
            changes.get("management_profile", animal.management_profile),
        )
        judge_pregnant = inputs_changed or turning_pregnant
        judge_lactating = inputs_changed or turning_lactating
        _check_state_against(
            cap_set,
            name=animal.name,
            pregnant=bool(changes.get("pregnant", animal.pregnant)) if judge_pregnant else False,
            lactating=bool(changes.get("lactating", animal.lactating)) if judge_lactating else False,
        )

    new_tag = changes.pop("tag", None)
    # A weight is a dated record; the animal's `weight_kg` is the latest of them.
    new_weight = changes.pop("weight_kg", None)

    audit_changes: dict[str, dict[str, object]] = {}
    for field, value in changes.items():
        before = getattr(animal, field)
        if before != value and field in _AUDITED_ANIMAL_FIELDS:
            audit_changes[field] = {"from": before, "to": value}
        setattr(animal, field, value)
    if new_weight is not None and (animal.weight_kg is None or float(new_weight) != float(animal.weight_kg)):
        audit_changes["weight_kg"] = {
            "from": float(animal.weight_kg) if animal.weight_kg is not None else None,
            "to": float(new_weight),
        }
        livestock_service.record_weight(
            db,
            animal,
            access=access,
            weight_kg=float(new_weight),
            method="scale",
            source_type="animal_update",
        )
    if new_tag is not None and new_tag.strip() and new_tag.strip() != (animal.tag or ""):
        audit_changes["tag"] = {"from": animal.tag, "to": new_tag.strip()}
        identifier_service.replace_primary_value(
            db,
            animal,
            new_tag.strip(),
            cap_set or livestock_service.capabilities_for_animal(animal),
            actor=access.membership_id,
        )

    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    # A change to what the feeding resolver reads asks for a feeding review
    # (feed architecture §10) — a task, never a silent reassignment.
    programs.review_animal_if_relevant(db, access, animal, set(payload.model_dump(exclude_unset=True)))
    if audit_changes:
        _audit_animal(
            control_db,
            access,
            animal,
            action="animal.updated",
            summary=f"Updated {animal.name}",
            changes=audit_changes,
        )
    return _to_animal_detail(db, control_db, animal)
