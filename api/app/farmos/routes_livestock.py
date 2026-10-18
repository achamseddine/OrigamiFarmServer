"""Species configuration, the capability resolver, identifiers, animal
groups and the care record (OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md §11).

These are the endpoints that make "adding a species is configuration"
true for the tablet: it asks `/species` what exists, `/species/{code}/
configuration` what each needs, and `/animal-capabilities/resolve` what a
particular animal can do — then draws exactly that.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import capability_service, identifier_service, livestock_service
from app.farmos import feed_performance_service as perf
from app.farmos import feeding_program_service as programs
from app.farmos import livestock_catalog as catalog
from app.farmos.deps import (
    AccessContext,
    get_access_context,
    get_farmos_tenant_db,
    require_diagnostic_role,
    require_permission,
)
from app.farmos.livestock_models import AnimalGroup, AnimalHealthCondition
from app.farmos.schemas import (
    AnimalGroupCreate,
    AnimalGroupOut,
    AnimalGroupUpdate,
    BreedOut,
    CapabilityOut,
    CapabilitySetOut,
    ConditionIn,
    ConditionOut,
    IdentifierIn,
    IdentifierOut,
    LifeStageOut,
    ManagementProfileOut,
    ResolveRequest,
    RuleOut,
    SpeciesConfigurationOut,
    SpeciesOut,
    WeightIn,
    WeightOut,
)

router = APIRouter()


def _audit(control_db: Session, access: AccessContext, **kwargs: object) -> None:
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        **kwargs,  # type: ignore[arg-type]
    )


# ------------------------------------------------------------------ species
@router.get("/species", response_model=list[SpeciesOut])
def list_species(_access: AccessContext = Depends(get_access_context)) -> list[SpeciesOut]:
    return [
        SpeciesOut(**s)
        for s in sorted((s for s in catalog.SPECIES if s["active"]), key=lambda s: s["sort_order"])
    ]


@router.get("/species/{code}/configuration", response_model=SpeciesConfigurationOut)
def species_configuration(
    code: str, _access: AccessContext = Depends(get_access_context)
) -> SpeciesConfigurationOut:
    """Everything the Add Animal flow needs for one species, in one call."""
    species = livestock_service.species_or_422(code)
    offered = set(species["profiles_json"] or ())
    return SpeciesConfigurationOut(
        species=SpeciesOut(**species),
        breeds=[
            BreedOut(**b)
            for b in sorted((b for b in catalog.BREEDS if b["species_code"] == code), key=lambda b: b["name"])
        ],
        life_stages=[
            LifeStageOut(code=c, species_code=sc, label_en=en, label_ar=ar, sort_order=order)
            for c, sc, en, ar, order in sorted(catalog.LIFE_STAGES, key=lambda s: s[4])
            if sc is None or sc == code
        ],
        management_profiles=[
            ManagementProfileOut(code=c, species_code=sc, label_en=en, label_ar=ar)
            for c, sc, en, ar in sorted(catalog.MANAGEMENT_PROFILES, key=lambda p: p[0])
            # Only the profiles that make sense for this species (empty = all).
            if (sc is None or sc == code) and (not offered or c in offered)
        ],
        rules=[RuleOut(**r) for r in catalog.RULES_BY_SPECIES.get(code, [])],
    )


@router.get("/capabilities", response_model=list[CapabilityOut])
def list_capabilities(_access: AccessContext = Depends(get_access_context)) -> list[CapabilityOut]:
    return [
        CapabilityOut(code=code, category=category, label_en=en, label_ar=ar, active=True)
        for code, category, en, ar in catalog.CAPABILITIES
    ]


# ----------------------------------------------------------------- resolver
@router.post("/animal-capabilities/resolve", response_model=CapabilitySetOut)
def resolve_capabilities(
    payload: ResolveRequest, _access: AccessContext = Depends(get_access_context)
) -> CapabilitySetOut:
    """What an animal of this species / sex / stage / profile can do — the
    same answer the write endpoints enforce, so a form built from it never
    offers something the server will refuse."""
    livestock_service.species_or_422(payload.species)
    cap_set = capability_service.resolve(
        payload.species, payload.sex, payload.life_stage, payload.management_profile
    )
    return CapabilitySetOut(**cap_set.to_dict())


@router.get("/animals/{animal_id}/capabilities", response_model=CapabilitySetOut)
def animal_capabilities(
    animal_id: str,
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> CapabilitySetOut:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    livestock_service.species_or_422(animal.species)
    return CapabilitySetOut(**capability_service.resolve_for_animal(animal).to_dict())


# -------------------------------------------------------------- identifiers
@router.get("/animals/{animal_id}/identifiers", response_model=list[IdentifierOut])
def list_identifiers(
    animal_id: str,
    access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[IdentifierOut]:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    return [livestock_service.to_identifier_out(i) for i in identifier_service.all_identifiers(db, animal.id)]


@router.post("/animals/{animal_id}/identifiers", response_model=IdentifierOut, status_code=201)
def add_identifier(
    animal_id: str,
    payload: IdentifierIn,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> IdentifierOut:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    livestock_service.species_or_422(animal.species)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    cap_set = capability_service.resolve_for_animal(animal)
    identifiers = identifier_service.collect([payload], None, cap_set, check_required=False)
    # `collect` would make a lone identifier primary; adding a second RFID
    # to a cow with an ear tag must not steal the primary unless asked.
    identifiers = [identifiers[0].model_copy(update={"is_primary": payload.is_primary})]
    identifier_service.ensure_unique(db, identifiers, exclude_animal_id=animal.id)
    if payload.is_primary:
        for existing in identifier_service.active_identifiers(db, animal.id):
            existing.is_primary = False
    created = identifier_service.attach(db, animal, identifiers, actor=access.membership_id)[0]
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    _audit(
        control_db,
        access,
        action="animal.identifier_added",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=(
            f"Added {payload.identifier_type.replace('_', ' ').lower()} "
            f"{payload.identifier_value} to {animal.name}"
        ),
    )
    return livestock_service.to_identifier_out(created)


@router.delete("/animals/{animal_id}/identifiers/{identifier_id}", status_code=204, response_model=None)
def retire_identifier(
    animal_id: str,
    identifier_id: str,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> None:
    """Retires, never deletes: a record that names the old tag still
    resolves. If it was the primary, the next active identifier takes over
    — an animal is not left with no way to be called."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    ident = next(
        (i for i in identifier_service.all_identifiers(db, animal.id) if str(i.id) == identifier_id), None
    )
    if ident is None:
        raise HTTPException(status_code=404, detail="Identifier not found.")
    remaining = [i for i in identifier_service.active_identifiers(db, animal.id) if i.id != ident.id]
    cap_set = livestock_service.capabilities_for_animal(animal)
    if cap_set is not None:
        still_present = {i.identifier_type for i in remaining}
        missing = cap_set.required_identifier_types() - still_present
        if missing:
            needed = ", ".join(m.replace("_", " ").lower() for m in sorted(missing))
            raise HTTPException(
                status_code=422, detail=f"{animal.name} must keep a {needed}. Add a replacement first."
            )
    ident.status = "retired"
    ident.is_primary = False
    identifier_service.sync_tag(db, animal)
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    _audit(
        control_db,
        access,
        action="animal.identifier_retired",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=(
            f"Retired {ident.identifier_type.replace('_', ' ').lower()} "
            f"{ident.identifier_value} on {animal.name}"
        ),
    )


# ------------------------------------------------------------ animal groups
@router.get("/animal-groups", response_model=list[AnimalGroupOut])
def list_groups(
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[AnimalGroupOut]:
    rows = db.scalars(
        select(AnimalGroup).where(AnimalGroup.deleted_at.is_(None)).order_by(AnimalGroup.name)
    ).all()
    return [livestock_service.to_group_out(g) for g in rows]


@router.post("/animal-groups", response_model=AnimalGroupOut, status_code=201)
def create_group(
    payload: AnimalGroupCreate,
    access: AccessContext = Depends(require_permission("animals", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalGroupOut:
    livestock_service.species_or_422(payload.species)
    group = AnimalGroup(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, **payload.model_dump()
    )
    cap_set = capability_service.resolve_for_group(group)
    if not cap_set.has(catalog.Cap.GROUP_TRACKING):
        word = cap_set.terminology.get("group_en", "Group")
        raise HTTPException(
            status_code=422,
            detail=(
                f"{word} management is not configured for this species; register the animals individually."
            ),
        )
    db.add(group)
    db.flush()
    _audit(
        control_db,
        access,
        action="animal_group.created",
        entity_type="animal_group",
        entity_id=str(group.id),
        module_code="animals",
        summary=f"Created {group.name} ({group.count} {group.species.replace('_', ' ')})",
    )
    return livestock_service.to_group_out(group)


@router.get("/animal-groups/{group_id}", response_model=AnimalGroupOut)
def get_group(
    group_id: str,
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> AnimalGroupOut:
    return livestock_service.to_group_out(livestock_service.load_group_or_404(db, group_id))


@router.patch("/animal-groups/{group_id}", response_model=AnimalGroupOut)
def update_group(
    group_id: str,
    payload: AnimalGroupUpdate,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalGroupOut:
    group = livestock_service.load_group_or_404(db, group_id)
    changes = payload.model_dump(exclude_unset=True)
    audit_changes: dict[str, dict[str, object]] = {}
    for field, value in changes.items():
        before = getattr(group, field)
        if before != value:
            audit_changes[field] = {"from": before, "to": value}
        setattr(group, field, value)
    group.version += 1
    group.last_modified_by = access.membership_id
    db.flush()
    # A group that grew, changed stage or changed profile may fit a different
    # feeding program — a review, never a silent reassignment (feed §10).
    if {"count", "life_stage", "management_profile", "sex_composition"} & changes.keys():
        programs.review_feeding(db, access, "group", group, trigger="group_changed")
    if audit_changes:
        _audit(
            control_db,
            access,
            action="animal_group.updated",
            entity_type="animal_group",
            entity_id=str(group.id),
            module_code="animals",
            summary=f"Updated {group.name}",
            changes=audit_changes,
        )
    return livestock_service.to_group_out(group)


@router.get("/animal-groups/{group_id}/capabilities", response_model=CapabilitySetOut)
def group_capabilities(
    group_id: str,
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> CapabilitySetOut:
    group = livestock_service.load_group_or_404(db, group_id)
    livestock_service.species_or_422(group.species)
    return CapabilitySetOut(**capability_service.resolve_for_group(group).to_dict())


# --------------------------------------------------------------- conditions
@router.get("/animals/{animal_id}/conditions", response_model=list[ConditionOut])
def list_conditions(
    animal_id: str,
    include_ended: bool = Query(default=False),
    _access: AccessContext = Depends(require_permission("animal_health", "view")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> list[ConditionOut]:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    stmt = select(AnimalHealthCondition).where(
        AnimalHealthCondition.animal_id == animal.id, AnimalHealthCondition.deleted_at.is_(None)
    )
    if not include_ended:
        stmt = stmt.where(AnimalHealthCondition.active.is_(True))
    rows = db.scalars(stmt.order_by(AnimalHealthCondition.recorded_at.desc())).all()
    names = livestock_service.user_names(control_db, (c.recorded_by for c in rows))
    return [livestock_service.to_condition_out(c, names) for c in rows]


@router.post("/animals/{animal_id}/conditions", response_model=ConditionOut, status_code=201)
def record_condition(
    animal_id: str,
    payload: ConditionIn,
    access: AccessContext = Depends(require_permission("animal_health", "create")),
    _diagnostic: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> ConditionOut:
    """An allergy, adverse reaction, chronic condition or contraindication
    the veterinarian puts on the animal's record. The emergency engine's
    eligibility rules read it as an authoritative fact."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    if payload.condition_type not in catalog.CONDITION_TYPES:
        raise HTTPException(
            status_code=422, detail=f"condition_type must be one of {list(catalog.CONDITION_TYPES)}"
        )
    row = AnimalHealthCondition(
        tenant_id=access.tenant_id,
        last_modified_by=access.membership_id,
        animal_id=animal.id,
        condition_type=payload.condition_type,
        code=payload.code.strip().lower(),
        label=payload.label.strip(),
        severity=payload.severity,
        recorded_at=livestock_service.now_utc(),
        recorded_by=access.user_id,
        active=True,
        notes=payload.notes,
    )
    db.add(row)
    db.flush()
    _audit(
        control_db,
        access,
        action="animal.condition_recorded",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animal_health",
        summary=f"Recorded {payload.condition_type.replace('_', ' ')} '{row.label}' on {animal.name}",
    )
    return livestock_service.to_condition_out(row, {access.user_id: access.display_name})


@router.delete("/animals/{animal_id}/conditions/{condition_id}", response_model=ConditionOut)
def end_condition(
    animal_id: str,
    condition_id: str,
    access: AccessContext = Depends(require_permission("animal_health", "create")),
    _diagnostic: AccessContext = Depends(require_diagnostic_role),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> ConditionOut:
    """Ended, never deleted — the history of what the animal reacted to
    stays on its record."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    condition = next(
        (
            c
            for c in db.scalars(
                select(AnimalHealthCondition).where(
                    AnimalHealthCondition.animal_id == animal.id, AnimalHealthCondition.deleted_at.is_(None)
                )
            )
            if str(c.id) == condition_id
        ),
        None,
    )
    if condition is None:
        raise HTTPException(status_code=404, detail="Condition not found.")
    if condition.active:
        condition.active = False
        condition.ended_at = livestock_service.now_utc()
        condition.version += 1
        condition.last_modified_by = access.membership_id
        db.flush()
        _audit(
            control_db,
            access,
            action="animal.condition_ended",
            entity_type="animal",
            entity_id=str(animal.id),
            module_code="animal_health",
            summary=(
                f"Ended {condition.condition_type.replace('_', ' ')} '{condition.label}' on {animal.name}"
            ),
        )
    names = livestock_service.user_names(control_db, [condition.recorded_by])
    return livestock_service.to_condition_out(condition, names)


# ------------------------------------------------------------------ weights
def require_weight_writer(access: AccessContext = Depends(get_access_context)) -> AccessContext:
    if access.has("animals", "edit") or access.has("animal_health", "create"):
        return access
    raise HTTPException(
        status_code=403, detail="Recording a weight needs Animals edit or Animal Health create."
    )


@router.get("/animals/{animal_id}/weights", response_model=list[WeightOut])
def list_weights(
    animal_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    _access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> list[WeightOut]:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    rows = livestock_service.weight_history(db, animal.id, limit=limit)
    names = livestock_service.user_names(control_db, (w.recorded_by for w in rows))
    return [livestock_service.to_weight_out(w, names) for w in rows]


@router.post("/animals/{animal_id}/weights", response_model=WeightOut, status_code=201)
def record_weight(
    animal_id: str,
    payload: WeightIn,
    access: AccessContext = Depends(require_weight_writer),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> WeightOut:
    """A dated weight with its method; the animal's `weight_kg` becomes
    the latest of them."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    row = livestock_service.record_weight(
        db,
        animal,
        access=access,
        weight_kg=payload.weight_kg,
        measured_at=payload.measured_at,
        method=payload.method,
        source_type="manual",
        notes=payload.notes,
    )
    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    perf.on_context_changed(db, access, "animal", animal, trigger="weight_recorded")
    _audit(
        control_db,
        access,
        action="animal.weight_recorded",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Recorded {payload.weight_kg:g} kg ({payload.method}) for {animal.name}",
    )
    return livestock_service.to_weight_out(row, {access.user_id: access.display_name})
