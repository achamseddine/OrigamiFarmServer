"""Animal groups as first-class subjects (OrigamiFarmOS
database/LIVESTOCK-SCHEMA.md §6–§9).

Membership is effective-dated and closed, never deleted. A group's type
policy decides whether membership is exclusive (a herd, a pen: joining
another closes the first) or overlapping (a cohort). A group's tracking
mode decides what its head count is:

- MEMBERSHIP_DERIVED — the number of current members;
- QUANTITY_MANAGED / HYBRID — the running total of its quantity ledger
  (`animal_group_quantity_event`); registered individuals in a hybrid
  flock never redefine its total.

Two projections are kept here and nowhere else: `animal_group.count`, and
`animal.group_name` — the name of the animal's current exclusive group,
which the feeding resolver and the tablet read.

The group type policies are code-defined (livestock_catalog.GROUP_TYPES)
like the rest of the server's catalog; `GET /animal-group-types` serves them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.farmos import livestock_catalog as catalog
from app.farmos.deps import AccessContext
from app.farmos.livestock_models import AnimalGroup, AnimalGroupMembership, AnimalGroupQuantityEvent
from app.farmos.livestock_service import ensure_utc, now_utc
from app.farmos.livestock_state_service import LivestockError, ensure_on_farm
from app.tenant_api.models import Animal

MEMBERSHIP_DERIVED = "MEMBERSHIP_DERIVED"
QUANTITY_MANAGED = "QUANTITY_MANAGED"
HYBRID = "HYBRID"

QUANTITY_EVENT_TYPES = {
    "INITIAL_COUNT": 0, "COUNT_ADJUSTMENT": 0,  # either sign
    "ADDITION": 1, "PURCHASE": 1, "BIRTH": 1, "HATCH": 1, "TRANSFER_IN": 1,
    "REMOVAL": -1, "DEATH": -1, "SALE": -1, "CULL": -1, "SLAUGHTER": -1, "TRANSFER_OUT": -1,
}  # fmt: skip

# Groups whose members follow the herd records (animal_group.membership_rule):
# the pregnant cows, the open (not pregnant) cows, those in milk, those dry.
# Membership is still effective-dated rows opened and closed here — a cow
# confirmed pregnant leaves "Open cows" and joins "Pregnant cows" the day
# the check is recorded, and the history says so.
MEMBERSHIP_RULES = ("PREGNANT", "NOT_PREGNANT", "IN_MILK", "DRY")
_BREEDING_STAGES = ("adult", "senior")
_CLOSED = ("closed", "archived")


@dataclass(frozen=True)
class GroupTypePolicy:
    """What a kind of group allows (LIVESTOCK-SCHEMA §8): a physical herd or
    pen is exclusive — joining another closes the first — while an
    analytical cohort may overlap with anything."""

    group_type: str
    label_en: str
    label_ar: str
    exclusive_membership: bool
    allows_individual_members: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_type": self.group_type,
            "label_en": self.label_en,
            "label_ar": self.label_ar,
            "exclusive_membership": self.exclusive_membership,
            "allows_individual_members": self.allows_individual_members,
        }


POLICIES: dict[str, GroupTypePolicy] = {
    code: GroupTypePolicy(code, en, ar, exclusive, individual)
    for code, en, ar, exclusive, individual in catalog.GROUP_TYPES
}


def policy(group_type: str | None) -> GroupTypePolicy:
    """The type's policy; an unknown type is a physical, exclusive group —
    the safe reading, since it never lets an animal be in two pens."""
    known = POLICIES.get(group_type or "")
    if known is not None:
        return known
    return GroupTypePolicy(group_type or "herd", group_type or "Group", group_type or "مجموعة", True, True)


def _utc(value: datetime | None) -> datetime:
    at = ensure_utc(value)
    return at if at is not None else now_utc()


def _uuid_or_none(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def load_group(db: Session, group_id: str | uuid.UUID | None) -> AnimalGroup | None:
    pk = _uuid_or_none(group_id)
    if pk is None:
        return None
    group = db.get(AnimalGroup, pk)
    return group if group is not None and group.deleted_at is None else None


def _species_label(code: str) -> str:
    return code.replace("_", " ")


# ------------------------------------------------------------------ rules
def rule_matches(rule: str, animal: Animal) -> bool:
    """Whether an animal on the farm belongs in a group of this rule. "Not
    pregnant" and "dry" mean breeding-age females — a calf is neither."""
    if not animal.active:
        return False
    female = (animal.sex or "").upper() == "F"
    breeding_age = (animal.life_stage or catalog.DEFAULT_LIFE_STAGE) in _BREEDING_STAGES
    if rule == "PREGNANT":
        return bool(animal.pregnant)
    if rule == "NOT_PREGNANT":
        return female and breeding_age and not animal.pregnant
    if rule == "IN_MILK":
        return bool(animal.lactating)
    if rule == "DRY":
        return female and breeding_age and not animal.lactating
    return False


def sync_rule_groups(
    db: Session, access: AccessContext, animal: Animal, *, groups: list[AnimalGroup] | None = None
) -> None:
    """Moves the animal into, or out of, every rule group of its species on
    its farm, so the groups always say what the records say."""
    if groups is None:
        groups = list(
            db.scalars(
                select(AnimalGroup).where(
                    AnimalGroup.species == animal.species,
                    AnimalGroup.membership_rule.is_not(None),
                    AnimalGroup.deleted_at.is_(None),
                )
            )
        )
    if not groups:
        return
    current = {m.animal_group_id for m in memberships_of_animal(db, animal.id, current_only=True)}
    for group in groups:
        if group.status in _CLOSED or group.membership_rule not in MEMBERSHIP_RULES:
            continue
        should = rule_matches(group.membership_rule, animal)
        if should and group.id not in current:
            add_member(db, access, group, animal, reason=f"rule:{group.membership_rule}", review=False)
        elif not should and group.id in current:
            remove_member(db, access, group, animal, reason=f"rule:{group.membership_rule}")


def fill_rule_group(db: Session, access: AccessContext, group: AnimalGroup) -> int:
    """A new rule group starts with every animal that already matches."""
    added = 0
    for animal in db.scalars(
        select(Animal).where(
            Animal.species == group.species, Animal.active.is_(True), Animal.deleted_at.is_(None)
        )
    ).all():
        if rule_matches(group.membership_rule or "", animal):
            add_member(db, access, group, animal, reason=f"rule:{group.membership_rule}", review=False)
            added += 1
    return added


# -------------------------------------------------------------- reads
def _active_at(at: datetime | None = None) -> Any:
    at = at or now_utc()
    return (AnimalGroupMembership.valid_from <= at) & (
        AnimalGroupMembership.valid_to.is_(None) | (AnimalGroupMembership.valid_to > at)
    )


def memberships_of_animal(
    db: Session, animal_id: uuid.UUID, *, current_only: bool = False
) -> list[AnimalGroupMembership]:
    db.flush()  # a membership closed in this transaction must read as closed
    stmt = select(AnimalGroupMembership).where(
        AnimalGroupMembership.animal_id == animal_id, AnimalGroupMembership.deleted_at.is_(None)
    )
    if current_only:
        stmt = stmt.where(AnimalGroupMembership.valid_to.is_(None))
    return list(db.scalars(stmt.order_by(AnimalGroupMembership.valid_from.desc())))


def current_members(
    db: Session, group: AnimalGroup, at: datetime | None = None
) -> list[tuple[AnimalGroupMembership, Animal]]:
    db.flush()
    rows = db.execute(
        select(AnimalGroupMembership, Animal)
        .join(Animal, Animal.id == AnimalGroupMembership.animal_id)
        .where(
            AnimalGroupMembership.animal_group_id == group.id,
            AnimalGroupMembership.deleted_at.is_(None),
            _active_at(ensure_utc(at) if at else None),
        )
        .order_by(Animal.name)
    ).all()
    return [(m, a) for m, a in rows]


def member_history(db: Session, group: AnimalGroup) -> list[tuple[AnimalGroupMembership, Animal]]:
    db.flush()
    rows = db.execute(
        select(AnimalGroupMembership, Animal)
        .join(Animal, Animal.id == AnimalGroupMembership.animal_id)
        .where(AnimalGroupMembership.animal_group_id == group.id, AnimalGroupMembership.deleted_at.is_(None))
        .order_by(AnimalGroupMembership.valid_from.desc())
    ).all()
    return [(m, a) for m, a in rows]


def exclusive_group_of(db: Session, animal: Animal) -> AnimalGroup | None:
    """The animal's current herd / flock / pen — the group it inherits a
    feeding program from (feed architecture §9)."""
    for membership in memberships_of_animal(db, animal.id, current_only=True):
        group = load_group(db, membership.animal_group_id)
        if group is not None and policy(group.group_type).exclusive_membership:
            return group
    return None


def _refresh_animal_group_name(db: Session, animal: Animal) -> bool:
    group = exclusive_group_of(db, animal)
    name = group.name if group is not None else None
    if animal.group_name != name:
        animal.group_name = name
        return True
    return False


def refresh_count(db: Session, group: AnimalGroup) -> None:
    if group.tracking_mode == MEMBERSHIP_DERIVED:
        db.flush()
        group.count = (
            db.scalar(
                select(func.count(AnimalGroupMembership.id)).where(
                    AnimalGroupMembership.animal_group_id == group.id,
                    AnimalGroupMembership.valid_to.is_(None),
                    AnimalGroupMembership.deleted_at.is_(None),
                )
            )
            or 0
        )


def _on_animal_regrouped(db: Session, access: AccessContext, animal: Animal) -> None:
    """A group change is a feeding-review trigger and a feed-performance
    confounder (feed §10, FEED-PERFORMANCE-INTELLIGENCE §12) — a review,
    never a silent reassignment."""
    from app.farmos import feed_performance_service, feeding_program_service

    feeding_program_service.review_animal_if_relevant(db, access, animal, {"group_name"})
    feed_performance_service.on_context_changed(db, access, "animal", animal, trigger="group_moved")


def _on_group_changed(db: Session, access: AccessContext, group: AnimalGroup) -> None:
    from app.farmos import feed_performance_service, feeding_program_service

    feeding_program_service.review_feeding(db, access, "group", group, trigger="group_changed")
    feed_performance_service.on_context_changed(db, access, "group", group, trigger="group_changed")


def _touch(row: Any, access: AccessContext) -> None:
    row.version += 1
    row.last_modified_by = access.membership_id


# -------------------------------------------------------------- membership
def add_member(
    db: Session,
    access: AccessContext,
    group: AnimalGroup,
    animal: Animal,
    *,
    at: datetime | None = None,
    reason: str | None = None,
    review: bool = True,
) -> AnimalGroupMembership:
    """`review=False` for an animal joining its herd as it is registered:
    it inherits the herd's program, which is no move to review."""
    at = _utc(at)
    if at > now_utc():
        raise LivestockError("A membership cannot start in the future.")
    ensure_on_farm(animal, "a group move")
    if animal.species != group.species:
        raise LivestockError(
            f"{animal.name} is a {_species_label(animal.species)}; {group.name} is a group of "
            f"{_species_label(group.species)}. A group holds one species."
        )
    if group.status in _CLOSED:
        raise LivestockError(f"{group.name} is {group.status}.", 409)
    group_policy = policy(group.group_type)
    if not group_policy.allows_individual_members:
        raise LivestockError(f"A {group_policy.label_en.lower()} is counted, not made of registered animals.")
    for m in memberships_of_animal(db, animal.id, current_only=True):
        if m.animal_group_id == group.id:
            raise LivestockError(f"{animal.name} is already in {group.name}.", 409)
    moved_from: list[AnimalGroup] = []
    if group_policy.exclusive_membership:
        # A physical group: joining it is a move out of the previous one.
        for m in memberships_of_animal(db, animal.id, current_only=True):
            other = load_group(db, m.animal_group_id)
            if other is not None and policy(other.group_type).exclusive_membership:
                joined = _utc(m.valid_from)
                if at < joined:
                    raise LivestockError(
                        f"{animal.name} joined {other.name} on {joined.date().isoformat()}; "
                        "a move cannot be dated before that."
                    )
                m.valid_to = at
                m.end_reason_code = f"moved_to:{group.id}"
                m.ended_by = access.user_id
                _touch(m, access)
                moved_from.append(other)
    membership = AnimalGroupMembership(
        tenant_id=group.tenant_id,
        last_modified_by=access.membership_id,
        animal_group_id=group.id,
        animal_id=animal.id,
        valid_from=at,
        reason_code=reason,
        created_by=access.user_id,
    )
    db.add(membership)
    if group.tracking_mode == QUANTITY_MANAGED:
        # Registering an individual in a counted flock makes it hybrid: the
        # count stays the flock's own (LIVESTOCK-SCHEMA §6).
        group.tracking_mode = HYBRID
    _touch(group, access)
    db.flush()
    for g in (*moved_from, group):
        refresh_count(db, g)
    changed = _refresh_animal_group_name(db, animal)
    if review and changed:
        _on_animal_regrouped(db, access, animal)
    if review and (group.tracking_mode == MEMBERSHIP_DERIVED or moved_from):
        _on_group_changed(db, access, group)
    return membership


def remove_member(
    db: Session,
    access: AccessContext,
    group: AnimalGroup,
    animal: Animal,
    *,
    at: datetime | None = None,
    reason: str | None = None,
    quantity_event: str | None = None,
) -> AnimalGroupMembership:
    at = _utc(at)
    membership = next(
        (m for m in memberships_of_animal(db, animal.id, current_only=True) if m.animal_group_id == group.id),
        None,
    )
    if membership is None:
        raise LivestockError(f"{animal.name} is not in {group.name}.", 404)
    joined = _utc(membership.valid_from)
    if at < joined:
        raise LivestockError(
            f"{animal.name} joined {group.name} on {joined.date().isoformat()}; "
            "leaving cannot be dated before that."
        )
    membership.valid_to = at
    membership.end_reason_code = reason or "removed"
    membership.ended_by = access.user_id
    _touch(membership, access)
    if quantity_event and group.tracking_mode in (HYBRID, QUANTITY_MANAGED) and (group.count or 0) > 0:
        record_quantity(
            db,
            access,
            group,
            quantity_event,
            -1,
            at=at,
            reason=f"{animal.name}: {reason or quantity_event.lower()}",
            source_type="animal",
            source_id=str(animal.id),
        )
    _touch(group, access)
    refresh_count(db, group)
    changed = _refresh_animal_group_name(db, animal)
    if changed and animal.active:
        _on_animal_regrouped(db, access, animal)
    if group.tracking_mode == MEMBERSHIP_DERIVED:
        _on_group_changed(db, access, group)
    return membership


def end_memberships_on_exit(
    db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    at: datetime,
    quantity_event: str | None,
    reason: str,
) -> None:
    """An animal that died or was sold leaves every group it was in; a
    counted flock it belonged to records the loss in its own ledger."""
    for membership in memberships_of_animal(db, animal.id, current_only=True):
        group = load_group(db, membership.animal_group_id)
        if group is not None:
            remove_member(
                db,
                access,
                group,
                animal,
                at=max(at, _utc(membership.valid_from)),
                reason=reason,
                quantity_event=quantity_event,
            )


def move_by_name(
    db: Session, access: AccessContext, animal: Animal, name: str | None, *, review: bool = True
) -> None:
    """The edit form's herd / group field (and older clients' free-text
    `group_name`): an existing group of the animal's species, by name, or
    blank to leave its current herd. A name that matches no group is
    refused with the names that would — a typo must not create a herd."""
    target_name = (name or "").strip()
    current_group = exclusive_group_of(db, animal)
    if not target_name:
        if current_group is not None:
            remove_member(db, access, current_group, animal, reason="left_group")
        return
    if current_group is not None and current_group.name.strip().lower() == target_name.lower():
        return
    group = db.scalar(
        select(AnimalGroup).where(
            AnimalGroup.species == animal.species,
            func.lower(AnimalGroup.name) == target_name.lower(),
            AnimalGroup.deleted_at.is_(None),
        )
    )
    if group is None:
        names = sorted(
            g.name
            for g in db.scalars(
                select(AnimalGroup).where(
                    AnimalGroup.species == animal.species, AnimalGroup.deleted_at.is_(None)
                )
            )
        )
        raise LivestockError(
            f"There is no group called '{target_name}' for {_species_label(animal.species)}. "
            + (
                f"Choose one of: {', '.join(names)} — or create the group first."
                if names
                else "Create the group first."
            )
        )
    add_member(db, access, group, animal, reason="edit" if review else "registered", review=review)


# -------------------------------------------------------------- quantity
def record_quantity(
    db: Session,
    access: AccessContext,
    group: AnimalGroup,
    event_type: str,
    quantity_delta: int,
    *,
    at: datetime | None = None,
    reason: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    correlation_id: str | None = None,
) -> AnimalGroupQuantityEvent:
    """One head-count movement. The total is the running sum in the order
    the movements are recorded; a backdated entry keeps its occurred-at but
    never rewrites totals already reported."""
    if event_type not in QUANTITY_EVENT_TYPES:
        raise LivestockError(f"event_type must be one of {sorted(QUANTITY_EVENT_TYPES)}")
    if group.tracking_mode == MEMBERSHIP_DERIVED:
        raise LivestockError(
            f"{group.name} counts its registered members; "
            "add or remove animals instead of changing its count."
        )
    sign = QUANTITY_EVENT_TYPES[event_type]
    if quantity_delta == 0 and event_type != "INITIAL_COUNT":
        raise LivestockError("A head-count change must move the count.")
    if (sign > 0 and quantity_delta < 0) or (sign < 0 and quantity_delta > 0):
        raise LivestockError(
            f"A {event_type.lower().replace('_', ' ')} {'adds' if sign > 0 else 'removes'} animals; "
            "send the quantity with that sign."
        )
    at = _utc(at)
    if at > now_utc():
        raise LivestockError("A head-count change cannot be dated in the future.")
    resulting = (group.count or 0) + quantity_delta
    if resulting < 0:
        raise LivestockError(
            f"{group.name} has {group.count or 0}; removing {-quantity_delta} would leave a negative count."
        )
    row = AnimalGroupQuantityEvent(
        tenant_id=group.tenant_id,
        last_modified_by=access.membership_id,
        animal_group_id=group.id,
        event_type=event_type,
        quantity_delta=quantity_delta,
        resulting_quantity=resulting,
        occurred_at=at,
        reason=reason,
        source_type=source_type,
        source_id=source_id,
        recorded_at=now_utc(),
        recorded_by=access.user_id,
        correlation_id=correlation_id,
    )
    db.add(row)
    group.count = resulting
    _touch(group, access)
    db.flush()
    _on_group_changed(db, access, group)
    return row


def set_count(
    db: Session, access: AccessContext, group: AnimalGroup, new_count: int, *, reason: str | None = None
) -> AnimalGroupQuantityEvent | None:
    """An older client's "count = N" edit, written as the adjustment it is."""
    if new_count == (group.count or 0):
        return None
    return record_quantity(
        db,
        access,
        group,
        "COUNT_ADJUSTMENT",
        new_count - (group.count or 0),
        reason=reason or "count corrected",
    )


def open_group(db: Session, access: AccessContext, group: AnimalGroup) -> None:
    """A new group's opening count, as the first row of its ledger. The
    group must already be flushed (it needs its id)."""
    if group.tracking_mode not in catalog.TRACKING_MODES:
        raise LivestockError(f"tracking_mode must be one of {list(catalog.TRACKING_MODES)}")
    if group.tracking_mode == MEMBERSHIP_DERIVED:
        group.count = 0
        return
    if (group.count or 0) > 0:
        db.add(
            AnimalGroupQuantityEvent(
                tenant_id=group.tenant_id,
                last_modified_by=access.membership_id,
                animal_group_id=group.id,
                event_type="INITIAL_COUNT",
                quantity_delta=group.count,
                resulting_quantity=group.count,
                occurred_at=now_utc(),
                reason="Opening count",
                recorded_at=now_utc(),
                recorded_by=access.user_id,
            )
        )


def quantity_history(db: Session, group: AnimalGroup) -> list[AnimalGroupQuantityEvent]:
    db.flush()
    return list(
        db.scalars(
            select(AnimalGroupQuantityEvent)
            .where(
                AnimalGroupQuantityEvent.animal_group_id == group.id,
                AnimalGroupQuantityEvent.deleted_at.is_(None),
            )
            .order_by(AnimalGroupQuantityEvent.recorded_at.desc(), AnimalGroupQuantityEvent.created_at.desc())
        )
    )


# -------------------------------------------------------------- dicts
def membership_dict(
    db: Session, m: AnimalGroupMembership, animal: Animal | None = None, group: AnimalGroup | None = None
) -> dict[str, Any]:
    group = group or load_group(db, m.animal_group_id)
    animal = animal or db.get(Animal, m.animal_id)
    return {
        "id": str(m.id),
        "group_id": str(m.animal_group_id),
        "group_name": group.name if group else None,
        "group_type": group.group_type if group else None,
        "animal_id": str(m.animal_id),
        "animal_name": animal.name if animal else None,
        "animal_tag": animal.tag if animal else None,
        "valid_from": m.valid_from,
        "valid_to": m.valid_to,
        "current": m.valid_to is None,
        "reason": m.reason_code,
        "end_reason": m.end_reason_code,
    }


def quantity_dict(e: AnimalGroupQuantityEvent) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "group_id": str(e.animal_group_id),
        "event_type": e.event_type,
        "quantity_delta": e.quantity_delta,
        "resulting_quantity": e.resulting_quantity,
        "occurred_at": e.occurred_at,
        "reason": e.reason,
        "source_type": e.source_type,
        "source_id": e.source_id,
        "recorded_at": e.recorded_at,
        "recorded_by": str(e.recorded_by) if e.recorded_by else None,
    }
