"""Milk records: the checks a milking must pass, and corrections
(OrigamiFarmOS backend/app/services/milk_service.py, PR 23).

The same rules hold for milk recorded today and for a correction made
later — a corrected record must still be milk an animal in milk could
give, outside withdrawal unless withheld, dated within her lactation and
not in the future — so both go through [ensure_milk_allowed].

A correction changes the record in place and keeps the full before/after
in the audit log (handbook: "Preserve history"): every total, chart and
analysis reads milk_record, so the corrected value is what they count,
and who changed what, from what, why and with whose approval stays on
file.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit_event
from app.common.enums import ActorType
from app.farmos import duplicates, livestock_service, livestock_state_service, reproduction_service
from app.farmos.deps import AccessContext
from app.farmos.feed_catalog import FeedError
from app.farmos.feed_inventory_service import parse_uuid
from app.farmos.livestock_catalog import Cap
from app.farmos.livestock_service import ensure_utc
from app.farmos.production_models import MilkRecord
from app.farmos.record_time import not_in_future
from app.farmos.schemas import ALLOWED_MILK_DESTINATIONS, MAX_LITERS_PER_MILKING, MILK_SESSIONS
from app.tenant_api.models import Animal

CORRECTABLE = ("animal_id", "session", "liters", "destination", "recorded_at")


class MilkError(FeedError):
    pass


def ensure_milk_allowed(db: Session, animal: Animal, recorded_at: datetime | None, destination: str) -> bool:
    """Refuses milk the animal cannot have given; returns whether she is
    under withdrawal (the milk is then only accepted as withheld)."""
    livestock_state_service.ensure_on_farm(animal, "milk")
    # Milk is a capability (generic animal model §12): a bull, a hen, a
    # meat ewe or a mare cannot have a milk record, however the form was
    # filled in. An animal with no profile on record is held to biology only.
    livestock_service.check_production_capability(
        livestock_service.capabilities_for_animal(animal),
        Cap.MILK_PRODUCTION,
        animal.name,
        configured=animal.management_profile is not None,
    )

    # Milk belongs to a lactation (PRODUCTION-SCHEMA §8): the round, the
    # days in milk and the dry-off plan all read "in milk" from the record,
    # so milk from an animal that is not being milked is refused with what
    # to do instead — never a second, silent idea of who is in milk.
    lactation = reproduction_service.active_lactation(db, animal.id)
    if lactation is None and not animal.lactating:
        raise MilkError(
            f"{animal.name} is not recorded as being milked. Start her milking first "
            "(her profile → Is she being milked? → Producing milk), then record the milk.",
            409,
        )
    if lactation is not None and recorded_at is not None:
        started, at = ensure_utc(lactation.started_at), ensure_utc(recorded_at)
        assert started is not None and at is not None
        if at < started - timedelta(days=1):
            raise MilkError(
                f"{animal.name} started this lactation on {started.date().isoformat()}; milk cannot be "
                "dated before it. If she calved earlier, correct the calving date on her profile.",
                422,
            )

    withdrawal_until = ensure_utc(animal.withdrawal_until)
    under_withdrawal = withdrawal_until is not None and withdrawal_until > datetime.now(timezone.utc)
    # RULE-WITHDRAWAL (tech spec §14): a server-side safety gate, including
    # when an offline tablet is stale.
    if under_withdrawal and destination != "withheld":
        assert withdrawal_until is not None
        raise MilkError(
            f"{animal.name} is under withdrawal until {withdrawal_until.isoformat()}. "
            "Record this milk as withheld; it must not enter stored, sold, processed or consumed stock.",
            422,
        )
    return under_withdrawal


def get_record(db: Session, record_id: str | uuid.UUID | None) -> tuple[MilkRecord, Animal]:
    """The milk record and its animal, on this farm (RLS hides the rest)."""
    pk = parse_uuid(record_id)
    record = db.get(MilkRecord, pk) if pk else None
    animal = db.get(Animal, record.animal_id) if record is not None and record.deleted_at is None else None
    if record is None or animal is None:
        raise MilkError("Milk record not found", 404)
    return record, animal


def _current(record: MilkRecord) -> dict[str, Any]:
    return {
        "animal_id": record.animal_id,
        "session": record.session,
        "liters": float(record.liters),
        "destination": record.destination,
        "recorded_at": ensure_utc(record.recorded_at),
    }


def proposed_values(record: MilkRecord, changes: dict) -> dict[str, Any]:
    """The record as it would read after [changes] (only the fields a
    correction may change; unset ones keep their value)."""
    out = _current(record)
    for k in CORRECTABLE:
        if changes.get(k) is None:
            continue
        if k == "animal_id":
            pk = parse_uuid(changes[k])
            if pk is None:
                raise MilkError("Animal not found", 404)
            out[k] = pk
        elif k == "liters":
            out[k] = float(changes[k])
        elif k == "recorded_at":
            out[k] = ensure_utc(changes[k])
        else:
            out[k] = changes[k]
    return out


def validate_correction(db: Session, record: MilkRecord, changes: dict) -> tuple[dict[str, Any], Animal]:
    """Checks a correction before it is applied or sent for approval."""
    new = proposed_values(record, changes)
    if new == _current(record):
        raise MilkError(
            "The correction changes nothing: give the right amount, session, destination, day or animal."
        )
    if new["session"] not in MILK_SESSIONS:
        raise MilkError(f"session must be one of {sorted(MILK_SESSIONS)}")
    if new["destination"] not in ALLOWED_MILK_DESTINATIONS:
        raise MilkError(f"destination must be one of {sorted(ALLOWED_MILK_DESTINATIONS)}")
    liters = float(new["liters"])
    if liters < 0:
        raise MilkError("liters cannot be negative")
    if liters > MAX_LITERS_PER_MILKING:
        raise MilkError(f"{liters:g} L is more than one animal gives in one milking — check the amount")
    animal = db.get(Animal, new["animal_id"])
    if animal is None or animal.deleted_at is not None:
        raise MilkError("Animal not found", 404)
    at = not_in_future(new["recorded_at"], "Milk")
    ensure_milk_allowed(db, animal, at, new["destination"])
    return new, animal


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def apply_correction(
    db: Session,
    control_db: Session,
    access: AccessContext,
    record: MilkRecord,
    changes: dict,
    *,
    reason: str,
    requested_by: uuid.UUID | None = None,
    approval_request_id: uuid.UUID | None = None,
) -> MilkRecord:
    """Corrects [record] in place; the before/after goes to the audit log.
    [requested_by] is the employee whose request the manager [access]
    approved, when there was one."""
    if not reason or not reason.strip():
        raise MilkError("Say why the milk record is corrected.")
    new, animal = validate_correction(db, record, changes)
    before = _current(record)
    # Moving it onto another session or day must not create a second record
    # of a milking already on file.
    if (new["session"], new["recorded_at"].date(), new["animal_id"]) != (
        before["session"],
        before["recorded_at"].date(),
        before["animal_id"],
    ):
        tz = duplicates.tenant_timezone(control_db, access.tenant_id)
        day_start, day_end = duplicates.farm_day(tz, new["recorded_at"])
        clash = db.scalar(
            select(MilkRecord).where(
                MilkRecord.animal_id == new["animal_id"],
                MilkRecord.session == new["session"],
                MilkRecord.id != record.id,
                MilkRecord.deleted_at.is_(None),
                MilkRecord.recorded_at >= day_start,
                MilkRecord.recorded_at < day_end,
            )
        )
        if clash is not None:
            raise MilkError(
                f"{animal.name}'s {new['session']} milk is already recorded for that day "
                f"({float(clash.liters):g} L); correct that record instead.",
                409,
            )
    changed: dict[str, dict[str, Any]] = {}
    for k in CORRECTABLE:
        if before[k] != new[k]:
            changed[k] = {"from": _json(before[k]), "to": _json(new[k])}
            setattr(record, k, new[k])
    record.version += 1
    record.last_modified_by = access.membership_id
    db.flush()
    record_audit_event(
        control_db,
        actor_id=access.user_id,
        actor_type=ActorType.TENANT_USER,
        actor_role=access.role,
        tenant_id=access.tenant_id,
        action="milk_record.corrected",
        entity_type="milk_record",
        entity_id=str(record.id),
        module_code="milk_production",
        summary=f"Milk record of {animal.name} corrected: {reason.strip()}"[:500],
        changes=changed,
        metadata={
            "reason": reason.strip(),
            "requested_by": str(requested_by) if requested_by else None,
            "approved_by": str(access.user_id) if requested_by else None,
            "approval_request_id": str(approval_request_id) if approval_request_id else None,
        },
    )
    return record
