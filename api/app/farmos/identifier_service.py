"""Identifiers an animal carries — attaching, replacing, and keeping them
unique (generic animal model §4, §12).

The rules the resolver hands over (`allowed_identifier_types`,
`required_identifier_types`) are enforced here; the routers only ask.
Every query runs inside the tenant-scoped (RLS) session, so "this farm"
needs no extra filter.
"""

from __future__ import annotations

import uuid

from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import capability_service
from app.farmos.capability_service import CapabilitySet
from app.farmos.livestock_models import AnimalIdentifier
from app.farmos.schemas import IdentifierIn
from app.tenant_api.models import Animal


def legacy_tag_type(cap_set: CapabilitySet | None) -> str:
    """What an older client's bare `tag` means for this animal: an ear tag
    where the species wears one, a leg band where it wears that, and a
    farm number otherwise. Deterministic, so the same tablet always maps
    the same way."""
    if cap_set is None:
        return "FARM_NUMBER"
    allowed = cap_set.allowed_identifier_types()
    for candidate in ("EAR_TAG", "LEG_BAND"):
        if candidate in allowed:
            return candidate
    return "FARM_NUMBER"


def collect(
    payload_identifiers: list[IdentifierIn],
    legacy_tag: str | None,
    cap_set: CapabilitySet,
    *,
    check_required: bool = True,
) -> list[IdentifierIn]:
    """Merges the identifier list with a legacy `tag`, checks every type is
    permitted for this animal and — at registration — that every required
    type is present, and settles which one is primary. Adding one more
    identifier to an animal that already has its ear tag passes
    `check_required=False`; the retire endpoint guards the other direction."""
    identifiers = list(payload_identifiers)
    if legacy_tag:
        identifiers.append(
            IdentifierIn(identifier_type=legacy_tag_type(cap_set), identifier_value=legacy_tag)
        )

    allowed = cap_set.allowed_identifier_types()
    for ident in identifiers:
        if ident.identifier_type not in allowed:
            label = ident.identifier_type.replace("_", " ").lower()
            raise HTTPException(
                status_code=422,
                detail=(
                    f"A {label} is not used for this kind of animal. "
                    f"Allowed: {', '.join(sorted(allowed)).lower()}."
                ),
            )

    present = {i.identifier_type for i in identifiers}
    missing = cap_set.required_identifier_types() - present if check_required else set()
    if missing:
        needed = ", ".join(m.replace("_", " ").lower() for m in sorted(missing))
        raise HTTPException(status_code=422, detail=f"This animal must have a {needed} to be registered.")

    # Exactly one primary: the one marked, else the first.
    if identifiers and not any(i.is_primary for i in identifiers):
        identifiers[0] = identifiers[0].model_copy(update={"is_primary": True})
    primaries = [i for i in identifiers if i.is_primary]
    if len(primaries) > 1:
        keep = primaries[0]
        identifiers = [i if i is keep else i.model_copy(update={"is_primary": False}) for i in identifiers]
    return identifiers


def find_clash(
    db: Session,
    identifier_type: str,
    value: str,
    issuing_authority: str | None,
    exclude_animal_id: uuid.UUID | None = None,
) -> Animal | None:
    """Another active animal on this farm carrying the same identifier.
    Scope is (farm, type, issuing authority): farm number 744 may exist on
    two farms; RFID 982000123456789 issued by one authority may not."""
    stmt = (
        select(Animal)
        .join(AnimalIdentifier, AnimalIdentifier.animal_id == Animal.id)
        .where(
            Animal.active.is_(True),
            Animal.deleted_at.is_(None),
            AnimalIdentifier.status == "active",
            AnimalIdentifier.identifier_type == identifier_type,
            AnimalIdentifier.identifier_value == value,
        )
    )
    if issuing_authority is None:
        stmt = stmt.where(AnimalIdentifier.issuing_authority.is_(None))
    else:
        stmt = stmt.where(AnimalIdentifier.issuing_authority == issuing_authority)
    if exclude_animal_id is not None:
        stmt = stmt.where(Animal.id != exclude_animal_id)
    return db.scalars(stmt).first()


def ensure_unique(
    db: Session, identifiers: list[IdentifierIn], exclude_animal_id: uuid.UUID | None = None
) -> None:
    for ident in identifiers:
        clash = find_clash(
            db, ident.identifier_type, ident.identifier_value, ident.issuing_authority, exclude_animal_id
        )
        if clash is not None:
            label = ident.identifier_type.replace("_", " ").lower()
            raise HTTPException(
                status_code=409,
                detail=f"{label.capitalize()} '{ident.identifier_value}' is already used by {clash.name}.",
            )


def attach(
    db: Session, animal: Animal, identifiers: list[IdentifierIn], *, actor: uuid.UUID | None
) -> list[AnimalIdentifier]:
    rows = []
    for ident in identifiers:
        row = AnimalIdentifier(
            tenant_id=animal.tenant_id,
            last_modified_by=actor,
            animal_id=animal.id,
            identifier_type=ident.identifier_type,
            identifier_value=ident.identifier_value,
            issuing_authority=ident.issuing_authority,
            valid_from=ident.valid_from,
            valid_to=ident.valid_to,
            is_primary=ident.is_primary,
            status="active",
            notes=ident.notes,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    sync_tag(db, animal)
    return rows


def all_identifiers(db: Session, animal_id: uuid.UUID) -> list[AnimalIdentifier]:
    return list(
        db.scalars(
            select(AnimalIdentifier)
            .where(AnimalIdentifier.animal_id == animal_id, AnimalIdentifier.deleted_at.is_(None))
            .order_by(
                AnimalIdentifier.status, AnimalIdentifier.is_primary.desc(), AnimalIdentifier.created_at
            )
        )
    )


def active_identifiers(db: Session, animal_id: uuid.UUID) -> list[AnimalIdentifier]:
    return list(
        db.scalars(
            select(AnimalIdentifier)
            .where(
                AnimalIdentifier.animal_id == animal_id,
                AnimalIdentifier.status == "active",
                AnimalIdentifier.deleted_at.is_(None),
            )
            .order_by(AnimalIdentifier.is_primary.desc(), AnimalIdentifier.created_at)
        )
    )


def active_identifiers_for(
    db: Session, animal_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[AnimalIdentifier]]:
    """One query for a whole listing, grouped by animal."""
    grouped: dict[uuid.UUID, list[AnimalIdentifier]] = {}
    if not animal_ids:
        return grouped
    rows = db.scalars(
        select(AnimalIdentifier)
        .where(
            AnimalIdentifier.animal_id.in_(animal_ids),
            AnimalIdentifier.status == "active",
            AnimalIdentifier.deleted_at.is_(None),
        )
        .order_by(AnimalIdentifier.is_primary.desc(), AnimalIdentifier.created_at)
    ).all()
    for row in rows:
        grouped.setdefault(row.animal_id, []).append(row)
    return grouped


def sync_tag(db: Session, animal: Animal) -> None:
    """`animal.tag` mirrors the primary identifier's value so lists and
    search keep working unchanged. If nothing is primary, the first
    active identifier is promoted."""
    active = active_identifiers(db, animal.id)
    if not active:
        animal.tag = None
        return
    primary = next((i for i in active if i.is_primary), None)
    if primary is None:
        primary = active[0]
        primary.is_primary = True
    for other in active:
        if other is not primary and other.is_primary:
            other.is_primary = False
    animal.tag = primary.identifier_value


def ensure_legacy_identifier(db: Session, animal: Animal, *, actor: uuid.UUID | None) -> None:
    """An animal registered before identifiers existed — seeded, synced,
    or created by an older client — carries only `tag`. The first time its
    identifiers are touched, that tag becomes its primary identifier row,
    typed the way a bare tag is read for its species. Idempotent, and done
    here (inside the tenant-scoped request) rather than in the migration,
    which cannot see any tenant's rows under FORCE ROW LEVEL SECURITY."""
    if not animal.tag or not animal.tag.strip():
        return
    exists = db.scalar(select(AnimalIdentifier.id).where(AnimalIdentifier.animal_id == animal.id).limit(1))
    if exists is not None:
        return
    try:
        cap_set: CapabilitySet | None = capability_service.resolve_for_animal(animal)
    except capability_service.UnknownSpecies:
        cap_set = None
    attach(
        db,
        animal,
        [
            IdentifierIn(
                identifier_type=legacy_tag_type(cap_set), identifier_value=animal.tag.strip(), is_primary=True
            )
        ],
        actor=actor,
    )


def replace_primary_value(
    db: Session, animal: Animal, new_value: str, cap_set: CapabilitySet | None, *, actor: uuid.UUID | None
) -> None:
    """An older client editing `tag`: the old primary is retired, not
    erased — a treatment record that names the old tag still resolves —
    and a new primary of the same type takes its place."""
    active = active_identifiers(db, animal.id)
    primary = next((i for i in active if i.is_primary), None)
    identifier_type = primary.identifier_type if primary else legacy_tag_type(cap_set)
    replacement = IdentifierIn(identifier_type=identifier_type, identifier_value=new_value, is_primary=True)
    ensure_unique(db, [replacement], exclude_animal_id=animal.id)
    if primary is not None:
        if primary.identifier_value == new_value:
            return
        primary.status = "retired"
        primary.is_primary = False
    attach(db, animal, [replacement], actor=actor)
