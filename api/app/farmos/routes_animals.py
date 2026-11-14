"""The animal registry — the generic animal model's individual subject
(OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md §2, §12, §13;
database/LIVESTOCK-SCHEMA.md §5, §14, §16).

Species, sex, stage and profile go through the capability resolver
(app/farmos/capability_service.py) before a write is accepted: it decides
which identifiers this animal may and must carry and whether it can be
pregnant or lactating. Registration itself is app/farmos/animal_service.py
(shared with births); identifiers live in routes_livestock.py. A stage,
profile or location change is written to the animal's state history
first; pregnancy and lactation are records (routes_reproduction.py), not
fields; archiving is an exit.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.exceptions import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.db import get_control_db
from app.common.enums import ActorType
from app.farmos import (
    animal_service,
    capability_service,
    duplicates,
    group_service,
    identifier_service,
    livestock_service,
    livestock_state_service,
    reproduction_service,
)
from app.farmos import emergency_models as em
from app.farmos import feed_performance_service as perf
from app.farmos import feeding_program_service as programs
from app.farmos import health_case_service as cases
from app.farmos import pharmacy_service as ph
from app.farmos import reproduction_models as rm
from app.farmos.capability_service import CapabilitySet
from app.farmos.deps import (
    AccessContext,
    check_farm_id,
    get_farmos_tenant_db,
    require_any_permission,
    require_permission,
)
from app.farmos.livestock_models import AnimalIdentifier
from app.farmos.pharmacy_models import MedicationAdministration
from app.farmos.production_models import MilkRecord, Observation, Treatment
from app.farmos.routing_service import NameCache
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

_FINANCIAL_FIELDS = animal_service.FINANCIAL_FIELDS
# Pregnancy and lactation are records, not fields (LIVESTOCK-SCHEMA §1.8,
# REPRODUCTION-SCHEMA): the animal's flags are their projection.
_REPRODUCTION_FIELDS = ("pregnant", "lactating")
_DERIVED_FIELDS = ("pregnancy_days",)
# Current-state fields whose every change is written to the animal's state
# history first (LIVESTOCK-SCHEMA §14).
_HISTORY_FIELDS = {
    "life_stage": livestock_state_service.LIFE_STAGE,
    "management_profile": livestock_state_service.MANAGEMENT_PROFILE,
    "location_label": livestock_state_service.LOCATION,
}

# One animal's record is reached from anywhere an animal is named: a herd
# card, a milk entry, a priority or a notification.
_ANIMAL_RECORD_VIEW = require_any_permission(
    ("animals", "view"),
    ("animal_health", "view"),
    ("milk_production", "view"),
    ("feed_nutrition", "view"),
    ("egg_production", "view"),
    ("ai_intelligence", "view"),
)


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
        lifecycle_status=animal.lifecycle_status or "active",
        exited_at=animal.exited_at,
        pregnancy_started_at=animal.pregnancy_started_at,
        expected_due_date=animal.expected_due_date,
        lactation_started_at=animal.lactation_started_at,
        dried_off_at=animal.dried_off_at,
        identifiers=[livestock_service.to_identifier_out(i) for i in identifiers],
    )


def _to_animal_out(animal: Animal, identifiers: list[AnimalIdentifier]) -> AnimalOut:
    return AnimalOut(**_animal_fields(animal, identifiers))


def _to_animal_detail(db: Session, control_db: Session, animal: Animal) -> AnimalDetailOut:
    """The digital twin: the record, every identifier it carries, what it
    can do, its care record, the last medication administrations with the
    withdrawal windows they applied, the open health case and the live
    emergency (an open, running or escalated assessment), and the livestock
    record — reproduction card, current groups, parentage, recent history."""
    identifiers = identifier_service.all_identifiers(db, animal.id)
    cap_set = livestock_service.capabilities_for_animal(animal)
    conditions = livestock_service.active_conditions(db, animal.id)
    weight = livestock_service.latest_weight(db, animal.id)
    names = livestock_service.user_names(
        control_db, [c.recorded_by for c in conditions] + ([weight.recorded_by] if weight else [])
    )
    administrations = db.scalars(
        select(MedicationAdministration)
        .where(
            MedicationAdministration.subject_type == "animal",
            MedicationAdministration.subject_id == animal.id,
            MedicationAdministration.deleted_at.is_(None),
        )
        .order_by(MedicationAdministration.administered_at.desc())
        .limit(10)
    ).all()
    case = cases.current_case(db, "animal", animal.id)
    live = db.scalar(
        select(em.EmergencyAssessment)
        .where(
            em.EmergencyAssessment.animal_id == animal.id,
            em.EmergencyAssessment.status.in_(("open", "protocol_started", "escalated")),
        )
        .order_by(em.EmergencyAssessment.started_at.desc())
        .limit(1)
    )
    live_run = (
        db.scalar(
            select(em.EmergencyProtocolRun)
            .where(em.EmergencyProtocolRun.emergency_assessment_id == live.id)
            .order_by(em.EmergencyProtocolRun.started_at.desc())
            .limit(1)
        )
        if live
        else None
    )
    name_cache = NameCache()
    return AnimalDetailOut(
        **_animal_fields(animal, identifiers),
        capabilities=cap_set.to_dict() if cap_set else {},
        recent_administrations=[ph.administration_dict(db, a, name_cache) for a in administrations],
        withdrawals=livestock_service.withdrawals_for(
            animal, [a for a in administrations if a.status == "recorded"]
        ),
        active_conditions=[livestock_service.to_condition_out(c, names).model_dump() for c in conditions],
        latest_weight=livestock_service.to_weight_out(weight, names).model_dump() if weight else None,
        open_health_case=cases.case_dict(db, case, name_cache, with_details=False) if case else None,
        emergency=(
            {
                "id": str(live.id),
                "status": live.status,
                "triage_level": live.triage_level,
                "started_at": live.started_at.isoformat(),
                "explanation": live.explanation,
                "escalation_reasons": live.escalation_reasons or [],
                "run_id": str(live_run.id) if live_run else None,
                "run_status": live_run.status if live_run else None,
            }
            if live
            else None
        ),
        reproduction=reproduction_service.summary(db, animal) if cap_set else None,
        groups=[
            group_service.membership_dict(db, m, animal=animal)
            for m in group_service.memberships_of_animal(db, animal.id, current_only=True)
        ],
        relationships=reproduction_service.relationships(db, animal),
        recent_history=[
            livestock_state_service.history_dict(h)
            for h in livestock_state_service.history(db, animal.id)[:20]
        ],
    )


def _has_operational_history(db: Session, animal: Animal) -> bool:
    """Anything recorded *about* the animal since it was registered. While
    this is true its species cannot change (§12) — the history would no
    longer mean what it meant."""
    key = str(animal.id)
    checks = (
        select(MilkRecord.id).where(MilkRecord.animal_id == animal.id),
        select(Observation.id).where(Observation.entity_type == "animal", Observation.entity_id == key),
        select(Treatment.id).where(Treatment.entity_type == "animal", Treatment.entity_id == key),
        select(rm.BreedingEvent.id).where(rm.BreedingEvent.female_animal_id == animal.id),
        select(rm.BirthEvent.id).where(rm.BirthEvent.dam_animal_id == animal.id),
    )
    return any(db.scalar(q.limit(1)) for q in checks)


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


def _identifier_match(like: str):  # type: ignore[no-untyped-def]
    return Animal.id.in_(
        select(AnimalIdentifier.animal_id).where(func.lower(AnimalIdentifier.identifier_value).like(like))
    )


@router.get("/animals", response_model=list[AnimalOut])
def list_animals(
    farm_id: str = Query(...),
    species: str | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    include_exited: bool = Query(
        default=False, description="Also list animals that were sold, died, or otherwise left the farm."
    ),
    access: AccessContext = Depends(require_permission("animals", "view")),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[AnimalOut]:
    check_farm_id(farm_id, access)
    stmt = select(Animal).where(Animal.deleted_at.is_(None))
    if not include_exited:
        stmt = stmt.where(Animal.active.is_(True))
    if species:
        stmt = stmt.where(Animal.species == species)
    if status:
        stmt = stmt.where(Animal.status == status)
    if search:
        # Name, the primary tag, and every identifier the animal carries or
        # carried (LIVESTOCK-SCHEMA §16: search by allowed identifiers).
        like = f"%{search.strip().lower()}%"
        stmt = stmt.where(Animal.name.ilike(like) | Animal.tag.ilike(like) | _identifier_match(like))
    rows = db.execute(stmt.order_by(Animal.tag)).scalars().all()
    identifiers = identifier_service.active_identifiers_for(db, [row.id for row in rows])
    return [_to_animal_out(row, identifiers.get(row.id, [])) for row in rows]


@router.get("/animals/lookup")
def lookup_animal(
    value: str = Query(
        ...,
        min_length=1,
        description="An identifier value as written on the animal: ear tag, RFID, microchip, band, name…",
    ),
    _access: AccessContext = Depends(_ANIMAL_RECORD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    """Every animal on this farm an identifier value could mean, exact
    matches only (LIVESTOCK-SCHEMA §16). More than one result is an
    ambiguity for the person to resolve — the server never silently picks
    one. A retired tag still resolves, flagged, so an old paper record can
    be matched to its animal."""
    needle = value.strip().lower()
    rows = db.execute(
        select(AnimalIdentifier, Animal)
        .join(Animal, Animal.id == AnimalIdentifier.animal_id)
        .where(
            func.lower(AnimalIdentifier.identifier_value) == needle,
            AnimalIdentifier.deleted_at.is_(None),
            Animal.deleted_at.is_(None),
        )
    ).all()
    out = [
        {
            "animal_id": str(a.id),
            "name": a.name,
            "tag": a.tag,
            "species": a.species,
            "active": a.active,
            "lifecycle_status": a.lifecycle_status,
            "matched_type": i.identifier_type,
            "matched_value": i.identifier_value,
            "identifier_status": i.status,
            "retired": i.status != "active",
        }
        for i, a in rows
    ]
    seen = {r["animal_id"] for r in out}
    for a in db.scalars(select(Animal).where(func.lower(Animal.name) == needle, Animal.deleted_at.is_(None))):
        if str(a.id) not in seen:
            out.append(
                {
                    "animal_id": str(a.id),
                    "name": a.name,
                    "tag": a.tag,
                    "species": a.species,
                    "active": a.active,
                    "lifecycle_status": a.lifecycle_status,
                    "matched_type": "NAME",
                    "matched_value": a.name,
                    "identifier_status": "active",
                    "retired": False,
                }
            )
    # Current animals and active identifiers first: the likely answer on top.
    return sorted(out, key=lambda r: (not r["active"], r["retired"], r["name"]))


@router.post("/animals", response_model=AnimalDetailOut, status_code=201)
def create_animal(
    payload: AnimalCreate,
    access: AccessContext = Depends(require_permission("animals", "create")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    """Registers a new animal — the start of its digital twin (tech spec §13).

    Generic animal model §2: species, sex, stage and profile go through
    the capability resolver first; it decides which identifiers this
    animal may and must carry and whether it can be pregnant or lactating.
    Every animal gets the farm's next number; a required ear tag or
    microchip may be added later. An animal registered pregnant or in milk
    gets the pregnancy or lactation episode that says so; `group_name` must
    name an existing group of its species, which it then joins. Another
    animal of the species with the same name is a possible duplicate
    (409) unless `confirm_duplicate` says otherwise.
    """
    same_name = db.scalar(
        select(Animal).where(
            Animal.species == payload.species,
            Animal.active.is_(True),
            Animal.deleted_at.is_(None),
            func.lower(Animal.name) == payload.name.strip().lower(),
        )
    )
    match = duplicates.guard(
        same_name,
        f"{same_name.name} (#{same_name.tag or str(same_name.id)[:8]}) is already registered on this farm."
        if same_name
        else "",
        confirm=payload.confirm_duplicate,
    )
    animal = animal_service.create_animal(
        db,
        access,
        payload.model_dump(exclude={"identifiers", "tag", "confirm_duplicate"}),
        identifiers_in=payload.identifiers,
        tag=payload.tag,
    )
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="animal",
        entity_id=str(animal.id),
        match_id=match.id if match else None,
        what=f"animal:{animal.id}",
        module_code="animals",
    )
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
    """Moves the animal to another place — written to its location history
    (LIVESTOCK-SCHEMA §5 LOCATION) before the current value changes."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    livestock_state_service.ensure_on_farm(animal, "a move")
    before = animal.location_label
    livestock_state_service.record(
        db,
        animal,
        livestock_state_service.LOCATION,
        payload.location_label,
        reason="moved",
        source_type="animal_move",
        user_id=access.user_id,
    )
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


def _snapshot(animal: Animal) -> dict[str, Any]:
    out = {}
    for field in _AUDITED_ANIMAL_FIELDS:
        value = getattr(animal, field)
        out[field] = (
            float(value)
            if field in ("weight_kg", "purchase_cost", "current_value") and value is not None
            else value
        )
    return out


@router.put("/animals/{animal_id}", response_model=AnimalDetailOut)
def update_animal(
    animal_id: str,
    payload: AnimalUpdate,
    access: AccessContext = Depends(require_permission("animals", "edit")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> AnimalDetailOut:
    """Full edit of an animal record (tech spec §12).

    What an edit cannot do, by design:
    - change `pregnant` or `lactating` — a pregnancy check, a birth, a loss,
      a lactation start or a dry-off records that (`/animals/{id}/...`); a
      re-sent unchanged value is fine, and `pregnancy_days` is derived;
    - change species once anything has been recorded about the animal.

    What an edit does on the way: a new stage, profile or location is
    written to the state history first; a new herd name moves the animal's
    group membership (it must name an existing group of its species);
    archiving closes its memberships, lactation and pregnancy like an exit.
    """
    animal = livestock_service.load_animal_or_404(db, animal_id)
    identifier_service.ensure_legacy_identifier(db, animal, actor=access.membership_id)
    changes = payload.model_dump(exclude_unset=True)

    if not access.has("finance", "edit"):
        for field in _FINANCIAL_FIELDS:
            changes.pop(field, None)
    if changes.get("active") is False and animal.active and not access.has("animals", "delete"):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to archive an animal. Ask your farm owner or manager.",
        )
    for field in _DERIVED_FIELDS:
        changes.pop(field, None)
    for field in _REPRODUCTION_FIELDS:
        if (
            field in changes
            and changes[field] is not None
            and bool(changes[field]) != bool(getattr(animal, field))
        ):
            what = "a pregnancy" if field == "pregnant" else "a lactation"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{animal.name}'s {'pregnancy' if field == 'pregnant' else 'milking'} state comes "
                    f"from its records. Record {what} through the animal's reproduction actions "
                    "(pregnancy check, birth, loss, start of milking or dry-off)."
                ),
            )
        changes.pop(field, None)
    if "lactation_cycle" in changes:
        if changes["lactation_cycle"] != animal.lactation_cycle and db.scalar(
            select(rm.LactationEpisode.id)
            .where(rm.LactationEpisode.animal_id == animal.id, rm.LactationEpisode.source != "registration")
            .limit(1)
        ):
            raise HTTPException(
                status_code=422,
                detail=f"{animal.name}'s lactation number comes from its recorded lactations.",
            )
        if changes["lactation_cycle"] is not None and changes["lactation_cycle"] < 0:
            raise HTTPException(status_code=422, detail="The lactation number cannot be negative.")

    # Species is the one thing history depends on (§12): once anything has
    # been recorded about the animal, it stays what it was registered as.
    new_species = changes.get("species", animal.species)
    if new_species != animal.species:
        livestock_service.species_or_422(new_species)
        if _has_operational_history(db, animal):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{animal.name} already has milk, treatment, observation or reproduction records as a "
                    f"{animal.species.replace('_', ' ')}. Its species cannot be changed; "
                    "register a new animal instead."
                ),
            )

    # Sex, stage or profile changing re-evaluates what the animal can be
    # — a cow cannot move to the meat profile while she is recorded in milk.
    resolver_inputs = {"species", "sex", "life_stage", "management_profile"}
    cap_set: CapabilitySet | None = None
    if resolver_inputs & changes.keys():
        cap_set = capability_service.resolve(
            new_species,
            changes.get("sex", animal.sex),
            changes.get("life_stage", animal.life_stage),
            changes.get("management_profile", animal.management_profile),
        )
        animal_service.check_state_against(
            cap_set, name=animal.name, pregnant=bool(animal.pregnant), lactating=bool(animal.lactating)
        )

    new_tag = changes.pop("tag", None)
    group_name_sent = "group_name" in changes
    group_name = changes.pop("group_name", None)
    active = changes.pop("active", None)

    before = _snapshot(animal)
    # A weight is a dated record (HEALTH-VETERINARY-SCHEMA / per-weight
    # dosing); the animal's `weight_kg` is the latest of them.
    new_weight = changes.pop("weight_kg", None)
    for field, state_type in _HISTORY_FIELDS.items():
        if field in changes:
            livestock_state_service.record(
                db,
                animal,
                state_type,
                changes.pop(field),
                reason="edit",
                source_type="animal_update",
                user_id=access.user_id,
            )
    for field, value in changes.items():
        setattr(animal, field, value)
    if new_weight is not None and (animal.weight_kg is None or float(new_weight) != float(animal.weight_kg)):
        livestock_service.record_weight(
            db,
            animal,
            access=access,
            weight_kg=float(new_weight),
            method="scale",
            source_type="animal_update",
        )
    if group_name_sent and (group_name or "").strip().lower() != (animal.group_name or "").strip().lower():
        group_service.move_by_name(db, access, animal, group_name)
    if active is False and animal.active:
        livestock_state_service.exit_animal(
            db, access, animal, livestock_state_service.ARCHIVED, reason="archived"
        )
    elif active is True and not animal.active:
        livestock_state_service.reactivate(db, access, animal)
    after = _snapshot(animal)
    changed = {k for k in _AUDITED_ANIMAL_FIELDS if before.get(k) != after.get(k)}
    # A change to what the feeding resolver reads (stage, profile, weight…)
    # asks for a feeding review (feed architecture §10) — an event and a
    # task, never a silent reassignment. A group move reviewed itself.
    if animal.active:
        programs.review_animal_if_relevant(db, access, animal, changed - {"group_name"})
        # FEED-PERFORMANCE-INTELLIGENCE §12: a lifecycle change moves the
        # confounders, so the reading is taken again.
        if {"life_stage", "management_profile", "status"} & changed:
            perf.on_context_changed(db, access, "animal", animal, trigger="lifecycle_changed")
        if {"sex", "life_stage"} & changed:
            # A heifer that became an adult now belongs in "Open cows".
            group_service.sync_rule_groups(db, access, animal)
    if new_tag is not None and new_tag.strip() and new_tag.strip() != (animal.tag or ""):
        identifier_service.replace_primary_value(
            db,
            animal,
            new_tag.strip(),
            cap_set or livestock_service.capabilities_for_animal(animal),
            actor=access.membership_id,
        )
        after["tag"] = animal.tag
        changed.add("tag")

    animal.version += 1
    animal.last_modified_by = access.membership_id
    db.flush()
    if changed:
        _audit_animal(
            control_db,
            access,
            animal,
            action="animal.archived" if active is False and "active" in changed else "animal.updated",
            summary=f"Updated {animal.name}" + (f" #{animal.tag}" if animal.tag else ""),
            changes={k: {"from": before.get(k), "to": after.get(k)} for k in sorted(changed)},
        )
    return _to_animal_detail(db, control_db, animal)
