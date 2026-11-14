"""Effective-dated animal state and the animal's exit from the farm
(OrigamiFarmOS database/LIVESTOCK-SCHEMA.md §5, §14; BEHAVIORAL-MODEL §14).

`animal.lifecycle_status`, `life_stage`, `management_profile` and
`location_label` are operational current values. A change to any of them
goes through [record]: validate, close the open history range, open the
new one, update the current field — in one transaction, never the current
field alone (§14 "Do not update only the current field").

An exit (sold, dead, culled, slaughtered, transferred) is explicit
(behavioural model §14): it closes the animal's group memberships, its
active lactation and pregnancy and its feeding assignments, blocks future
ordinary production and feeding, and keeps every record made before it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.farmos import feed_models as fm
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.livestock_models import AnimalStateHistory
from app.farmos.livestock_service import ensure_utc, now_utc
from app.tenant_api.models import Animal

STATUS = "STATUS"
LIFE_STAGE = "LIFE_STAGE"
MANAGEMENT_PROFILE = "MANAGEMENT_PROFILE"
LOCATION = "LOCATION"
STATE_TYPES = (STATUS, LIFE_STAGE, MANAGEMENT_PROFILE, LOCATION)

ACTIVE = "active"
ARCHIVED = "archived"
# exit type -> the quantity event a hybrid or quantity-managed group the
# animal belonged to records for it (LIVESTOCK-SCHEMA §9).
EXIT_TYPES = {
    "dead": "DEATH",
    "sold": "SALE",
    "culled": "CULL",
    "slaughtered": "SLAUGHTER",
    "transferred": "TRANSFER_OUT",
}
# A death is a fact anyone looking after the animal may record; the others
# are management decisions (see routes_livestock.exit_animal).
FACT_EXITS = frozenset({"dead"})


class LivestockError(FeedError):
    """A livestock rule refused the operation. Carries a sentence for the
    farmer; FastAPI answers it as `detail` with its status code."""


def _utc(value: datetime | None) -> datetime:
    at = ensure_utc(value)
    return at if at is not None else now_utc()


def _apply(animal: Animal, state_type: str, value: str | None) -> None:
    if state_type == STATUS:
        animal.lifecycle_status = value or ACTIVE
    elif state_type == LIFE_STAGE:
        animal.life_stage = value
    elif state_type == MANAGEMENT_PROFILE:
        animal.management_profile = value
    elif state_type == LOCATION:
        animal.location_label = value


def current(db: Session, animal_id: uuid.UUID, state_type: str) -> AnimalStateHistory | None:
    db.flush()  # a range closed in this transaction must read as closed
    return db.scalar(
        select(AnimalStateHistory)
        .where(
            AnimalStateHistory.animal_id == animal_id,
            AnimalStateHistory.state_type == state_type,
            AnimalStateHistory.effective_to.is_(None),
        )
        .order_by(AnimalStateHistory.effective_from.desc())
        .limit(1)
    )


def record(
    db: Session,
    animal: Animal,
    state_type: str,
    value: str | None,
    *,
    reference_id: str | None = None,
    at: datetime | None = None,
    reason: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    user_id: uuid.UUID | None,
) -> AnimalStateHistory | None:
    """One state change, written the §14 way. Returns the new history row,
    or None when the value did not change (an edit that re-sends the same
    stage writes nothing)."""
    if state_type not in STATE_TYPES:
        raise ValueError(f"Unknown state type {state_type}")
    at = _utc(at)
    if at > now_utc():
        raise LivestockError("A change cannot be dated in the future.")
    open_row = current(db, animal.id, state_type)
    if open_row is not None and open_row.value_code == value and open_row.value_reference_id == reference_id:
        return None
    if open_row is None and value is None and reference_id is None:
        return None
    if open_row is not None:
        since = _utc(open_row.effective_from)
        if at < since:
            raise LivestockError(
                f"{animal.name}'s {_label(state_type)} has been {open_row.value_code or 'unset'} since "
                f"{since.date().isoformat()}; a change cannot be dated before that."
            )
        open_row.effective_to = at
    row = AnimalStateHistory(
        tenant_id=animal.tenant_id,
        last_modified_by=None,
        animal_id=animal.id,
        state_type=state_type,
        value_code=value,
        value_reference_id=reference_id,
        effective_from=at,
        reason_code=reason,
        source_type=source_type,
        source_id=source_id,
        recorded_at=now_utc(),
        recorded_by=user_id,
    )
    db.add(row)
    _apply(animal, state_type, value)
    return row


def _label(state_type: str) -> str:
    return {
        STATUS: "status",
        LIFE_STAGE: "life stage",
        MANAGEMENT_PROFILE: "management profile",
        LOCATION: "location",
    }[state_type]


def open_initial(
    db: Session, animal: Animal, *, at: datetime | None = None, user_id: uuid.UUID | None
) -> None:
    """The opening rows for a newly registered animal: what it was
    registered as, from the moment it was."""
    at = _utc(at)
    for state_type, value in (
        (STATUS, animal.lifecycle_status or ACTIVE),
        (LIFE_STAGE, animal.life_stage),
        (MANAGEMENT_PROFILE, animal.management_profile),
        (LOCATION, animal.location_label),
    ):
        if value is None:
            continue
        db.add(
            AnimalStateHistory(
                tenant_id=animal.tenant_id,
                animal_id=animal.id,
                state_type=state_type,
                value_code=value,
                effective_from=at,
                reason_code="registered",
                source_type="registration",
                recorded_at=now_utc(),
                recorded_by=user_id,
            )
        )


def history(db: Session, animal_id: uuid.UUID, state_type: str | None = None) -> list[AnimalStateHistory]:
    db.flush()
    stmt = select(AnimalStateHistory).where(AnimalStateHistory.animal_id == animal_id)
    if state_type:
        stmt = stmt.where(AnimalStateHistory.state_type == state_type)
    return list(
        db.scalars(
            stmt.order_by(AnimalStateHistory.effective_from.desc(), AnimalStateHistory.recorded_at.desc())
        )
    )


def history_dict(row: AnimalStateHistory) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "state_type": row.state_type,
        "value": row.value_code,
        "reference_id": row.value_reference_id,
        "effective_from": row.effective_from,
        "effective_to": row.effective_to,
        "reason": row.reason_code,
        "source_type": row.source_type,
        "source_id": row.source_id,
        "recorded_at": row.recorded_at,
        "recorded_by": str(row.recorded_by) if row.recorded_by else None,
    }


# ------------------------------------------------------------------- exit
def exit_animal(
    db: Session,
    access: AccessContext,
    animal: Animal,
    exit_type: str,
    *,
    at: datetime | None = None,
    reason: str | None = None,
) -> Animal:
    """Records that the animal left the farm. Everything recorded about it
    stays; what can no longer be true afterwards is closed. The router
    writes the audit entry."""
    if exit_type not in EXIT_TYPES and exit_type != ARCHIVED:
        raise LivestockError(f"exit_type must be one of {sorted(EXIT_TYPES)}")
    if not animal.active:
        raise LivestockError(f"{animal.name} already left the farm ({animal.lifecycle_status}).", 409)
    at = _utc(at)
    record(db, animal, STATUS, exit_type, at=at, reason=reason, source_type="exit", user_id=access.user_id)
    animal.active = False
    animal.exited_at = at
    animal.version += 1
    animal.last_modified_by = access.membership_id
    _close_dependents(db, access, animal, exit_type, at=at)
    return animal


def _close_dependents(
    db: Session, access: AccessContext, animal: Animal, exit_type: str, *, at: datetime
) -> None:
    from app.farmos import feeding_program_service as feeding
    from app.farmos import group_service as groups
    from app.farmos import reproduction_service as repro

    groups.end_memberships_on_exit(
        db, access, animal, at=at, quantity_event=EXIT_TYPES.get(exit_type), reason=f"exit:{exit_type}"
    )
    repro.on_exit(db, animal, at=at, user_id=access.user_id)
    assignments = db.scalars(
        select(fm.FeedingAssignment).where(
            fm.FeedingAssignment.subject_type == "animal",
            fm.FeedingAssignment.subject_id == animal.id,
            fm.FeedingAssignment.status == "active",
            fm.FeedingAssignment.deleted_at.is_(None),
        )
    ).all()
    for assignment in assignments:
        feeding.end_assignment(db, access, assignment, reason=f"animal {exit_type}")


def reactivate(
    db: Session,
    access: AccessContext,
    animal: Animal,
    *,
    at: datetime | None = None,
    reason: str | None = None,
) -> Animal:
    """Only an archived animal comes back. A sale or a death is a fact; a
    mistaken one is corrected by a manager re-registering, not by flipping
    it back — the exit and everything it closed stay on the record."""
    if animal.active:
        return animal
    if animal.lifecycle_status != ARCHIVED:
        raise LivestockError(
            f"{animal.name} left the farm as {animal.lifecycle_status}; "
            "only an archived animal can be restored.",
            409,
        )
    record(
        db,
        animal,
        STATUS,
        ACTIVE,
        at=at,
        reason=reason or "restored",
        source_type="restore",
        user_id=access.user_id,
    )
    animal.active = True
    animal.exited_at = None
    animal.version += 1
    animal.last_modified_by = access.membership_id
    return animal


def ensure_on_farm(animal: Animal, action: str) -> None:
    """Behavioural model §14: an animal that left blocks ordinary
    production, feeding and reproduction afterwards."""
    if not animal.active:
        exited = ensure_utc(animal.exited_at)
        raise LivestockError(
            f"{animal.name} is no longer on the farm ({animal.lifecycle_status}"
            + (f" on {exited.date().isoformat()}" if exited else "")
            + f"); {action} cannot be recorded.",
            409,
        )
