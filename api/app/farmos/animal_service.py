"""Registering an animal — the start of its digital twin (OrigamiFarmOS
tech spec §13, generic animal model §2, LIVESTOCK-SCHEMA §2/§14).

One path for every way an animal arrives: the Add Animal form, a calf
registered at its birth, a chick picked out of a hatch. Species, sex,
stage and profile go through the capability resolver first; it decides
which identifiers the animal may and must carry, and whether it can be
registered pregnant or in milk. Registration then writes the opening
state history, turns an opening pregnancy or lactation into the episode it
stands for, and places the animal in its herd. The router writes the
audit entry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi.exceptions import HTTPException
from sqlalchemy.orm import Session

from app.farmos import capability_service, identifier_service, livestock_service
from app.farmos.capability_service import CapabilitySet
from app.farmos.deps import AccessContext
from app.farmos.livestock_catalog import Cap
from app.farmos.schemas import IdentifierIn
from app.tenant_api.models import Animal

# Money on an animal record is Finance data. Someone who looks after the
# herd does not automatically get to see what it cost.
FINANCIAL_FIELDS = ("purchase_cost", "current_value")
# Reproduction and lactation are not columns a registration sets: they
# become episodes (reproduction_service.open_registration_state).
_OPENING_STATE_FIELDS = (
    "pregnant",
    "pregnancy_days",
    "lactating",
    "lactation_cycle",
    "service_date",
    "service_type",
    "lactation_started_at",
)


def check_state_against(cap_set: CapabilitySet, *, name: str, pregnant: bool, lactating: bool) -> None:
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


def create_animal(
    db: Session,
    access: AccessContext,
    data: dict[str, Any],
    *,
    identifiers_in: list[IdentifierIn] | None = None,
    tag: str | None = None,
    registered_at: datetime | None = None,
) -> Animal:
    """Registers one animal from an AnimalCreate-shaped dict (without
    `identifiers`, `tag` and `confirm_duplicate`). Does not commit: a birth
    registers its offspring in the birth's own transaction."""
    from app.farmos import group_service, livestock_state_service, reproduction_service

    data = dict(data)
    livestock_service.species_or_422(data["species"])
    cap_set = capability_service.resolve(
        data["species"], data.get("sex"), data.get("life_stage"), data.get("management_profile")
    )
    pregnant = bool(data.get("pregnant"))
    lactating = bool(data.get("lactating"))
    check_state_against(cap_set, name=data["name"], pregnant=pregnant, lactating=lactating)
    identifiers = identifier_service.collect(identifiers_in or [], tag, cap_set)
    # Every animal gets the farm's next number — the human-readable ID on
    # its QR label — whatever else it carries; no tag has to be scanned or
    # typed to register it.
    identifiers = identifier_service.with_farm_number(db, identifiers)
    identifier_service.ensure_unique(db, identifiers)

    opening = {k: data.pop(k, None) for k in _OPENING_STATE_FIELDS}
    group_name = data.pop("group_name", None)
    if not access.has("finance", "create"):
        for field in FINANCIAL_FIELDS:
            data.pop(field, None)

    animal = Animal(
        tenant_id=access.tenant_id, last_modified_by=access.membership_id, lifecycle_status="active", **data
    )
    db.add(animal)
    db.flush()
    at = livestock_service.ensure_utc(registered_at) if registered_at else None
    livestock_state_service.open_initial(db, animal, at=at, user_id=access.user_id)
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
    breeding = None
    if opening["service_date"] is not None:
        breeding = reproduction_service.record_breeding(
            db,
            access,
            animal,
            event_type=opening["service_type"] or "ARTIFICIAL_INSEMINATION",
            at=opening["service_date"],
        )
    reproduction_service.open_registration_state(
        db,
        animal,
        pregnant=pregnant,
        pregnancy_days=opening["pregnancy_days"],
        lactating=lactating,
        lactation_cycle=opening["lactation_cycle"],
        user_id=access.user_id,
        breeding=breeding if pregnant else None,
        lactation_started_at=opening["lactation_started_at"],
    )
    if group_name and group_name.strip():
        group_service.move_by_name(db, access, animal, group_name, review=False)
    group_service.sync_rule_groups(db, access, animal)
    db.flush()
    return animal
