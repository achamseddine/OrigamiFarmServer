"""Species configuration, the capability resolver, identifiers, animal
groups and the care record (OrigamiFarmOS docs/GENERIC-ANIMAL-CAPABILITY-MODEL.md §11).

These are the endpoints that make "adding a species is configuration"
true for the tablet: it asks `/species` what exists, `/species/{code}/
configuration` what each needs, and `/animal-capabilities/resolve` what a
particular animal can do — then draws exactly that.
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
    capability_service,
    duplicates,
    group_service,
    identifier_service,
    livestock_service,
    livestock_state_service,
    reproduction_service,
)
from app.farmos import feed_performance_service as perf
from app.farmos import feeding_program_service as programs
from app.farmos import livestock_catalog as catalog
from app.farmos.deps import (
    AccessContext,
    get_access_context,
    get_farmos_tenant_db,
    require_any_permission,
    require_diagnostic_role,
    require_permission,
)
from app.farmos.livestock_models import AnimalGroup, AnimalHealthCondition, AnimalWeightRecord
from app.farmos.reproduction_schemas import (
    AnimalExitIn,
    AnimalRestoreIn,
    MembershipEndIn,
    MembershipIn,
    ParentIn,
    QuantityEventIn,
)
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

# Herd records are read wherever an animal or a group is worked with.
_HERD_VIEW = require_any_permission(
    ("animals", "view"),
    ("animal_health", "view"),
    ("milk_production", "view"),
    ("feed_nutrition", "view"),
    ("egg_production", "view"),
)
_HERD_EDIT = require_permission("animals", "edit")
# A flock's mortality and additions are recorded by whoever keeps it.
_HEADCOUNT_CREATE = require_any_permission(("animals", "create"), ("egg_production", "create"))


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
    """A new herd, flock, pen, batch or cohort (LIVESTOCK-SCHEMA §6–§8).
    A group either counts heads or counts its registered members; a rule
    group (pregnant, open, in milk, dry) is a cohort that fills itself."""
    species = livestock_service.species_or_422(payload.species)
    # One name per group of a species: two "Pregnant cows" would split the herd's records.
    clash = db.scalar(
        select(AnimalGroup).where(
            AnimalGroup.species == payload.species,
            func.lower(AnimalGroup.name) == payload.name.strip().lower(),
            AnimalGroup.status.notin_(("closed", "archived")),
            AnimalGroup.deleted_at.is_(None),
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=409, detail=f"A group named {clash.name} already exists for this species."
        )
    data = payload.model_dump()
    if data.get("membership_rule"):
        # A rule group (pregnant, open, in milk, dry) overlaps the herd and
        # the pen the animal is in — it is a cohort counting its members.
        if group_service.policy(data.get("group_type")).exclusive_membership:
            data["group_type"] = "cohort"
        data["tracking_mode"] = group_service.MEMBERSHIP_DERIVED
        data["count"] = 0
    # LIVESTOCK-SCHEMA §6: a group either counts heads or counts its
    # registered members. Unstated: a head count given means a counted
    # group; otherwise a species kept individually counts members.
    if not data.get("tracking_mode"):
        individual = species["default_management"] == "individual"
        data["tracking_mode"] = (
            group_service.MEMBERSHIP_DERIVED
            if individual and not data.get("count")
            else group_service.QUANTITY_MANAGED
        )
    group = AnimalGroup(tenant_id=access.tenant_id, last_modified_by=access.membership_id, **data)
    cap_set = capability_service.resolve_for_group(group)
    # A counted flock needs the species to be manageable as a group; a herd
    # of registered cows only needs its members to be individuals.
    needed = (
        catalog.Cap.INDIVIDUAL_TRACKING
        if group.tracking_mode == group_service.MEMBERSHIP_DERIVED
        else catalog.Cap.GROUP_TRACKING
    )
    if not cap_set.has(needed):
        word = cap_set.terminology.get("group_en", "Group")
        raise HTTPException(
            status_code=422,
            detail=(
                f"{word} management is not configured for this species; register the animals individually."
                if needed == catalog.Cap.GROUP_TRACKING
                else f"{species['name_en']} are not tracked individually; keep them as a counted group."
            ),
        )
    db.add(group)
    db.flush()
    group_service.open_group(db, access, group)
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
    if group.membership_rule:
        group_service.fill_rule_group(db, access, group)
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
    # A head count is never typed over (LIVESTOCK-SCHEMA §9): a new count
    # becomes a COUNT_ADJUSTMENT in the group's ledger, and a group that
    # counts its members takes its count from them.
    if "count" in changes:
        new_count = changes.pop("count")
        if new_count is not None and new_count != group.count:
            if group.tracking_mode == group_service.MEMBERSHIP_DERIVED:
                raise HTTPException(
                    status_code=422,
                    detail=f"{group.name} counts its registered members; add or remove animals instead.",
                )
            audit_changes["count"] = {"from": group.count, "to": new_count}
            group_service.set_count(db, access, group, new_count)
            changes["count"] = new_count
    for field, value in changes.items():
        if field == "count":
            continue
        before = getattr(group, field)
        if before != value:
            audit_changes[field] = {"from": before, "to": value}
        setattr(group, field, value)
    group.version += 1
    group.last_modified_by = access.membership_id
    db.flush()
    # A group that grew, changed stage or changed profile may fit a different
    # feeding program — a review, never a silent reassignment (feed §10); a
    # group change is a feed-performance confounder (§12).
    if {"count", "life_stage", "management_profile", "sex_composition"} & changes.keys():
        programs.review_feeding(db, access, "group", group, trigger="group_changed")
        perf.on_context_changed(db, access, "group", group, trigger="group_changed")
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


# ------------------------------------------------------------ group membership (LIVESTOCK-SCHEMA §7–§9)
@router.get("/animal-group-types")
def list_group_types(_access: AccessContext = Depends(get_access_context)) -> list[dict[str, Any]]:
    return [p.to_dict() for p in sorted(group_service.POLICIES.values(), key=lambda p: p.group_type)]


@router.get("/animal-groups/{group_id}/members")
def list_members(
    group_id: str,
    include_past: bool = Query(default=False),
    _access: AccessContext = Depends(_HERD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    """Who is in the group now — or, with `include_past`, everyone who ever
    was, with the dates (membership is closed, never deleted)."""
    group = livestock_service.load_group_or_404(db, group_id)
    rows = (
        group_service.member_history(db, group) if include_past else group_service.current_members(db, group)
    )
    return [group_service.membership_dict(db, m, animal=a, group=group) for m, a in rows]


@router.post("/animal-groups/{group_id}/members", status_code=201)
def add_member(
    group_id: str,
    payload: MembershipIn,
    access: AccessContext = Depends(_HERD_EDIT),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """Puts an animal in the group. In an exclusive group (a herd, a pen)
    this is a move: the animal's previous exclusive membership closes."""
    group = livestock_service.load_group_or_404(db, group_id)
    animal = livestock_service.load_animal_or_404(db, payload.animal_id)
    membership = group_service.add_member(
        db, access, group, animal, at=payload.valid_from, reason=payload.reason
    )
    _audit(
        control_db,
        access,
        action="animal_group.member_added",
        entity_type="animal_group",
        entity_id=str(group.id),
        module_code="animals",
        summary=f"Added {animal.name} to {group.name}",
    )
    return group_service.membership_dict(db, membership, animal=animal, group=group)


@router.post("/animal-groups/{group_id}/members/{animal_id}/end")
def end_member(
    group_id: str,
    animal_id: str,
    payload: MembershipEndIn,
    access: AccessContext = Depends(_HERD_EDIT),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    group = livestock_service.load_group_or_404(db, group_id)
    animal = livestock_service.load_animal_or_404(db, animal_id)
    membership = group_service.remove_member(
        db, access, group, animal, at=payload.valid_to, reason=payload.reason
    )
    _audit(
        control_db,
        access,
        action="animal_group.member_removed",
        entity_type="animal_group",
        entity_id=str(group.id),
        module_code="animals",
        summary=f"Took {animal.name} out of {group.name}",
    )
    return group_service.membership_dict(db, membership, animal=animal, group=group)


@router.get("/animal-groups/{group_id}/quantity-events")
def list_quantity_events(
    group_id: str,
    _access: AccessContext = Depends(_HERD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    group = livestock_service.load_group_or_404(db, group_id)
    return [group_service.quantity_dict(e) for e in group_service.quantity_history(db, group)]


@router.post("/animal-groups/{group_id}/quantity-events", status_code=201)
def record_quantity_event(
    group_id: str,
    payload: QuantityEventIn,
    access: AccessContext = Depends(_HEADCOUNT_CREATE),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """A flock's deaths, sales, purchases, transfers or a recount — one row
    of its head-count ledger; `count` follows."""
    group = livestock_service.load_group_or_404(db, group_id)
    event = group_service.record_quantity(
        db,
        access,
        group,
        payload.event_type,
        payload.quantity_delta,
        at=payload.occurred_at,
        reason=payload.reason,
        source_type="manual",
    )
    _audit(
        control_db,
        access,
        action="animal_group.quantity_changed",
        entity_type="animal_group",
        entity_id=str(group.id),
        module_code="animals",
        summary=(
            f"Recorded {payload.event_type.lower().replace('_', ' ')} "
            f"{payload.quantity_delta:+d} on {group.name}"
        ),
        changes={"count": {"to": group.count}},
    )
    return group_service.quantity_dict(event)


# ------------------------------- animal history, exit, parentage (LIVESTOCK-SCHEMA §5, §10, §14)
@router.get("/animals/{animal_id}/history")
def animal_history(
    animal_id: str,
    state_type: str | None = None,
    _access: AccessContext = Depends(_HERD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    """The animal's effective-dated status, stage, profile and location."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    if state_type and state_type.upper() not in livestock_state_service.STATE_TYPES:
        raise HTTPException(
            status_code=422, detail=f"state_type must be one of {list(livestock_state_service.STATE_TYPES)}"
        )
    rows = livestock_state_service.history(db, animal.id, state_type.upper() if state_type else None)
    return [livestock_state_service.history_dict(h) for h in rows]


@router.get("/animals/{animal_id}/groups")
def animal_groups(
    animal_id: str,
    _access: AccessContext = Depends(_HERD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> list[dict[str, Any]]:
    animal = livestock_service.load_animal_or_404(db, animal_id)
    return [
        group_service.membership_dict(db, m, animal=animal)
        for m in group_service.memberships_of_animal(db, animal.id)
    ]


@router.post("/animals/{animal_id}/exit")
def exit_animal(
    animal_id: str,
    payload: AnimalExitIn,
    access: AccessContext = Depends(get_access_context),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """The animal left the farm: sold, dead, culled, slaughtered or
    transferred. Its groups, lactation, pregnancy and feeding close; its
    record stays. A death is recorded by whoever looks after the animal
    (Animals · edit); the others are a manager's call (Animals · approve)."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    action = "edit" if payload.exit_type in livestock_state_service.FACT_EXITS else "approve"
    if not access.has("animals", action):
        raise HTTPException(
            status_code=403,
            detail=f"You do not have permission to {action} in Animals. Ask a farm manager to grant it.",
        )
    livestock_state_service.exit_animal(
        db, access, animal, payload.exit_type, at=payload.occurred_at, reason=payload.reason
    )
    _audit(
        control_db,
        access,
        action="animal.exited",
        entity_type="animal",
        entity_id=str(animal.id),
        module_code="animals",
        summary=f"Recorded {animal.name} as {payload.exit_type}"
        + (f": {payload.reason}" if payload.reason else ""),
        changes={"lifecycle_status": {"from": livestock_state_service.ACTIVE, "to": payload.exit_type}},
        metadata={"notes": payload.notes} if payload.notes else None,
    )
    return {
        "id": str(animal.id),
        "lifecycle_status": animal.lifecycle_status,
        "exited_at": animal.exited_at,
        "active": animal.active,
    }


@router.post("/animals/{animal_id}/restore")
def restore_animal(
    animal_id: str,
    payload: AnimalRestoreIn,
    access: AccessContext = Depends(require_permission("animals", "delete")),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """Brings back an animal archived by mistake. A sale or a death is not
    undone this way."""
    animal = livestock_service.load_animal_or_404(db, animal_id)
    was_active = animal.active
    livestock_state_service.reactivate(db, access, animal, reason=payload.reason)
    if not was_active:
        _audit(
            control_db,
            access,
            action="animal.restored",
            entity_type="animal",
            entity_id=str(animal.id),
            module_code="animals",
            summary=f"Restored {animal.name}" + (f": {payload.reason}" if payload.reason else ""),
            changes={
                "lifecycle_status": {"from": livestock_state_service.ARCHIVED, "to": animal.lifecycle_status}
            },
        )
    return {"id": str(animal.id), "lifecycle_status": animal.lifecycle_status, "active": animal.active}


@router.get("/animals/{animal_id}/relationships")
def animal_relationships(
    animal_id: str,
    _access: AccessContext = Depends(_HERD_VIEW),
    db: Session = Depends(get_farmos_tenant_db),
) -> dict[str, Any]:
    return reproduction_service.relationships(db, livestock_service.load_animal_or_404(db, animal_id))


@router.post("/animals/{animal_id}/parents", status_code=201)
def record_parent(
    animal_id: str,
    payload: ParentIn,
    access: AccessContext = Depends(_HERD_EDIT),
    db: Session = Depends(get_farmos_tenant_db),
    control_db: Session = Depends(get_control_db),
) -> dict[str, Any]:
    """Parentage for an animal that arrived with known parents on this
    farm. A birth recorded here links its own parentage."""
    child = livestock_service.load_animal_or_404(db, animal_id)
    try:
        parent = livestock_service.load_animal_or_404(db, payload.parent_animal_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Parent not found.") from None
    reproduction_service.record_parent(
        db, access, child, parent, payload.relationship_type, evidence=payload.evidence
    )
    _audit(
        control_db,
        access,
        action="animal.parent_recorded",
        entity_type="animal",
        entity_id=str(child.id),
        module_code="animals",
        summary=(
            f"Recorded {parent.name} as {payload.relationship_type.replace('_OF', '').lower()} "
            f"of {child.name}"
        ),
    )
    return reproduction_service.relationships(db, child)


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
    livestock_service.validate_weight_input(payload.weight_kg, payload.method, payload.measured_at)
    # Weighed twice the same day: the second entry is a possible duplicate
    # until the person says it is a real second weighing.
    day_start, day_end = duplicates.farm_day(
        duplicates.tenant_timezone(control_db, access.tenant_id), payload.measured_at
    )
    same_day = db.scalar(
        select(AnimalWeightRecord)
        .where(
            AnimalWeightRecord.animal_id == animal.id,
            AnimalWeightRecord.deleted_at.is_(None),
            AnimalWeightRecord.measured_at >= day_start,
            AnimalWeightRecord.measured_at < day_end,
        )
        .order_by(AnimalWeightRecord.measured_at.desc())
        .limit(1)
    )
    match = duplicates.guard(
        same_day,
        f"{animal.name} was already weighed that day ({float(same_day.weight_kg):g} kg)." if same_day else "",
        confirm=payload.confirm_duplicate,
    )
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
    duplicates.log_confirmed(
        control_db,
        access,
        entity_type="animal",
        entity_id=str(animal.id),
        match_id=match.id if match else None,
        what=f"weight:{row.id}",
        module_code="animals",
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
